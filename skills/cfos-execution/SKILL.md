---
name: cfos-execution
description: >-
  Planı bracket emre çevirir: market giriş, ardından reduce-only stop ve
  take-profit. Tüm risk kapılarını yeniden çalıştırır; BLOCK varsa emir
  göndermez. Paper varsayılandır; canlı için üç kilit gerekir.
---

# CFOS Execution

## Amaç

Tek emir yolu. Başka hiçbir skill doğrudan emir göndermez; her şey buradan
geçer. Sıra: kapılar → izin → giriş → stop → TP → journal.

## Ne zaman

- `cfos plan` ile plan dosyası üretildiğinde.
- Kullanıcı "aç / gir / execute" dediğinde.
- Paper lifecycle testinde.

## Girdi

```bash
# 1) Plan üret (emir yok)
cfos plan --symbol BTC/USDT:USDT --side long --entry 65000 --stop 64000 \
  --tp 67000 --tp 68500 --intent trend \
  --rationale "4h trend, EMA20 pullback" \
  --invalidation "4h close below 64,000" \
  --out /tmp/plan.json --json

# 2) Kapıları gör (emir yok)
cfos execute --plan /tmp/plan.json --dry-run --json

# 3) Paper emir (varsayılan)
cfos execute --plan /tmp/plan.json --json
```

Canlı (yalnızca insan açıkça isterse ve onaylarsa):

```bash
# .env: CFOS_MODE=live, CFOS_LIVE_ACK=I_UNDERSTAND_REAL_MONEY
cfos execute --plan /tmp/plan.json --confirm-live --json
```

## Emir akışı

1. `build_exchange` → paper / testnet / live adaptörü.
2. `evaluate_gates` → 15 kapı; tek BLOCK yeterli.
3. `check_live_permission` → mod + ack + confirm.
4. `set_leverage` → plan kaldıracı (≤3x).
5. Market giriş (`client_id = plan.id-entry`).
6. Giriş `closed/filled` değilse **stop eklenmez**, `ERROR` döner.
7. Reduce-only stop (`plan.id-stop`).
8. Her TP için reduce-only TP (`plan.id-tpN`); miktar `split_quantity` ile
   dilimlenir (venue minimumu altına inilmez).
9. Journal: order + position kaydı.

## Verdict anlamları

| Verdict | Anlam | Sonraki adım |
|---|---|---|
| `EXECUTED` | Giriş + bracket tamam | `cfos positions` ile doğrula |
| `DRY_RUN` | Emir yok, kapılar raporlandı | Sonucu oku, gerekirse planı düzelt |
| `BLOCKED` | Kapı veya izin reddetti | Nedeni düzelt; sayıları gevşetme |
| `ERROR` | Venue/emir hatası | `cfos positions` ile gerçek durumu kontrol et |

## Karar kuralları

1. `ERROR` sonrası asla "tekrar dene" demeden önce pozisyonu kontrol et;
   kısmi fill olabilir.
2. Stop eklenemediye pozisyon korumasızdır: derhal `cfos close` öner.
3. Kısmi fill bir hata değildir: stop ve TP merdiveni gerçek `filled_qty`
   üzerine kurulur ve rapor `partial entry` uyarısı taşır. Kalan emir miktarı
   otomatik kovalanmaz; pozisyonu `cfos positions` ile doğrula.
4. Aynı plan id'si ile ikinci kez execute etme; `max_per_symbol` bloklar ama
   yine de kontrol et.
5. Canlı emirde `--confirm-live` bayrağını asla otomatik ekleme.
6. Kill switch her zaman kazanır; execution sırasında açılırsa yeni emir yok.

## Çıktı sözleşmesi

```json
{
  "ok": true,
  "verdict": "EXECUTED",
  "mode": "paper",
  "symbol": "BTC/USDT:USDT",
  "messages": [
    "entry filled at 65013.0",
    "stop attached at 64000.0",
    "2 take-profit order(s) attached"
  ],
  "position": {
    "id": "BTCUSDTUSDT-long-2026-11-14T09:00:00+00:00",
    "entry": 65013.0,
    "stop": 64000.0,
    "take_profit": [67000.0, 68500.0],
    "leverage": 0.325
  },
  "blocked": [],
  "warnings": []
}
```

## Guardrails

- Gerçek para kilidi üç parçalıdır; biri eksikse emir gitmez.
- API anahtarı **çekim yetkisi olmayan**, IP kısıtlı, futures-only olmalı.
- Paper varsayılandır; testnet/live geçişi bilinçli bir eylemdir.
- Bu skill emri kendi başına "düşünüp" açmaz; plan dosyası şarttır.
