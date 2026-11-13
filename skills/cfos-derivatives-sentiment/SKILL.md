---
name: cfos-derivatives-sentiment
description: >-
  Türev piyasa konumlanmasını okur: funding rate, open interest, 24h hacim ve
  spread üzerinden crowding ve kırılganlık değerlendirir. Yön tahmini değil,
  "kim sıkışmış" sorusunun cevabını verir. Veri yoksa nötr varsaymaz.
---

# CFOS Derivatives Sentiment

## Amaç

Spot fiyat ne söylüyorsa türev piyasa aynı şeyi söylüyor mu? Funding aşırı mı,
OI artıyor mu, kalabalık hangi tarafta?

## Ne zaman

- Trend/breakout tezini onaylamadan önce.
- Funding carry veya liquidation reversal öncesi.
- Fiyat yatayken OI artıyorsa (yaklaşan hareket).
- "Long mu short mu kalabalık?" sorusu.

## Girdi

```bash
cfos market BTC/USDT:USDT --json
cfos regime BTC/USDT:USDT --tf 4h --json
```

`cfos market` şunları döndürür: `funding_rate`, `open_interest`,
`quote_volume_24h`, `spread_bps`. Tarihsel funding/OI serisi v1'de yoktur;
varsa agent doğrulanmış bir kaynaktan çeker ve kaynağı yazar, yoksa
`INSUFFICIENT_HISTORY` der.

## Yorum tablosu

| Gözlem | Etiket | Trade anlamı |
|---|---|---|
| funding ≥ +0.05% | crowded_long | Long chase riskli; squeeze aşağı |
| funding ≤ -0.05% | crowded_short | Short chase riskli; squeeze yukarı |
| funding ±0.01% içinde | neutral | Crowding sinyali yok |
| OI ↑ + fiyat ↑ | fresh longs | Trend sağlıklı olabilir |
| OI ↑ + fiyat ↓ | fresh shorts | Düşüş sağlıklı olabilir |
| OI ↑ + fiyat yatay | coiled | Kırılım yaklaşıyor |
| OI ↓ + fiyat ↑ | short covering | Trend kalitesi düşük |
| OI ↓ + fiyat ↓ | long liquidation | Satış tükeniyor olabilir |
| spread > 8 bps | illiquid | Emir kalitesi düşük |

## Karar kuralları

1. Funding aşırı + trend yönü aynıysa "geç kaldın" uyarısı ver; yeni giriş için
   geri çekilme bekle.
2. OI düşerken fiyat yükseliyorsa breakout tezini zayıflat.
3. Funding `None` ise `unknown`; asla `neutral` yazma.
4. Tek başına funding ile ters işlem açma; fiyat teyidi (`cfos-regime`,
   `cfos-liquidation-reversal`) şart.
5. Hacim < 50M USD ise bu skill'in çıktısı "düşük güven" damgası taşır.

## Çıktı sözleşmesi

```json
{
  "symbol": "BTC/USDT:USDT",
  "funding_rate": 0.00031,
  "funding_bias": "neutral",
  "open_interest": 812345.6,
  "oi_trend": "unknown",
  "crowding": "none",
  "confidence": "medium",
  "notes": ["funding neutral; no squeeze fuel"]
}
```

## Guardrails

- Funding tahmini üretme; yalnızca venue'nun döndürdüğü değeri kullan.
- OI tek snapshot ise "artıyor/azalıyor" deme; `unknown` bırak.
- Bu skill emir açmaz; en fazla zinciri `cfos-sizing`e devreder.
