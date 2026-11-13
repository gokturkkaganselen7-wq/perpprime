# CFOS v1 — Safety

Bu belge CFOS'un **neyi koruduğunu**, **neyi korumadığını** ve bir olay
anında ne yapılacağını tanımlar. Kripto vadeli işlemlerde sermaye kaybı
gerçektir; bu araç kaybı imkânsız kılmaz, yalnızca aptalca kayıpları engeller.

## 1. Savunma katmanları

1. **Mod kilidi** — varsayılan `paper`; anahtar yok, gerçek emir yok.
2. **Canlı üçlü kilit** — `CFOS_MODE=live` + `CFOS_LIVE_ACK` +
   `--confirm-live` aynı anda olmadan canlı emir gitmez.
3. **Kill switch** — `data/KILL` dosyası varsa tüm yeni risk bloke.
4. **Risk kernel** — 15 kapı, saf fonksiyonlar, fail-closed.
5. **Bracket zorunluluğu** — giriş sonrası reduce-only stop ve TP merdiveni.
   Kısmi fill'de bracket dolu miktara kurulur; fill doğrulanamazsa venue
   pozisyonu kontrol edilir ve varsa savunmacı kapatma yapılır.
6. **Tek emir yolu** — `execution.execute` dışında `create_order` çağrısı yok.
7. **Journal** — her karar, emir ve pozisyon SQLite'ta; denetlenebilir.
8. **Allowlist** — yalnızca `config/universe.toml` sembolleri.
9. **Veri tazeliği** — 120 sn'den eski snapshot ile emir yok.
10. **Kaldıraç tavanı** — 3x; stop likidasyonun ötesindeyse plan reddedilir.

## 2. API anahtarı hijyeni

- **Çekim yetkisi kapalı** anahtar kullanın. CFOS zaten çekim çağrısı yapmaz;
  anahtar çalınırsa hasar sınırlı kalır.
- IP allowlist açın; anahtarı yalnızca botun çalıştığı IP'den kullanın.
- Futures trading izni verin, spot/transfer izni vermeyin (gerekiyorsa).
- Anahtarı `.env` dışında tutmayın; `.env` `.gitignore` içinde.
- Anahtarı log'a, sohbet bağlamına veya plan JSON'una yazmayın.
- Sızma şüphesinde: anahtarı borsadan **hemen iptal edin**, kill switch çekin.
- Testnet ve canlı için ayrı anahtar kullanın.

## 3. Kill switch

```bash
cfos kill --reason "manual: unexpected drawdown"
cfos kill --release        # yalnızca neden anlaşıldıysa
```

- Dosya tabanlıdır; süreç çökse de kalır, `ls data/KILL` ile görülür.
- Okunamayan dosya "engaged" sayılır (fail-closed).
- `cfos execute` her durumda kill switch'i yeniden okur; önbellek yoktur.
- Ajanlar `cfos_kill` aracını her zaman çağırabilir.

## 4. Risk kapıları ve anlamları

| Kapı | Tetik | Sonuç |
|---|---|---|
| `kill_switch` | KILL dosyası | BLOCK |
| `universe` | Allowlist dışı / düşük hacim | BLOCK |
| `daily_loss` | ≤ -%2 günlük | BLOCK |
| `weekly_loss` | ≤ -%5 haftalık | BLOCK |
| `drawdown` | ≥ %10 peak-to-trough | BLOCK |
| `trade_count` | 6 işlem/gün | BLOCK |
| `max_positions` | 3 açık | BLOCK |
| `max_per_symbol` | Aynı sembolde 1 | BLOCK |
| `correlation` | Kümede 2 | BLOCK |
| `portfolio_heat` | Açık risk + yeni > %3 | BLOCK |
| `cooldown` | 2 ardışık zarar + 60 dk | BLOCK |
| `reward_risk` | R:R < 2.0 | BLOCK |
| `data_freshness` | Snapshot yok / > 120 sn | BLOCK |
| `spread` | > 16 bps BLOCK, 8-16 WARN | BLOCK/WARN |
| `leverage` | > 3x | BLOCK |

Limitler `config/risk.toml` içindedir. Değiştirmek bilinçli bir eylemdir;
ajanın "işlem geçsin" diye limit değiştirmesi yasaktır.

## 5. Neyi korumaz

- **Piyasa riski**: stop gap ile atlanabilir; likidasyon stop'tan önce
  gelebilir. Kaldıraç kullanmayın veya çok düşük tutun.
- **Venue riski**: borsa çökebilir, API yalanabilir, funding anormal olabilir.
- **Model/agent hatası**: ajan yanlış tez kurabilir. Kapılar matematiksel
  tutarlılığı sağlar, doğru yönü değil.
- **Veri kalitesi**: public API bozuk olabilir; `cfos-data-quality` bunu
  yakalamaya çalışır ama garanti etmez.
- **Regülasyon/vergi**: kullanıcının sorumluluğundadır.
- **Slippage**: paper modeli sabit 2 bps; gerçek piyasa daha kötü olabilir.

## 6. Olay müdahalesi

1. `cfos kill --reason "incident: ..."` — ilk saniye.
2. `cfos positions --json` — gerçek venue durumu.
3. Gerekiyorsa `cfos close <symbol>` veya borsa arayüzünden manuel kapat.
4. `cfos journal --limit 50 --json` — son kararları oku.
5. `data/cfos.sqlite3` ve `data/paper_state.json` yedeğini al.
6. Neden analiz edilene kadar `cfos kill --release` **yapma**.
7. Düzeltme sonrası önce paper, sonra testnet, en son canlı.

## 7. Paper → testnet → live geçişi

- Paper: en az 20 kapanmış işlem, pozitif olmayan sonuç bile kabul (süreç
  doğruluğu önemli).
- Testnet: gerçek API akışı, küçük miktar, en az 10 işlem, stop/TP doğrulaması.
- Live: `docs/LIVE_CHECKLIST.md` tamamlanmadan başlamaz.

## 8. Yazılım güvenliği

- MCP sunucusu stdout'a yalnızca JSON-RPC yazar; hata/log stderr'e gider.
- Tool girdileri şema ile doğrulanır; bilinmeyen araç `-32601` döner.
- Plan dosyası kullanıcı tarafından üretilir; `cfos execute` planı yeniden
  boyutlandırmaz, kapılardan geçirir.
- Testler `CFOS_MODE=paper` ve geçici dizin kullanır; gerçek emir yoktur.
- Bağımlılık yüzeyi: Python stdlib + `ccxt` (yalnızca gerçek venue için).

## 9. Acil durum komutları

```bash
cfos kill --reason "emergency"
cfos positions --json
cfos close BTC/USDT:USDT --json
cfos journal --limit 20 --json
```
