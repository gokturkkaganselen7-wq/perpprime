---
name: cfos-edge-review
description: >-
  Bir stratejinin kenarını denetler: ekonomik gerekçe, overfit riski, örneklem
  yeterliliği, maliyet duyarlılığı ve rejim bağımlılığı. Backtest'i onaylamaz;
  "bu kenar neden var ve ne zaman ölür?" sorusunu sorar.
---

# CFOS Edge Review

## Amaç

İstatistiksel olarak pozitif görünen bir stratejinin gerçek bir ekonomik kenar
mi yoksa veri madenciliği mi olduğunu ayırmak.

## Ne zaman

- Backtest + walk-forward sonrası.
- Yeni bir setup canlıya alınmadan önce.
- Canlı performans backtest'ten sapınca.
- `cfos-review` sonrası aylık değerlendirmede.

## Girdi

- `cfos-backtest` ve `cfos-walk-forward` çıktıları.
- `cfos review --json` (canlı/paper sonuçlar).
- `cfos journal --json` (işlem detayları).

## Beş soru

1. **Neden var?** Kenarın kaynağı: likidite sağlama, davranışsal hata, yapısal
   funding, bilgi avantajı. "Teknik görünüyor" gerekçe değildir.
2. **Kim kaybediyor?** Her kenarın karşı tarafı vardır. Karşı taraf neden
   kaybediyor? Cevap yoksa kenar muhtemelen yok.
3. **Ne zaman ölür?** Rejim değişimi, ücret artışı, rekabet, likidite kaybı.
   Ölüm koşulu yazılı olmalı.
4. **Örneklem yeterli mi?** ≥100 işlem ve ≥2 rejim; değilse "aday" statüsü.
5. **Maliyete dayanıyor mu?** Ücret/kayma 2x artınca kenar negatife dönerse
   kırılgandır.

## Overfit kırmızı bayrakları

| Sinyal | Yorum |
|---|---|
| Çok sayıda parametre (>5) | açıklama gücü düşük |
| Tek sembolde çalışıp evrende çalışmıyor | sembole özel tesadüf |
| Tek yılda tüm kâr | rejim şansı |
| Komşu parametrelerde çöküş | kırılgan tepe |
| İşlem sayısı < 50 | istatistik yok |
| Kâr sadece TP1'de, TP2 hep zarar | asimetri şüphesi |

## Karar sınıfları

- `EDGE_CONFIRMED`: ekonomik gerekçe + ≥100 işlem + 2 rejim + maliyet dayanıklı.
- `EDGE_CANDIDATE`: gerekçe var, örneklem yetersiz → paper'da izle.
- `NO_EDGE`: gerekçe yok veya maliyetle ölüyor.
- `INSUFFICIENT_DATA`: metrik eksik.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-edge-review",
  "strategy": "breakout_4h_v1",
  "classification": "EDGE_CANDIDATE",
  "economic_reason": "squeeze sonrası stop avı likiditesi",
  "who_loses": "erken giren kaldıraçlı trader'lar",
  "kill_condition": "ardışık 20 işlemde PF < 1.0",
  "red_flags": ["örneklem 62 işlem"],
  "next": "paper 3 ay, aylık review"
}
```

## Guardrails

- "Kenar var" demek için tek metrik yetmez.
- Canlı sonuçlar backtest'ten kötüyse stratejiyi savunma; sapmayı raporla.
- Bu skill emir açmaz.
