---
name: cfos-breakout
description: >-
  Sıkışma ve aralık kırılımı tezi üretir: daralan aralık, hacim patlaması,
  kapanışın seviye dışında olması ve retest kalitesi. Rejim squeeze veya
  range iken çalışır; trend ortasında geç kovalamayı reddeder.
---

# CFOS Breakout

## Amaç

Enerji birikimi sonrası ilk gerçek kırılımı yakalamak; fakeout ile gerçek
kırılımı ayırmak.

## Ne zaman

- `cfos regime` = `squeeze` veya `range`.
- 20+ mumdur daralan yüksek/dip yapısı.
- Hacim ortalamanın belirgin üstüne çıktığında.
- Kullanıcı "kırılım" dediğinde.

Kullanma: `chaos` rejiminde veya funding aşırı uçtayken.

## Girdi

```bash
cfos regime BTC/USDT:USDT --tf 4h --json
cfos candles BTC/USDT:USDT --tf 1h --limit 120 --json
cfos candles BTC/USDT:USDT --tf 15m --limit 96 --json
cfos market BTC/USDT:USDT --json
```

## Kırılım kalite kontrolü

| Kontrol | Geçer | Şüpheli |
|---|---|---|
| Aralık genişliği | Son 20 mum ≤ %2.5 | > %4 |
| Kırılım kapanışı | Seviye dışında kapanış | Sadece wick |
| Hacim | 20-mum ortalamasının ≥ 1.5x | < 1.2x |
| Retest | Seviye üstünde tutunma | Hemen geri içeri |
| Funding | Nötr / karşı taraf sakin | Kırılım yönünde aşırı |
| Spread | ≤ 8 bps | > 16 bps |

## Setup kuralları

1. Giriş: kırılım kapanışı veya retest; chase için market emir yalnızca güçlü
   hacim + nötr funding varsa.
2. Stop: aralığın karşı kenarı veya kırılım mumunun yarısı; hangisi daha yakın
   ve yapısal ise o.
3. TP1: ölçülen hareket (aralık yüksekliği) → genelde 2R.
4. TP2: bir üst zaman dilimi direnci.
5. Geçersizlik: 1h kapanış aralığın içine dönerse tez ölür.

## Karar kuralları

1. Hacimsiz kırılım = fakeout; işlem yok.
2. Kırılım sonrası ilk geri çekilme seviyeyi korumuyorsa (retest fail) çık.
3. Funding kırılım yönünde ≥ 0.05% ise squeeze riski; risk yarıya iner veya
   işlem atlanır.
4. Girişin stop'a uzaklığı > 2 ATR ise geç kalınmıştır; retest bekle.
5. 4h ATR% ≥ 6 ise kırılım güvenilmez; `NO_TRADE`.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-breakout",
  "verdict": "READY_FOR_PLAN",
  "symbol": "BTC/USDT:USDT",
  "side": "long",
  "entry": 66200,
  "stop": 64800,
  "take_profits": [69000, 70500],
  "rr": 2.0,
  "volume_confirmation": true,
  "invalidation": "1h close back below 65,500",
  "notes": ["20-bar squeeze 2.1%", "volume 1.9x average"]
}
```

## Guardrails

- Aralık dışına çıkmamış "hazırlık" aşamasında emir açma.
- Stop'u aralık ortasına koyma; yapısal kenar kullan.
- Bu skill plan üretir; emir için `cfos-sizing` + `cfos-execution`.
