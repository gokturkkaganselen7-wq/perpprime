# CFOS v1 — Live Go-Live Checklist

Bu liste eksiksiz tamamlanmadan `CFOS_MODE=live` ile emir gönderilmez.
Her satırı tarih ve imza ile işaretleyin; bu dosyayı commit'lemeyin (kopyasını
özel tutun).

## A. Kod ve ortam

- [ ] `make test` → tüm testler geçiyor.
- [ ] `make audit` → `RESULT: IN SYNC`.
- [ ] `cfos doctor --json` → `status: OK` (WARN varsa gerekçesi yazılı).
- [ ] Python ≥ 3.11, `ccxt` sürümü kaydedildi: ________
- [ ] Repo commit hash'i kaydedildi: ________
- [ ] Veri dizini yedeklenebilir ve yazılabilir.
- [ ] Saat UTC ve senkron (drift < 5 sn).

## B. Anahtar ve venue

- [ ] API anahtarı **trade-only**; çekim yetkisi yok.
- [ ] IP allowlist açık ve yalnızca bot IP'si.
- [ ] Futures/perp izni var; gereksiz spot/transfer izni yok.
- [ ] Testnet anahtarı ile canlı anahtar ayrı.
- [ ] Anahtar `.env` içinde; git geçmişinde sır yok (`git log -p | grep -i key`).
- [ ] Venue bakım takvimi kontrol edildi.
- [ ] Sembol allowlist'i ve kümeler gözden geçirildi.
- [ ] Minimum emir miktarı ve kontrat büyüklüğü doğrulandı.

## C. Risk yapılandırması

- [ ] `risk_per_trade_pct` ≤ 0.50.
- [ ] `daily_loss_limit_pct` ≤ 2.00.
- [ ] `weekly_loss_limit_pct` ≤ 5.00.
- [ ] `max_drawdown_pct` ≤ 10.00.
- [ ] `max_leverage` ≤ 3.0.
- [ ] `max_portfolio_heat_pct` ≤ 3.0.
- [ ] `max_open_positions` ≤ 3, `max_per_symbol` = 1.
- [ ] `max_cluster_positions` ≤ 2.
- [ ] `min_rr` ≥ 2.0.
- [ ] `max_data_age_sec` ≤ 120.
- [ ] Limitleri kimin, neden değiştirdiği kayıtlı.

## D. Paper kanıtı

- [ ] En az 20 kapanmış paper işlem.
- [ ] Her işlemde stop ve TP gerçekten bağlanmış.
- [ ] Kısmi kapatma ve stop taşıma test edilmiş.
- [ ] Kill switch engage/release test edilmiş.
- [ ] `cfos demo` son çalıştırmada başarılı.
- [ ] Journal özeti incelendi; süreç ihlali yok.

## E. Testnet kanıtı

- [ ] En az 10 testnet işlem.
- [ ] Market giriş → stop → TP akışı canlı API'de doğrulandı.
- [ ] Reddedilen emir / kısmi fill senaryosu en az bir kez görüldü ve doğru
      raporlandı.
- [ ] `cfos positions` venue ile birebir uyuşuyor.
- [ ] `cfos close` reduce-only çalışıyor; başka pozisyonu kapatmıyor.
- [ ] Funding ve OI alanları dolu geliyor.
- [ ] Spread WARN/BLOCK davranışı doğrulandı.

## F. Canlıya geçiş (ilk gün)

- [ ] `CFOS_MODE=live`.
- [ ] `CFOS_LIVE_ACK=I_UNDERSTAND_REAL_MONEY`.
- [ ] Tek sembol (BTC veya ETH), minimum boyut.
- [ ] `cfos execute --dry-run` ile kapılar temiz.
- [ ] İlk emir `--confirm-live` ile, gözetim altında.
- [ ] İlk işlemden sonra `cfos positions --json` ve borsa arayüzü karşılaştırıldı.
- [ ] Stop emrinin borsada göründüğü doğrulandı.
- [ ] Günlük zarar limiti test edilmediyse **test etmeyin**; limit zaten kapalı
      olmalı.

## G. İzleme ve geri dönüş

- [ ] Günlük `cfos review --json` rutini kuruldu.
- [ ] Haftalık `cfos journal` + `cfos-postmortem` rutini kuruldu.
- [ ] Kill switch'i kimin çekebileceği yazılı.
- [ ] Geri dönüş planı: `CFOS_MODE=paper`, `cfos kill`, pozisyonları kapat.
- [ ] Borsa API'si çökerse manuel kapatma prosedürü yazılı.
- [ ] İlk 5 işlem için maksimum toplam risk ≤ %1 equity.

## H. İmza

| Alan | Değer |
|---|---|
| Tarih | ________ |
| Sorumlu kişi | ________ |
| Commit | ________ |
| İlk sembol | ________ |
| Başlangıç equity | ________ |
| Onay | ________ |

> Not: Bu checklist bir garanti değildir. Kripto vadeli işlemlerde sermaye
> kaybı olasıdır; yalnızca kaybetmeyi göze alabileceğiniz sermaye ile işlem
> yapın.
