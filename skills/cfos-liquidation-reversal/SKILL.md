---
name: cfos-liquidation-reversal
description: >-
  Likidasyon kaskadı sonrası wick dönüşünü değerlendirir: aşırı hacimli fitil,
  hızlı geri alım, funding tarafı ve reclaim teyidi. Kaskadın içine düşen
  bıçağı tutmaz; yalnızca reclaim sonrası giriş arar.
---

# CFOS Liquidation Reversal

## Amaç

Zorunlu satışların (veya short squeeze'in) yarattığı fitili fırsata çevirmek.
Kural: **önce reclaim, sonra giriş.**

## Ne zaman

- Ani %3+ fitil + yüksek hacim.
- `cfos-liquidation-map` bir bandın süpürüldüğünü gösteriyor.
- Funding aşırı ve kaskad yönünde.
- Kullanıcı "likidasyon oldu, döner mi?" dediğinde.

Kullanma: yavaş trend düşüşünde (kaskad değilse) ve `chaos` rejiminde.

## Girdi

```bash
cfos candles BTC/USDT:USDT --tf 15m --limit 96 --json
cfos candles BTC/USDT:USDT --tf 1h --limit 120 --json
cfos market BTC/USDT:USDT --json
cfos regime BTC/USDT:USDT --tf 4h --json
```

## Kaskad kalite kontrolü

| Kontrol | Geçer | Reddet |
|---|---|---|
| Fitil boyu | ≥ 1.5 ATR | < 1 ATR |
| Hacim | 20-mum ortalamasının ≥ 2x | < 1.5x |
| Geri alım hızı | 3 mum içinde seviye üstü | sürünerek |
| Reclaim | Fitil seviyesinin üstünde kapanış | altında |
| Funding | Kaskad yönünde aşırı | nötr |
| OI | Kaskadla azalmış | artıyor (yeni satıcı) |

## Setup kuralları

1. Giriş: reclaim kapanışı veya reclaim sonrası ilk geri çekilme.
2. Stop: fitilin extreme'inin 0.5 ATR ötesi.
3. TP1: kaskad öncesi son swing / VWAP.
4. TP2: bir üst yapısal seviye.
5. Geçersizlik: fiyat fitil dibinin altına inerse kaskad devam ediyordur.

## Karar kuralları

1. Reclaim yoksa işlem yok; "düştü, ucuz" gerekçe değildir.
2. Kaskad sırasında emir gönderme; spread ve kayma felaket olur.
3. İlk 15 dakika geçmeden market emir yok.
4. Fitil destekli hacim ortalamanın altındaysa bu kaskad değil, sızıntıdır;
   `NO_TRADE`.
5. Reclaim sonrası R:R < 2.0 ise plan reddedilir.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-liquidation-reversal",
  "verdict": "READY_FOR_PLAN",
  "symbol": "BTC/USDT:USDT",
  "side": "long",
  "entry": 64200,
  "stop": 62800,
  "take_profits": [66200, 67500],
  "rr": 2.0,
  "wick_low": 63100,
  "reclaim_confirmed": true,
  "invalidation": "15m close below 63,100",
  "notes": ["2.4x volume wick", "reclaimed in 3 bars"]
}
```

## Guardrails

- Kaskadın içinde alım yapma; bıçak tutulmaz.
- Stop, fitilin dibine değil, dibin dışına konur; stop avına açık bırakma.
- Bu skill plan üretir; emir için `cfos-sizing` + `cfos-execution`.
