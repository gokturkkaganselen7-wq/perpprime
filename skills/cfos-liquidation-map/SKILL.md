---
name: cfos-liquidation-map
description: >-
  Likidasyon kümelerini ve stop-sweep riskini haritalar. Kaldıraçlı piyasada
  fiyatın nereye "çekildiğini" tahmin eder: yoğun likidasyon bantları, son
  wick'ler, funding baskısı. Kesin likidasyon verisi yoksa uydurmaz; gözleme
  dayalı bant üretir ve güven seviyesini düşürür.
---

# CFOS Liquidation Map

## Amaç

Fiyatın likidite avlamak için nereye gidebileceğini haritalamak. Amaç kesin
seviye vermek değil, "bu seviyenin altında/üstünde stoplar var mı?" sorusunu
cevaplamak.

## Ne zaman

- Liquidation reversal setup'ı öncesi.
- Stop yerleştirirken "stop avına açık mıyım?" kontrolü.
- Ani wick sonrası (→ `cfos-liquidation-reversal`).
- Funding aşırı olduğunda (→ `cfos-derivatives-sentiment`).

## Girdi

```bash
cfos market BTC/USDT:USDT --json
cfos candles BTC/USDT:USDT --tf 1h --limit 200 --json
cfos candles BTC/USDT:USDT --tf 4h --limit 100 --json
cfos regime BTC/USDT:USDT --tf 4h --json
```

## Haritalama yöntemi

1. Son 200 1h mumunda en büyük hacimli wick'leri işaretle (sweep izleri).
2. 4h mumlarda eşit yüksek/dip (equal highs/lows) kümelerini bul.
3. Bu seviyelerin 0.3-1.0 ATR dışını "likidite bandı" olarak işaretle.
4. Funding yönü kalabalığı söyler; kalabalığın stop tarafı baskın banttır.
5. ATR% ≥ 6 ise harita güvenilir değil; `chaos` rejiminde işlem yok.

## Bant kalitesi

| Kanıt | Güven |
|---|---|
| Eşit dip + yüksek hacim wick + crowded funding | yüksek |
| Eşit dip + normal hacim | orta |
| Tek wick, düşük hacim | düşük |
| Veri < 100 mum | yok (INSUFFICIENT_DATA) |

## Karar kuralları

1. Stop, likidite bandının **içine** konmaz; bandın dışına ve ATR payı eklenir.
2. Long stop, eşit dip bandının hemen altında; short stop eşit yüksek üstünde.
3. Yakın bant yoksa stop yapısal seviyeye (son swing) konur, keyfi yüzdeye değil.
4. Fiyat bandı test edip geri döndüyse ("sweep + reclaim") bu bir sinyaldir;
   geri dönmediyse trend devam ediyor demektir.
5. Harita "kesin likidasyon miktarı" iddiası taşımaz; venue verisi olmadan
   yalnızca bant/güven etiketi verilir.

## Çıktı sözleşmesi

```json
{
  "symbol": "BTC/USDT:USDT",
  "price": 65013.0,
  "atr_pct": 2.4,
  "bands": [
    {"side": "below", "low": 63200, "high": 63800, "evidence": "equal lows + wick", "confidence": "high"},
    {"side": "above", "low": 66800, "high": 67400, "evidence": "equal highs", "confidence": "medium"}
  ],
  "sweep_risk": "stops below 63,800 are exposed",
  "data_ok": true
}
```

## Guardrails

- "Likidasyon haritası" adı altında kesin fiyat hedefi verme.
- Veri yetersizse bant üretme; `INSUFFICIENT_DATA`.
- Bu skill tek başına emir açmaz; giriş/stop için `cfos-sizing`e devreder.
