---
name: cfos-backtest
description: >-
  Bir kural setini tarihsel veri üzerinde test eder: giriş/çıkış kuralları,
  ücretler, kayma, stop/TP, örneklem büyüklüğü ve sonuç metrikleri. CFOS v1
  yerleşik backtest motoru içermez; bu skill ancak yeniden üretilebilir bir
  script ve gerçek OHLCV varsa sayı üretir.
---

# CFOS Backtest

## Amaç

"Bu kural geçmişte çalıştı mı?" sorusuna, overfit etmeden ve maliyetleri
sayarak cevap vermek.

## Dürüstlük notu

CFOS v1'de backtest motoru **yoktur**. Bu skill:

- Kural setini ve veri aralığını netleştirir.
- `cfos candles --json` ile gerçek OHLCV çeker.
- Yeniden üretilebilir bir Python script'i yazdırır/çalıştırır.
- Sonuçları raporlar.

Script yoksa veya veri eksikse `INSUFFICIENT_TOOLING` / `INSUFFICIENT_DATA`
döndürür. "Kafadan" backtest sonucu yasaktır.

## Girdi

```bash
cfos candles BTC/USDT:USDT --tf 4h --limit 1000 --json > candles.json
cfos regime BTC/USDT:USDT --tf 4h --json
```

Script şu girdileri almalı: `candles.json`, sembol, timeframe, ücret (bps),
kayma (bps), risk yüzdesi, stop/TP kuralları, başlangıç equity.

## Minimum test standardı

| Alan | Zorunlu |
|---|---|
| Örneklem | ≥ 100 işlem (yoksa "yetersiz" damgası) |
| Veri aralığı | ≥ 2 farklı rejim (trend + range) |
| Ücret | Taker %0.055 varsayımı |
| Kayma | En az 2 bps |
| Stop/TP | Bar içi tetikleme mantığı açık |
| Look-ahead | Kapanıştan sonra karar, sonraki bar fill |
| Sonuç | Win rate, expectancy, profit factor, max DD, ortalama R |

## Karar kuralları

1. 100 işlemden azsa sonuç "kanıt değil, gözlem"dir.
2. Profit factor < 1.2 veya expectancy ≤ 0 ise strateji reddedilir.
3. Max drawdown, canlı limitin (%10) üstündeyse strateji canlıya çıkamaz.
4. Ücret/kayma olmadan pozitif, maliyetle negatifse strateji yoktur.
5. Parametre sayısı arttıkça overfit riski artar; her ek parametre için
   `cfos-walk-forward` şartı sertleşir.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-backtest",
  "symbol": "BTC/USDT:USDT",
  "timeframe": "4h",
  "bars": 1000,
  "trades": 142,
  "win_rate": 41.5,
  "expectancy_pct": 0.18,
  "profit_factor": 1.63,
  "max_drawdown_pct": 7.4,
  "avg_r": 0.31,
  "verdict": "PASS_TO_WALK_FORWARD",
  "caveats": ["tek rejim ağırlıklı", "1000 bar sınırı"]
}
```

## Guardrails

- Gelecekteki mumu kullanma (look-ahead) — en yaygın sahtekârlık.
- Aynı veride hem optimize hem test etme; o `cfos-walk-forward` işidir.
- Sonucu tek bir metrikle özetleme; DD ve örneklem birlikte raporlanır.
- Bu skill canlı emir açmaz.
