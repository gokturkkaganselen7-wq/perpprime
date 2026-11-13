---
name: cfos-ops
description: >-
  CFOS kurulumunu ve güvenlik operasyonlarını yönetir: doctor kontrolleri, MCP
  bağlantısı, kill switch, veri dizini, mod geçişi ve yedekleme. Emir açmaz;
  sistem sağlığını korur.
---

# CFOS Ops

## Amaç

Sistem çalışmıyorsa strateji anlamsızdır. Bu skill kurulumu doğrular, kill
switch'i yönetir ve ajan bağlantısını test eder.

## Ne zaman

- İlk kurulumdan sonra.
- `cfos doctor` FAIL/WARN verdiğinde.
- Ajan (OpenCode / Claude Code / Codex) CFOS araçlarını göremiyorsa.
- Mod değişimi (paper → testnet → live) öncesi.
- Şüpheli davranış sonrası (emir gitmiyor, fiyat gelmiyor).

## Girdi

```bash
cfos doctor --json
cfos status --json
cfos kill --reason "manual maintenance"
cfos kill --release
cfos mcp   # stdio; ajanlar bunu konuşur
```

## Kontrol listesi

| Kontrol | Beklenen |
|---|---|
| Python | ≥ 3.11 |
| ccxt | kurulu (paper için WARN kabul) |
| Mod | paper varsayılan |
| Config | `config/risk.toml`, `config/universe.toml` okunur |
| Credentials | paper'da gerekmez; testnet/live'da zorunlu |
| Live ack | canlıda `I_UNDERSTAND_REAL_MONEY` |
| Data dir | yazılabilir |
| Journal | SQLite açılır |
| Kill switch | beklenen durumda |
| Saat | UTC, drift yok |

## Mod geçişi

1. **paper → testnet**: `.env` içinde `CFOS_MODE=testnet`, testnet API anahtarı,
   `cfos doctor`, ardından küçük bir market emir testi.
2. **testnet → live**: `CFOS_MODE=live`, `CFOS_LIVE_ACK`, trade-only anahtar,
   IP allowlist, `cfos doctor` FAIL'siz, tek sembolde minimum boyut testi.
3. Geri dönüş her zaman mümkündür: `CFOS_MODE=paper`; veri dizini ayrı tutulur.

## Kill switch prosedürü

- **Engage**: `cfos kill --reason "..."` — anında, herkes tarafından.
- **Release**: yalnızca neden anlaşıldıysa `cfos kill --release`.
- Kill dosyası `data/KILL`; varlığı yeterlidir, içeriği neden kaydıdır.
- Kill engaged iken `cfos execute` her zaman BLOCKED döner.

## MCP bağlantısı

OpenCode `opencode.json`, Claude Code `.mcp.json`, Codex `config.toml` içinde
şu sunucu tanımlı olmalı:

```json
{
  "command": "/home/gokturkkaganselen7/opencodetrading/cfos/.venv/bin/python",
  "args": ["-m", "cfos", "mcp"],
  "env": {"PYTHONPATH": "/home/gokturkkaganselen7/opencodetrading/cfos/src"}
}
```

Doğrulama: ajan `cfos_doctor` aracını çağırabiliyor ve `status: OK/WARN`
dönüyor olmalı.

## Karar kuralları

1. Doctor FAIL varken canlı moda geçilmez.
2. API anahtarı çekim yetkiliyse derhal iptal edilir; CFOS çekim yapmaz.
3. Veri dizini yedeği: `data/cfos.sqlite3` + `data/paper_state.json`.
4. Loglar stdout'a değil stderr'e gider; MCP protokolünü bozacak print yasak.
5. Şüphede kalındığında kill switch çekilir; maliyeti sıfırdır.

## Çıktı sözleşmesi

```json
{
  "doctor": "OK",
  "mode": "paper",
  "kill_switch": "released",
  "mcp": "reachable",
  "warnings": [],
  "next": "none"
}
```

## Guardrails

- Bu skill emir açmaz; `cfos execute` çağırmaz.
- Live geçişini otomatikleştirme; insan onayı şart.
- Kill switch'i "test" için engage edip release etmeyi unutma.
