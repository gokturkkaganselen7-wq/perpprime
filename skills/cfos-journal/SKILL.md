---
name: cfos-journal
description: >-
  İşlem günlüğünü tutar ve okur: kararlar, emirler, açılan/kapanan pozisyonlar,
  equity eğrisi. SQLite tabanlıdır; her execute kararı kaydedilir. Günlük
  olmayan işlem yok sayılır.
---

# CFOS Journal

## Amaç

"Ne yaptım ve neden?" sorusunun kalıcı kaydı. Hafıza değil, veritabanı.
`data/cfos.sqlite3` içinde `decisions`, `orders`, `positions`, `equity`
tabloları bulunur.

## Ne zaman

- Her işlem sonrası (otomatik).
- Günlük kapanışta.
- Haftalık/aylık review öncesi.
- "Geçen hafta ne yaptım?" sorusunda.

## Girdi

```bash
cfos journal --limit 50 --json
cfos review --limit 100 --json
cfos status --json
```

## Kayıt içeriği

| Tablo | Alanlar |
|---|---|
| `decisions` | ts, mode, intent, symbol, side, verdict, reasons, plan |
| `orders` | ts, client_id, venue, symbol, type, side, qty, price, status |
| `positions` | id, opened_at, closed_at, symbol, side, qty, entry, exit, stop, tp, leverage, risk_amount, pnl, fees, status |
| `equity` | ts, equity, realized, unrealized |

## Okuma kuralları

1. `verdict=BLOCKED` kayıtları da değerlidir; neden işlem yapılmadığını gösterir.
2. Kapanmış işlem sayısı < 20 ise istatistiksel yorum yapılmaz.
3. `pnl` ve `fees` ayrı okunur; brüt kâr yanıltıcıdır.
4. `risk_amount` sıfırsa R hesabı yapılmaz.
5. Equity eğrisi ile işlem P&L'i tutmuyorsa senkronizasyon hatası vardır;
   `cfos tick` (paper) veya manuel mutabakat gerekir.

## Karar kuralları

1. Journal'a yazılmayan işlem "yapılmamış" sayılır.
2. Aynı `client_id` ikinci kez yazılırsa durum güncellenir, kopya oluşmaz.
3. Karar kaydı olmadan pozisyon açıklanamaz; execution bunu garanti eder.
4. Günlük özet: equity, realized, işlem sayısı, verdict dağılımı.
5. Haftalık özet: expectancy, profit factor, max DD, en iyi/en kötü işlem.

## Çıktı sözleşmesi

```json
{
  "summary": {
    "trades": 12,
    "wins": 5,
    "losses": 7,
    "win_rate": 41.67,
    "total_pnl": -18.4,
    "profit_factor": 0.82,
    "avg_r": -0.07,
    "fees": 14.2
  },
  "open": [],
  "closed": ["..."],
  "decisions": ["..."]
}
```

## Guardrails

- Journal dosyasını elle silme; arşivle.
- Paper ve canlı kayıtları aynı DB'de mode alanıyla ayrılır; karıştırma.
- Geçmiş işlemi "düzeltmek" için UPDATE yazma; yeni kayıt ekle.
