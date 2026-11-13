---
name: cfos-walk-forward
description: >-
  Backtest sonucunu out-of-sample dönemlerde doğrular: train/test pencereleri,
  parametre kararlılığı, rejim bazlı performans ve overfit denetimi. In-sample
  başarıyı kanıt saymaz.
---

# CFOS Walk-Forward

## Amaç

Backtest'te iyi görünen stratejinin gerçekten ileriye taşınabilir olup olmadığını
test etmek. Amaç stratejiyi kurtarmak değil, zayıflığını erken bulmak.

## Ne zaman

- `cfos-backtest` `PASS_TO_WALK_FORWARD` döndürdüğünde.
- Parametre optimizasyonu yapıldığında.
- Canlıya geçiş öncesi son kapı.

## Girdi

- `cfos-backtest` çıktısı (aynı kural seti, aynı veri).
- Yeterli OHLCV:

```bash
cfos candles BTC/USDT:USDT --tf 4h --limit 1000 --json > candles.json
```

## Pencere tasarımı

| Bölüm | Oran | Kullanım |
|---|---|---|
| Train (ilk) | %50 | Parametre seçimi |
| Validation | %25 | Model seçimi |
| Test (son) | %25 | **Tek sefer** final ölçüm |

Kural: test penceresi sonuç görüldükten sonra parametre değiştirilirse test
yanmıştır; yeni veri gerekir.

## Kararlılık kontrolleri

1. Parametre komşuluğu: ±%20 değişim sonucu %30'dan fazla bozuyorsa kırılgan.
2. Rejim bazlı: trend ve range dönemlerinde ayrı expectancy pozitif mi?
3. Yıl bazlı: tek bir yıl tüm kârı üretiyorsa şüpheli.
4. İşlem dağılımı: ilk 10 işlem toplam kârın yarısından fazlasıysa şans olabilir.
5. Drawdown: test penceresinde train'in 1.5 katından fazlaysa reddet.

## Karar kuralları

1. Test penceresi expectancy ≤ 0 → strateji reddedilir.
2. Test profit factor < 1.1 → reddedilir.
3. Parametre komşuluğu kırılgan → reddedilir veya parametre sayısı azaltılır.
4. Sadece tek rejimde çalışıyor → "rejim koşullu" etiketiyle sınırlı onay.
5. Walk-forward geçemeyen strateji paper'da bile çalıştırılmaz.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-walk-forward",
  "windows": {"train": 500, "validation": 250, "test": 250},
  "test_trades": 38,
  "test_expectancy_pct": 0.11,
  "test_profit_factor": 1.28,
  "param_stability": "acceptable",
  "regime_breakdown": {"trend": 0.19, "range": -0.02},
  "verdict": "CONDITIONAL_PASS",
  "condition": "yalnızca trend rejiminde, range'de no-trade"
}
```

## Guardrails

- Test sonucunu gördükten sonra parametre değiştirme; yeni veri topla.
- Az sayıda işlemle "geçti" deme; örneklem gücünü raporla.
- Bu skill canlı emir açmaz.
