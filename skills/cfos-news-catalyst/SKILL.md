---
name: cfos-news-catalyst
description: >-
  Haber ve katalizörleri fiyat tepkisi üzerinden değerlendirir: olayın türü,
  ilk tepki, tepkinin kalitesi (hacim, kapanış, devamlılık) ve "haber satıldı mı"
  sorusu. Haber yönünü tahmin etmez; piyasanın ne yaptığını ölçer.
---

# CFOS News Catalyst

## Amaç

Haberin kendisi değil, piyasanın habere verdiği tepki işlem edilir. Bu skill
"iyi haber = al" demez; "iyi habere rağmen yükselmiyorsa zayıflık var" der.

## Ne zaman

- Ani hareketin nedeni bilinmiyorsa.
- CPI/FOMC/NFP sonrası (→ önce `cfos-macro-calendar`).
- Listing, hack, regülasyon, ETF, büyük transfer haberi.
- Kullanıcı "bu habere ne olur?" dediğinde.

## Girdi

1. Haber kaynağı: doğrulanmış, tarihli, birincil kaynak tercih edilir; agent
   kaynağı ve yayın saatini yazar. Doğrulanamıyorsa `INSUFFICIENT_DATA`.
2. Fiyat tepkisi:

```bash
cfos candles BTC/USDT:USDT --tf 15m --limit 96 --json
cfos candles BTC/USDT:USDT --tf 1h --limit 120 --json
cfos market BTC/USDT:USDT --json
cfos regime BTC/USDT:USDT --tf 4h --json
```

## Tepki kalitesi matrisi

| Haber | Fiyat tepkisi | Yorum |
|---|---|---|
| İyi | Hacimli yükseliş + kapanış tepede | Gerçek talep |
| İyi | Yükseliş + geri veriyor | Satış baskısı / "sold the news" |
| İyi | Yatay/düşüş | Zayıflık; long tezi zayıf |
| Kötü | Hacimli düşüş + kapanış dipte | Gerçek satış |
| Kötü | Düşüş + hızlı reclaim | Absorption; short tezi zayıf |
| Kötü | Yatay/yükseliş | Güç; short tezi zayıf |

## Karar kuralları

1. İlk 15 dakika işlem yok; spread ve wick değerlendirmesi beklenir.
2. Haber sonrası giriş ancak yapısal seviye + hacim teyidi varsa.
3. "Söylenti al, haber sat" durumunda mevcut pozisyon varsa kâr alınır, yeni
   pozisyon açılmaz.
4. Aynı haberden iki kez işlem çıkarılmaz; ikinci tepki genelde zayıftır.
5. Kaynak doğrulanamıyorsa yorum yapılmaz.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-news-catalyst",
  "event": "ETF approval headline",
  "source": "SEC press release",
  "published_at": "2026-11-14T14:00:00Z",
  "reaction": "pump_then_fade",
  "volume_confirmation": true,
  "verdict": "NO_TRADE",
  "notes": ["first 15m volume 4x, close back inside range", "sold the news"]
}
```

## Guardrails

- Haber metnini uydurma; kaynak yoksa analiz yok.
- Fiyat tepkisi ölçülmeden yön iddiası kurma.
- Bu skill emir açmaz; teyit varsa `cfos-sizing`e devreder.
