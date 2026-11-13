---
name: cfos-trend
description: >-
  Trend devamı tezi üretir: yüksek timeframe bias, EMA yapısı, geri çekilme
  kalitesi ve tetikleyici mum. Yalnızca rejim trend_up/trend_down iken çalışır.
  Giriş, stop, TP ve geçersizlik seviyesini sayısal olarak verir.
---

# CFOS Trend

## Amaç

"Trend var, nereden ve nerede yanlış olur?" sorusunu yanıtlamak. Trendin
ortasında kovalamak değil, sağlıklı geri çekilmede katılmak.

## Ne zaman

- `cfos regime` = `trend_up` veya `trend_down`.
- Fiyat EMA20/EMA50'ye geri çekildiğinde.
- Kullanıcı "trend yönünde işlem" dediğinde.

Kullanma: `range`, `squeeze`, `chaos` rejimlerinde (→ ilgili skill).

## Girdi

```bash
cfos regime BTC/USDT:USDT --tf 4h --json
cfos candles BTC/USDT:USDT --tf 1h --limit 120 --json
cfos market BTC/USDT:USDT --json
```

## Tez kontrol listesi

1. 4h rejim trend_up/trend_down mı? Değilse dur.
2. Fiyat EMA20 ile EMA50 arasında mı (sağlıklı pullback), yoksa EMA20'den
   %5+ uzakta mı (geç giriş)?
3. Geri çekilme hacmi düşük mü? Yükseliş trendinde düşen hacim iyidir.
4. Tetikleyici: 1h kapanışın EMA20 üstüne dönmesi veya önceki swing high kırılımı.
5. Funding aşırı mı (→ `cfos-derivatives-sentiment`)? Aşırıysa risk yarıya iner.

## Setup kuralları

| Alan | Kural |
|---|---|
| Giriş | Tetikleyici kapanışın hemen üstü/altı; market veya limit |
| Stop | Son swing + 0.5 ATR; likidite bandının içine değil |
| TP1 | Son swing yüksek/dip veya 2R |
| TP2 | Ölçülen hareket (range yüksekliği) |
| R:R | İlk TP'ye göre ≥ 2.0 |
| Geçersizlik | 4h kapanış EMA50'nin karşı tarafında |

## Karar kuralları

1. Giriş EMA20'den > 1.5 ATR uzaksa "geç" işaretle; geri çekilme bekle.
2. Stop mesafesi > 2.5 ATR ise pozisyon küçült veya setup'ı atla.
3. Karşı trend sinyali (1h) varsa yalnızca limit giriş, market yok.
4. R:R < 2.0 ise `cfos plan` çağırma; TP/stop'u yeniden çalış.
5. `chaos` veya funding aşırı uçta ise tez `NO_TRADE`.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-trend",
  "verdict": "READY_FOR_PLAN",
  "symbol": "BTC/USDT:USDT",
  "side": "long",
  "entry": 65000,
  "stop": 64000,
  "take_profits": [67000, 68500],
  "rr": 2.0,
  "invalidation": "4h close below EMA50 (63,100)",
  "confidence": "medium",
  "notes": ["pullback to EMA20 on falling volume"]
}
```

## Guardrails

- Rejim trend değilken trend işlemi önerme.
- Stop'u "yuvarlak sayı" diye likidite bandına koyma.
- Bu skill plan üretir; emir için `cfos-sizing` + `cfos-execution`.
