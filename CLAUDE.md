# CLAUDE.md — PerpPrime

@AGENTS.md

## Claude Code özel notları

- Proje skill'leri `.claude/skills/` altında `skills/` dizinine bağlıdır.
  Yeni oturumda `cfos-router` skill'ini oku, sonra göreve uygun tek skill'i.
- MCP sunucusu `.mcp.json` içinde tanımlıdır. Bağlantı yoksa:
  `claude mcp list` ve `claude mcp get cfos`.
- Emir öncesi zorunlu sıra:
  `cfos_status` → `cfos_route` → ilgili analiz → `cfos_plan` → `cfos_execute`
  (`dry_run=true` ile bir kez, sonra paper).
- `cfos_execute` çağrısında `confirm_live=true` **yalnızca** kullanıcı açıkça
  gerçek para ile işlem istediğinde ve `.env`'de `CFOS_MODE=live` +
  `CFOS_LIVE_ACK=I_UNDERSTAND_REAL_MONEY` varsa kullanılır.
- Uzun analizlerde sayıları komut çıktısından kopyala; sohbet bağlamından
  "hatırlama" yapma.
- Hata durumunda önce `cfos positions --json` ile gerçek durumu kontrol et;
  körlemesine tekrar deneme yapma.
