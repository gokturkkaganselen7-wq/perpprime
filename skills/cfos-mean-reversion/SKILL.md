---
name: cfos-mean-reversion
description: >-
  Aşırı uzanmış fiyata karşı ortalama dönüş tezi üretir: RSI/ATR uzanımı,
  bant dışı sapma, hacim tükenmesi ve dönüş tetikleyicisi. Rejim kırılımı
  varsa fade yapmaz; trende karşı işlem açmaz.
---

# CFOS Mean Reversion

## Amaç

Fiyat ortalamadan ne kadar uzaklaştı ve dönüş için kanıt var mı? "Düştü, alayım"
değil; ölçülmüş uzanım + tükeniş + tetikleyici.

## Ne zaman

- `cfos regime` = `range` veya `squeeze`.
- Fiyat EMA20'den ≥ 2 ATR uzakta.
- Hacim düşerek momentum tükeniyor.
- Kullanıcı "fade / ortalamaya dönüş" dediğinde.

Kullanma: `trend_up`/`trend_down` rejimlerinde (trende karşı fade yasak) ve
`chaos`ta.

## Girdi

```bash
cfos regime BTC/USDT:USDT --tf 4h --json
cfos candles BTC/USDT:USDT --tf 1h --limit 120 --json
cfos market BTC/USDT:USDT --json
```

## Uzanım ölçütleri

| Ölçüt | Aşırı kabul | Not |
|---|---|---|
| EMA20 sapması | ≥ 2.0 ATR | 3+ ATR'de bile trend olabilir; rejim şart |
| Son 5 mum getirisi | ≥ 2x ATR% | dikey hareket |
| Hacim | Azalan | tükeniş lehine |
| Funding | Aşırı, fade yönünün karşısında | squeeze yakıtı |
| Range konumu | Son 20 mumun dış %10'u | istatistiksel uç |

## Setup kuralları

1. Giriş: dönüş tetikleyicisi olmadan girme. Tetikleyici = 1h kapanışın son
   swing'e dönmesi veya RSI'nin 30/70 sınırını terk etmesi.
2. Stop: uzanımın extreme'inin 1 ATR ötesi. "Ucuz" diye stop'suz giriş yasak.
3. TP1: EMA20. TP2: EMA50 veya range orta bandı.
4. R:R: TP1'e göre ≥ 2.0; değilse işlem yok.
5. Geçersizlik: 1h kapanış yeni extreme yaparsa tez ölür, ekleme yapılmaz.

## Karar kuralları

1. Trend rejiminde fade sinyali üretilmez; "trend devam" notu düşülür.
2. Uzanım 3 ATR üzerindeyse "aşırı ama tehlikeli" işaretlenir; risk yarıya iner.
3. Funding fade yönünde aşırıysa (kalabalık zaten fade'de) işlem kalitesi düşer.
4. İlk hedefe mesafe stop mesafesinin 2 katından azsa plan reddedilir.
5. Aynı sembolde trend pozisyonu varsa fade açılmaz (netleştirme yok).

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-mean-reversion",
  "verdict": "READY_FOR_PLAN",
  "symbol": "BTC/USDT:USDT",
  "side": "short",
  "entry": 67400,
  "stop": 68250,
  "take_profits": [65800, 64900],
  "rr": 2.0,
  "deviation_atr": 2.3,
  "invalidation": "1h close above 68,250",
  "notes": ["range regime", "volume declining into high"]
}
```

## Guardrails

- Trende karşı ortalama dönüş, en pahalı hatalardan biridir; rejim kapısı şart.
- Stop'u "nasılsa döner" diye genişletme.
- Bu skill plan üretir; emir için `cfos-sizing` + `cfos-execution`.
