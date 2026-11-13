---
name: cfos-grid
description: >-
  Aralık piyasasında grid planı üretir: üst/alt sınır, seviye sayısı, adet
  dağılımı, toplam maruziyet ve rejim kırılımında kapatma kuralı. Trend ve
  chaos rejiminde grid açmaz.
---

# CFOS Grid

## Amaç

Yatay piyasada dalgalanmayı toplamak. Grid, yönlü işlemden farklı bir risk
profilidir: sınır dışına çıkıldığında zarar birikir. Bu yüzden rejim kapısı
ve toplam maruziyet tavanı zorunludur.

## Ne zaman

- `cfos regime` = `range` (tercihen `squeeze` değil; sıkışmada kırılım riski).
- 20+ mumdur net üst/alt sınır var.
- Kullanıcı "grid / aralık" dediğinde.

Kullanma: `trend_up`, `trend_down`, `chaos` rejimlerinde.

## Girdi

```bash
cfos regime BTC/USDT:USDT --tf 4h --json
cfos candles BTC/USDT:USDT --tf 4h --limit 120 --json
cfos size --symbol BTC/USDT:USDT --side long --entry 63000 --stop 61000 --risk-pct 0.25 --json
cfos status --json
```

## Grid tasarım kuralları

1. Sınırlar: son 20-30 mumun belirgin destek/direnci; keyfi yüzde değil.
2. Seviye sayısı: 5-9. Fazlası ücret ve karmaşıklık üretir.
3. Her seviye adedi eşit; alt seviyelerde daha büyük adet v1'de yok.
4. Toplam notional ≤ 1x equity; toplam açık risk ≤ `max_portfolio_heat_pct`.
5. Stop: grid alt sınırının 1 ATR altı. **Grid stop'suz olmaz.**
6. Rejim kırılımı: 4h kapanış sınır dışında → tüm grid kapatılır.

## Örnek dağılım (10,000 equity, 62,000-68,000 aralığı)

| Seviye | Fiyat | Taraf | Notional |
|---|---|---|---|
| 1 | 62,400 | buy | 1,200 |
| 2 | 63,600 | buy | 1,200 |
| 3 | 64,800 | buy | 1,200 |
| 4 | 66,000 | sell | 1,200 |
| 5 | 67,200 | sell | 1,200 |

Toplam notional 6,000 = 0.6x equity. Marj ≈ 2,000 (3x üst sınırla), gerçek
kullanım çok daha düşük olmalı.

## Karar kuralları

1. Rejim range değilse grid yok.
2. Aralık genişliği < 2 ATR ise seviye başına kâr ücretleri karşılamaz; işlem yok.
3. Funding aşırı uçtaysa grid tek yöne sıkışabilir; risk yarıya iner.
4. Grid asla "ortalama düşürme" için sınırsız ekleme yapmaz; toplam maruziyet
   baştan sabittir.
5. Kırılım sonrası grid canlandırılmaz; yeni rejim analizi gerekir.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-grid",
  "verdict": "READY_FOR_MANUAL_GRID",
  "symbol": "BTC/USDT:USDT",
  "range": [62000, 68000],
  "levels": 5,
  "notional_total": 6000,
  "stop": 60800,
  "max_heat_pct": 1.5,
  "kill_rule": "4h close outside 62,000-68,000",
  "notes": ["range regime 26 bars", "ATR 2.1%"]
}
```

## Guardrails

- Grid emirleri v1'de tek tek `cfos plan`/`cfos execute` ile kurulur; toplu
  grid motoru yoktur. Bu skill plan üretir, emir göndermez.
- Aynı sembolde başka pozisyon varken grid açılmaz (`max_per_symbol=1`).
- Stop'suz grid kesinlikle yasaktır.
