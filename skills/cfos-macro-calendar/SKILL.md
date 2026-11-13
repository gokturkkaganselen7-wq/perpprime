---
name: cfos-macro-calendar
description: >-
  Makro olay takvimini ve event riskini yönetir: CPI, FOMC, NFP, GDP, PCE,
  merkez bankası kararları. Olay öncesi/sonrası pozisyon kurallarını belirler.
  Takvim doğrulanmamışsa tarih uydurmaz; INSUFFICIENT_DATA döndürür.
---

# CFOS Macro Calendar

## Amaç

Kripto 7/24 işlem görür ama makro saatlerde şiddetle hareket eder. Bu skill
"bu hafta hangi saatte ne var ve pozisyon ne olmalı?" sorusunu yanıtlar.

## Ne zaman

- Hafta başı planlamasında.
- CPI/FOMC/NFP öncesi 48 saatte.
- Kullanıcı "haber var mı?" dediğinde.
- Ani volatilite sonrası nedeni açıklarken.

## Girdi

CFOS'ta yerleşik takvim yoktur. Agent, güvenilir resmi kaynağı (örn. BLS,
Federal Reserve, ECB) `webfetch`/`websearch` ile doğrular ve **kaynak + tarih +
saat dilimi** yazar. Doğrulanamıyorsa:

```json
{"data_ok": false, "reason": "no verified calendar source"}
```

Piyasa fiyatı için:

```bash
cfos market BTC/USDT:USDT --json
cfos regime BTC/USDT:USDT --tf 4h --json
```

## Event risk kuralları

| Olay | T-24s | T-2s | T+15dk |
|---|---|---|---|
| FOMC faiz kararı | Yeni giriş yok | Bekle | İlk 15 dk işlem yok |
| CPI | Yeni giriş yok | Bekle | Spread normale dönünce |
| NFP | Yeni giriş yok | Bekle | İlk 5 dk işlem yok |
| PCE / GDP | Pozisyon küçült | Bekle | Teyit sonrası |
| Merkez bankası konuşması | Normal | Dikkat | Volatiliteye göre |

## Karar kuralları

1. T-24 saat içinde yeni pozisyon açılacaksa risk yarıya indirilir
   (`--risk-pct` mevcut değerin yarısı, asla limit üstü değil).
2. T-2 saat içinde yeni giriş önerilmez; mevcut pozisyonun stop'u kontrol edilir.
3. Olay sonrası ilk 15 dakika spread ve wick nedeniyle işlem yok.
4. Haberin yönü bilinmiyorsa yön tahmini yapılmaz; yalnızca risk yönetimi.
5. Takvim kaynağı yoksa "olay yok" varsayımı yasaktır.

## Çıktı sözleşmesi

```json
{
  "window": "2026-11-16..2026-11-22",
  "events": [
    {"ts": "2026-11-18T13:30:00Z", "name": "US CPI", "impact": "high", "source": "BLS"}
  ],
  "trade_posture": "no_new_entries_within_24h",
  "notes": ["FOMC 2026-11-19; risk yarıya indirilecek"]
}
```

## Guardrails

- Tarih/saat uydurma; kaynak yoksa `INSUFFICIENT_DATA`.
- "Haber iyi/kötü" yorumunu fiyat tepkisi olmadan kesinleştirme.
- Bu skill emir açmaz; yalnızca `cfos-news-catalyst` ve `cfos-sizing`e girdi verir.
