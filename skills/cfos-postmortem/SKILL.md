---
name: cfos-postmortem
description: >-
  Kapanan işlemleri tek tek analiz eder: tez, giriş/çıkış kalitesi, MAE/MFE,
  kural ihlali, şans mı beceri mi. Amaç suçlu bulmak değil, süreci düzeltmek.
---

# CFOS Postmortem

## Amaç

Her kapanan işlemden bir ders çıkarmak. Kazanan işlem de incelenir; kötü
süreçle kazanmak, iyi süreçle kaybetmekten daha tehlikelidir.

## Ne zaman

- İşlem kapandıktan sonra (özellikle stop/TP sonrası).
- Haftalık review'da toplu.
- Ardışık 2 zarardan sonra (cooldown ile birlikte).
- Beklenmeyen büyük kâr/zarar sonrası.

## Girdi

```bash
cfos journal --limit 50 --json
cfos review --limit 100 --json
cfos status --json
```

Paper işlemlerde MAE/MFE için fiyat geçmişi:

```bash
cfos candles BTC/USDT:USDT --tf 1h --limit 200 --json
```

## İnceleme şablonu

| Alan | Soru |
|---|---|
| Tez | Neden girdim? Hangi rejim/setup? |
| Giriş | Planlanan fiyat mı, kovalamaca mı? |
| Stop | Yapısal mı, keyfi mi? Likidite bandında mı? |
| Boyut | Risk %0.5 mi, değil mi? |
| Yönetim | Stop taşındı mı? Kısmi kâr kurala uydu mu? |
| Çıkış | Stop/TP mi, duygu mu? |
| Sonuç | R cinsinden ne oldu? |
| Kural ihlali | Var mı? Hangi kural? |
| Ders | Bir sonraki işlemde ne değişecek? |

## Sınıflandırma

- `GOOD_PROCESS_GOOD_OUTCOME` — kurala uygun, kâr.
- `GOOD_PROCESS_BAD_OUTCOME` — kurala uygun, zarar. **Kabul edilebilir.**
- `BAD_PROCESS_GOOD_OUTCOME` — şans. Tekrarlanırsa zarar getirir.
- `BAD_PROCESS_BAD_OUTCOME` — düzeltilmesi gereken.

## Karar kuralları

1. Sonucu değil süreci puanla; tek işlemden strateji değiştirme.
2. Aynı kural ihlali 3 kez tekrarlarsa o kuralı checklist'e ekle.
3. Stop genişletme veya boyut aşımı varsa işlem "kural dışı" sayılır.
4. Kazanan işlemde bile R < planlanan R ise erken çıkış incelenir.
5. Postmortem çıktısı yeni işlem gerekçesi değildir.

## Çıktı sözleşmesi

```json
{
  "position_id": "BTCUSDTUSDT-long-2026-11-14T09:00:00Z",
  "r_multiple": -0.94,
  "classification": "GOOD_PROCESS_BAD_OUTCOME",
  "thesis_valid": true,
  "rule_violations": [],
  "lessons": ["stop yapısal seviyedeydi; tekrar edilebilir"],
  "action": "değişiklik yok"
}
```

## Guardrails

- Kaybeden işlemi "kötü şans" diye kapatma; süreç kontrolü yap.
- Kazanan işlemi otomatik "doğru" kabul etme.
- Bu skill emir açmaz; yalnızca rapor üretir.
