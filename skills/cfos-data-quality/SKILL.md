---
name: cfos-data-quality
description: >-
  Veri tazeliği ve tutarlılığını denetler: market snapshot yaşı, spread,
  hacim, OHLCV boşlukları, funding/OI eksikliği, sembol çözümlemesi ve saat
  kayması. Veri yoksa sayı üretmez; INSUFFICIENT_DATA döndürür.
---

# CFOS Data Quality

## Amaç

Kötü veriyle alınan iyi karar yoktur. Bu skill, herhangi bir analizden önce
verinin kullanılabilir olduğunu doğrular.

## Ne zaman

- `cfos market` veya `cfos regime` beklenmedik değer döndürdüğünde.
- spread WARN/BLOCK aldığında.
- Yeni venue veya sembol eklendiğinde.
- `cfos doctor` FAIL/WARN verdiğinde.

## Girdi

```bash
cfos doctor --json
cfos market BTC/USDT:USDT --json
cfos candles BTC/USDT:USDT --tf 1h --limit 200 --json
cfos regime BTC/USDT:USDT --tf 4h --json
```

## Kontrol listesi

| Kontrol | Geçer | Reddet |
|---|---|---|
| Snapshot yaşı | ≤ 120 sn | > 120 sn veya `ts` yok |
| Fiyat | > 0, mantıklı aralık | 0, negatif, on kat sapma |
| Spread | ≤ 8 bps | > 16 bps (8-16 WARN) |
| 24h quote hacim | ≥ 50M USD | < 50M USD |
| OHLCV | ≥ 60 mum, boşluk yok | eksik mum, tekrar eden timestamp |
| Funding | makul aralık (-%1…+%1) | aşırı uç veya sürekli None |
| OI | > 0 veya bilinçli None | sessizce 0 kabul etme |
| Saat | UTC, drift < 5 sn | geçmiş/gelecek timestamp |

## Karar kuralları

1. Fiyat 0 veya negatifse hiçbir hesap yapılmaz.
2. Spread > 16 bps ise emir önerilmez; 8-16 bps ise yalnızca limit emir.
3. OHLCV < 60 mum ise `cfos regime` çağrılmaz; `cfos candles --limit` artırılır.
4. Funding `None` ise "nötr" varsayılmaz; `unknown` olarak raporlanır.
5. İki kaynak (market vs candles son kapanış) %1'den fazla saparsa uyarı ver.

## Çıktı sözleşmesi

```json
{
  "data_ok": false,
  "reasons": ["spread 19.2 bps > 16 bps", "OHLCV 42 candles < 60"],
  "usable_for": ["price_display"],
  "blocked_for": ["regime", "sizing", "execution"]
}
```

## Guardrails

- Eksik veriyi komşu mumdan, geçmişten veya "tahmini" değerden doldurma.
- Aynı sembol için farklı timeframe'leri karıştırıp tek seri gibi gösterme.
- `data_ok=false` iken downstream skill'lere sayı geçirme.
