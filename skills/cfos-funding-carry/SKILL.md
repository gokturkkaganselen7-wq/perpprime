---
name: cfos-funding-carry
description: >-
  Funding ve basis taşıma fırsatlarını değerlendirir: perp funding, spot-perp
  basis, delta-neutral kurulum, getiri ve likidasyon riski. Pozitif carry
  varsa bile getiriyi riskle karşılaştırmadan işlem önermez.
---

# CFOS Funding Carry

## Amaç

Piyasa yönünden bağımsız getiri: funding tahsil etmek veya basis yakalamak.
Kripto'da "risksiz getiri" yoktur; bu skill riski görünür kılar.

## Ne zaman

- Funding sürekli aynı yönde ve yüksek (≥ 0.03% / 8h).
- Basis (perp - spot) belirgin açıldığında.
- Kullanıcı "funding taşıma / carry" dediğinde.
- Yatay piyasada getiri arayışı.

## Girdi

```bash
cfos market BTC/USDT:USDT --json
cfos regime BTC/USDT:USDT --tf 4h --json
cfos status --json
cfos size --symbol BTC/USDT:USDT --side short --entry 65000 --stop 67000 --risk-pct 0.25 --json
```

Spot bacak v1'de ayrı bir venue komutu değildir; agent spot fiyatı doğrulanmış
kaynaktan alır ve kaynağı yazar. Spot bacak kurulamıyorsa carry **delta-neutral
değildir**; bu açıkça belirtilir.

## Getiri hesabı

```
8h funding getirisi = funding_rate * notional
günlük ≈ 3 * 8h funding (venue'ye göre değişir)
yıllık ≈ günlük * 365
```

Örnek: 10,000 USD notional, funding 0.05%/8h → 5 USD/8h → ~15 USD/gün → ~%54
yıllık brüt. Ancak:

- Perp bacak likidasyon riski taşır.
- Spot-perp basis kapanabilir.
- Giriş/çıkış ücretleri getiriyi yer.
- Funding işareti dönebilir.

## Karar kuralları

1. Net getiri (ücretler + kayma + stop maliyeti düşülmüş) < %5 yıllık ise
   işlem önerilmez.
2. Perp bacak kaldıracı ≤ 2x; likidasyon fiyatı stop'un ötesinde olmalı.
3. Funding işareti son 3 ödemede kararlı değilse "carry istikrarsız" denir.
4. Delta-neutral kuramıyorsan yön riski vardır; risk limitleri normal işlem
   gibi uygulanır.
5. Aynı anda hem carry hem yönlü pozisyon aynı sembolde tutulmaz (v1 kuralı).

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-funding-carry",
  "verdict": "READY_FOR_PLAN",
  "symbol": "BTC/USDT:USDT",
  "structure": "short_perp_long_spot",
  "funding_rate": 0.0005,
  "gross_apy_pct": 54.0,
  "net_apy_pct": 31.2,
  "leverage": 2.0,
  "liquidation_buffer_pct": 38.0,
  "risks": ["funding sign flip", "basis compression", "venue outage"],
  "invalidation": "funding < 0.01% for 3 consecutive payments"
}
```

## Guardrails

- "Risksiz" kelimesini kullanma.
- Likidasyon mesafesi hesaplanmadan carry önerme.
- Bu skill emir açmaz; iki bacaklı yapı v1'de manuel yürütülür, CFOS tek
  bacak planı üretir ve diğer bacak açıkça raporlanır.
