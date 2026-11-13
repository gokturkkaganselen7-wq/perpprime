---
name: cfos-sizing
description: >-
  Risk bazlı pozisyon boyutu hesaplar: equity, risk yüzdesi, giriş-stop mesafesi,
  kontrat büyüklüğü, adım yuvarlama, kaldıraç tavanı ve likidasyon mesafesi.
  Miktarı asla tahmin etmez; `cfos size` veya `cfos plan` çıktısını kullanır.
---

# CFOS Sizing

## Amaç

"Kaç adet?" sorusunun tek doğru cevabı: stop mesafesi ve risk bütçesinden
türetilen miktar. Hissiyatla lot belirlemek yasaktır.

## Ne zaman

- Her işlem öncesi, zorunlu.
- Kullanıcı "ne kadar alayım?" dediğinde.
- Risk yüzdesi değiştiğinde (asla limit üstüne).
- Kaldıraç veya stop değiştiğinde yeniden.

## Girdi

```bash
cfos status --json
cfos size --symbol BTC/USDT:USDT --side long --entry 65000 --stop 64000 --json
```

Opsiyonel risk yüzdesi (varsayılan `config/risk.toml` → 0.50%):

```bash
cfos size --symbol BTC/USDT:USDT --side long --entry 65000 --stop 64000 \
  --risk-pct 0.25 --json
```

Plan üretirken komut aynı hesabı yapar ve plan dosyasına yazar:

```bash
cfos plan --symbol BTC/USDT:USDT --side long --entry 65000 --stop 64000 \
  --tp 67000 --tp 68500 --intent trend --out /tmp/plan.json --json
```

## Hesap

```
risk_amount   = equity * risk_pct / 100
stop_distance = |entry - stop|
raw_qty       = risk_amount / (stop_distance * contract_size)
qty           = floor(raw_qty, qty_step)
notional      = qty * entry * contract_size
leverage      = notional / equity
```

Kurallar:

- `leverage > max_leverage (3x)` → miktar kırpılır, uyarı yazılır.
- `qty < min_qty` → plan reddedilir; risk artırma veya stop daraltma dışında
  çözüm yoktur.
- Stop, tahmini likidasyon fiyatının ötesindeyse → plan reddedilir.
- `contract_size` venue'den gelir; paper'da 1.0.

## Örnek

Equity 10,000, risk %0.5 = 50 USD. Entry 65,000, stop 64,000 → mesafe 1,000.
Qty = 50 / 1,000 = 0.05 BTC. Notional 3,250 USD → 0.325x. Likidasyon ~%33
uzakta; stop %1.5'te. Güvenli.

## Karar kuralları

1. Kullanıcı "daha büyük" isterse reddet; limit `risk.toml`'da.
2. Aynı sembolde açık pozisyon varsa yeni boyut hesaplanmaz (ekleme yok).
3. Heat %3'e yaklaşıyorsa risk yüzdesi düşürülür, artırılmaz.
4. Kaldıraç 3x'i aşıyorsa "kaldıraç kırpıldı" uyarısı rapora yazılır.
5. Likidasyon mesafesi stop mesafesinin 2 katından azsa plan reddedilir.

## Çıktı sözleşmesi

```json
{
  "symbol": "BTC/USDT:USDT",
  "side": "long",
  "ok": true,
  "qty": 0.05,
  "notional": 3250.0,
  "leverage": 0.325,
  "risk_amount": 50.0,
  "risk_pct": 0.5,
  "stop_distance": 1000.0,
  "liq_price": 43433.33,
  "warnings": []
}
```

## Guardrails

- Miktarı elle yuvarlama; `cfos size`/`cfos plan` ne derse o.
- Stop'suz boyut hesaplama yoktur.
- Bu skill emir göndermez; plan dosyasını `cfos-execution`a devreder.
