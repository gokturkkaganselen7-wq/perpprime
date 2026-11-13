---
name: cfos-risk-gate
description: >-
  CFOS risk kapılarını yorumlar: kill switch, günlük/haftalık zarar, drawdown,
  işlem sayısı, pozisyon ve küme limitleri, heat, cooldown, R:R, veri tazeliği,
  spread ve kaldıraç. Amaç kapıyı "açmak" değil, kapının neden kapalı olduğunu
  doğru okumak ve emri durdurmaktır. Fail-closed çalışır.
---

# CFOS Risk Gate

## Amaç

`cfos execute` içindeki 15 kapının insan tarafından okunabilir yorumu. Bu skill
**hiçbir limiti değiştirmez, hiçbir kapıyı bypass etmez**. Karar: ALLOW /
ALLOW+WARN / BLOCK.

## Ne zaman

- Her `cfos plan` sonrası, `cfos execute` öncesi.
- `cfos execute` BLOCK döndürdüğünde nedeni açıklamak için.
- Kullanıcı "neden işlem açamıyorum?" diye sorduğunda.

Kullanma: saf piyasa analizi istendiğinde (→ `cfos-regime`).

## Girdi

```bash
cfos status --json
cfos plan --symbol BTC/USDT:USDT --side long --entry 65000 --stop 64000 \
  --tp 67000 --intent trend --out /tmp/plan.json --json
cfos execute --plan /tmp/plan.json --dry-run --json
```

## Kapı yorumu

| Kapı | BLOCK koşulu | Doğru tepki |
|---|---|---|
| `kill_switch` | `data/KILL` dosyası var | Dur. Nedeni oku. `cfos kill --release` yalnızca insan onayıyla. |
| `universe` | Sembol allowlist dışında veya 24h hacim düşük | Sembol değiştir, limiti düşürme. |
| `daily_loss` | Günlük P&L ≤ -%2 | Günü kapat. |
| `weekly_loss` | Haftalık P&L ≤ -%5 | Haftayı kapat. |
| `drawdown` | Peak'ten ≥ %10 düşüş | Yeni risk yok; stratejiyi gözden geçir. |
| `trade_count` | Bugün 6 işlem | Yarına bırak. |
| `max_positions` | 3 açık pozisyon | Önce kapat, sonra aç. |
| `max_per_symbol` | Aynı sembolde zaten pozisyon | Ekleme yapma (pyramiding v1'de yok). |
| `correlation` | Kümede 2 pozisyon | Aynı bahsi üçüncü kez alma. |
| `portfolio_heat` | Açık risk + yeni risk > %3 | Yeni riski küçült veya mevcut kapat. |
| `cooldown` | Üst üste 2 zarar + 60 dk dolmadı | Bekle. |
| `reward_risk` | R:R < 2.0 | TP'yi uzat veya stop'u sık; sayıyı değil setup'ı düzelt. |
| `data_freshness` | Snapshot yok veya > 120 sn | Veriyi yenile. |
| `spread` | > 16 bps BLOCK, 8-16 bps WARN | Likit saat bekle. |
| `leverage` | Plan kaldıracı > 3x | Planı yeniden boyutla. |

## Karar kuralları

1. Tek bir BLOCK yeterlidir; diğer kapılar ALLOW olsa bile emir gönderilmez.
2. WARN emri durdurmaz ama rapora yazılır.
3. Aynı hatadan sonra kullanıcı "limitleri gevşet" derse reddet:
   limitler `config/risk.toml` içinde ve değişiklik bilinçli bir eylemdir.
4. `data_freshness` BLOCK ise hiçbir sayısal yorum yapılmaz; önce veri.

## Çıktı sözleşmesi

```json
{
  "verdict": "BLOCK",
  "blocked_by": ["reward_risk"],
  "warnings": ["spread: 9.4 bps above 8.0 bps"],
  "action": "TP 67,000 yerine 68,000 veya stop 64,500; R:R >= 2.0 olmadan emir yok"
}
```

## Guardrails

- Kapı limitlerini komut satırından override etme yetkisi yoktur.
- `--dry-run` her zaman güvenlidir; gerçek emir değildir.
- Canlı modda üç kilit birlikte gerekir; bu skill `--confirm-live` önermez.
