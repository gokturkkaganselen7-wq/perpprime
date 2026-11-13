---
name: cfos-regime
description: >-
  Piyasa rejimini deterministik olarak sınıflandırır: trend_up, trend_down,
  range, squeeze veya chaos. EMA20/50/200 yapısı, ATR yüzdesi, 20 mumluk
  aralık ve funding crowding overlay'i kullanır. Çıktı etiket + onu üreten
  sayılardır; yorum değil kanıt verir.
---

# CFOS Regime

## Amaç

Hangi setup'ın geçerli olduğunu belirleyen tek üst karar: trend mi, aralık mı,
sıkışma mı, kaos mu? Her trade skill'i önce bu etiketi okur.

## Ne zaman

- Her trade zincirinin ilk adımı.
- "Piyasa nasıl?" sorusu.
- Grid / mean-reversion / breakout seçimi öncesi.
- Rejim değişimi şüphesinde (ATR sıçraması, range genişlemesi).

## Girdi

```bash
cfos regime BTC/USDT:USDT --tf 4h --limit 200 --json
cfos market BTC/USDT:USDT --json
```

`cfos regime` gerçek OHLCV ister; public CCXT endpoint'i kullanır, API key
gerektirmez. Paper modda da çalışır.

## Sınıflandırma mantığı

| Etiket | Koşul (yaklaşık) | Anlam |
|---|---|---|
| `chaos` | ATR% ≥ 6 | Stop genişlet veya dur; kaldıraç düşman |
| `squeeze` | 20-mum aralığı ≤ %2.5 ve ATR% ≤ 2 | Enerji birikiyor; kırılım yakın |
| `trend_up` | price > EMA50 > EMA200 ve EMA20 > EMA50 | Alıcı kontrolü |
| `trend_down` | price < EMA50 < EMA200 ve EMA20 < EMA50 | Satıcı kontrolü |
| `range` | diğer | Ortalamaya dönüş ortamı |

Funding overlay: `≥ +0.05%` crowded_long, `≤ -0.05%` crowded_short.

## Yorum kuralları

1. `chaos` → hiçbir yeni setup önerilmez; sadece mevcut pozisyon korunur.
2. `squeeze` + crowded_long → aşağı süpürme riski; long breakout'ta bekle.
3. `squeeze` + crowded_short → yukarı squeeze riski; short breakout'ta bekle.
4. `trend_up` + crowded_long → trend gerçek ama geç kovalamak yasak.
5. `range` → breakout ve trend skill'leri devre dışı; grid/mean-reversion.
6. Etiket ile kullanıcının tezi çelişiyorsa etiket kazanır; kanıtı göster.

## Çıktı sözleşmesi

```json
{
  "symbol": "BTC/USDT:USDT",
  "timeframe": "4h",
  "regime": "trend_up",
  "trend": "up",
  "price": 65013.0,
  "ema20": 64210.5,
  "ema50": 63100.2,
  "ema200": 59800.7,
  "atr_pct": 2.4,
  "range_pct": 6.1,
  "funding_bias": "neutral",
  "notes": []
}
```

## Guardrails

- 60 mumdan az veriyle çağırma; `cfos regime` hata verir.
- Farklı timeframe etiketlerini birleştirip "kesin yön" üretme; çelişki varsa
  yüksek timeframe baskındır.
- Rejim etiketi bir emir değildir; sizing ve execution olmadan işlem yok.
