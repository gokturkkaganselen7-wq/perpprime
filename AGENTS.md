# AGENTS.md — PerpPrime / CFOS çalışma kuralları

Bu depo **PerpPrime** — motoru **CFOS v1**: kripto vadeli işlemler için, CCXT
tabanlı, ajanların (OpenCode, Claude Code, Codex) MCP üzerinden otomatik emir
açabildiği bir trading workspace'i.

## Okuma sırası

1. Bu dosya.
2. `skills/cfos-router/SKILL.md` — niyet → zincir → kapılar.
3. Göreve uygun tek skill: `skills/<ad>/SKILL.md`.
4. Sayısal ihtiyaç: `cfos` komutları veya MCP araçları.

## Demir kurallar

1. **Önce durum.** Her oturumda `cfos_status` / `cfos status --json`.
   `kill_switch.engaged=true` ise yeni risk yok.
2. **Sayı uydurma.** Fiyat, funding, ATR, miktar yalnızca `cfos market`,
   `cfos regime`, `cfos size`, `cfos plan` çıktısından gelir.
3. **Miktar tahmini yasak.** Boyut her zaman `cfos_size` veya `cfos_plan`.
4. **Tek emir yolu.** Emir yalnızca `cfos_execute` / `cfos execute` ile gider.
   Skill'ler doğrudan borsa API'si çağırmaz.
5. **Bracket zorunlu.** Giriş → reduce-only stop → reduce-only TP. Stop
   eklenemezse pozisyon kapatılır.
6. **Kapı gevşetme yasak.** `config/risk.toml` limitlerini "işlem geçsin" diye
   değiştirme. Setup'ı değiştir veya dur.
7. **Paper varsayılan.** Canlı için üç kilit: `CFOS_MODE=live`,
   `CFOS_LIVE_ACK=I_UNDERSTAND_REAL_MONEY`, `--confirm-live`.
8. **Kill switch kutsaldır.** Herkes, her an `cfos kill --reason "..."`.
9. **Sır yazma.** API anahtarı `.env` dışına çıkmaz; log'a basılmaz.
10. **Testler yeşil olmadan değişiklik bitmez:** `make test`.

## Komut haritası

```bash
cfos doctor                     # ortam + güvenlik
cfos status --json              # equity, DD, heat, kill
cfos route --text "kırılım"     # niyet + zincir + kapılar
cfos market BTC/USDT:USDT --json
cfos regime BTC/USDT:USDT --tf 4h --json
cfos candles BTC/USDT:USDT --tf 1h --limit 120 --json
cfos size --symbol ... --side long --entry ... --stop ... --json
cfos plan --symbol ... --entry ... --stop ... --tp ... --out /tmp/plan.json
cfos execute --plan /tmp/plan.json --dry-run --json
cfos execute --plan /tmp/plan.json --json          # paper
cfos positions --json
cfos close BTC/USDT:USDT [--qty N] --json
cfos tick BTC/USDT:USDT 64000 --json               # paper stop/TP
cfos kill --reason "..." | cfos kill --release
cfos journal --limit 50 --json
cfos review --limit 100 --json
```

MCP araçları: `cfos_status, cfos_doctor, cfos_market, cfos_regime,
cfos_route, cfos_size, cfos_plan, cfos_execute, cfos_positions, cfos_close,
cfos_tick, cfos_kill, cfos_unkill, cfos_journal, cfos_review`.

OpenCode notu: Code Mode açıkken bu araçlar `tools.cfos.cfos_status`,
`tools.cfos.cfos_execute` … biçiminde çağrılır. Claude Code ve Codex'te düz
MCP adı (`cfos_status`) kullanılır. Aynı sunucu, aynı davranış.

## Kod düzeni

```
src/cfos/        Python çekirdeği (risk, execution, paper, journal, MCP)
skills/          24 SKILL.md — ajan talimatları
config/          risk.toml + universe.toml (tek doğruluk kaynağı)
tests/           pytest; ağ erişimi yok
docs/            SAFETY, LIVE_CHECKLIST, ARCHITECTURE
bin/cfos         PYTHONPATH ayarlı başlatıcı
```

## Değişiklik kuralları

- Risk limitleri ve kapı sırası testlerle korunur; değiştirirsen test ekle.
- `skills/` içindeki her yeni skill `cfos-router` registry tablosuna eklenir;
  `make audit` `IN SYNC` vermeden bitirme.
- `mcp_server.py` stdout'a yalnızca JSON-RPC yazar; loglar stderr'e gider.
- Canlıya dair hiçbir otomatik test gerçek emir göndermez; `conftest.py`
  daima paper moduna zorlar.
