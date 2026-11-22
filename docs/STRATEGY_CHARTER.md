# PerpPrime — Strateji Tüzüğü (v1, 2026-11-22)

Bu belge **ne işlem yaptığımızı, hangi ufukta, hangi risk bütçesiyle ve neyi
kanıt saydığımızı** tanımlar. Amaç getiri vaadi değil, ölçülebilir bir karar
çerçevesi. Tüzük değişirse tarih ve gerekçe yazılır; sessizce gevşetilmez.

## 1. Durum

- **Canlı işlem yok.** Yalnızca paper. `v1.0.0` backtest sonuçları geri
  çekildi; düzeltilmiş motor trend kurallarında edge göstermiyor.
- Sermaye korunur; risk limitleri (`config/risk.toml`) bu tüzüğün parçasıdır
  ve "işlem geçsin" diye değiştirilmez.

## 2. Piyasa ve enstrüman

| Alan | Kapsam |
|---|---|
| Venue | Bybit USDT perpetual (tek venue) |
| Semboller | BTC, ETH, SOL (ilk aşama); diğerleri ancak likidite kapısı geçerse |
| Veri | 1h ve 15m OHLCV, 8h funding; OI ve order book tarihçesi yok |
| Kaldıraç | En fazla 3x, tek pozisyonda ve portföyde |
| Yön | Long ve short; one-way mode |

## 3. Ufuk tanımları

| Ufuk | Tipik tutuş | Kullanılan sinyal | Ölçüldüğü yer |
|---|---|---|---|
| Intraday | 2–24 saat | 1h opening-range breakout | `docs/EDGES_2026-11-22.md` |
| Swing | 1–5 gün | 1h trend devamı / range fade / sıkışma, 4h rejim | `docs/BACKTEST.md`, `docs/EDGES_2026-11-22.md`, `docs/RESEARCH_2026-11-22.md` |
| Carry | 8 saat–3 gün | 8h funding aşırılığı (tek bacak ve delta-neutral) | `docs/EDGES_2026-11-22.md`, `docs/RESEARCH_2026-11-22.md` |

Scalping (dakikalar) kapsam dışı: 5.5 bps taker + 2 bps kayma ile maliyet
yapısal olarak getiriden büyük.

## 4. Risk bütçesi (değişmez)

| Kural | Değer | Kaynak |
|---|---|---|
| İşlem başına risk | %0.5 equity | `risk_per_trade_pct` |
| Günlük zarar durdurucu | %2 | `daily_loss_limit_pct` |
| Haftalık zarar durdurucu | %5 | `weekly_loss_limit_pct` |
| Maksimum drawdown | %10 | `max_drawdown_pct` |
| Açık pozisyon | 3 | `max_open_positions` |
| Portföy heat | %3 | `max_portfolio_heat_pct` |
| Küme limiti | 2 | `max_cluster_positions` |
| Günlük işlem | 6 | `max_trades_per_day` |
| Minimum R:R | 2.0 | `min_rr` |
| Spread tavanı | 8 bps | `max_spread_bps` |
| Veri yaşı | 120 sn | `max_data_age_sec` |

Bu tablo **hedef değil, tavan**dır. Getiri hedefi aşağıda tanımlı değildir;
çünkü kanıtlanmış edge olmadan getiri hedefi koymak zararı büyütmekten başka
işe yaramaz.

## 5. Kanıt standardı — Tier S (önceden ilan edilir)

Bir strateji "edge var" sayılmak için **hepsini** geçmek zorundadır:

1. **Maliyet sonrası.** 5.5 bps taker + 2 bps kayma + gerçek funding
   düşüldükten sonra pozitif.
2. **Look-ahead yok.** Sinyal kapanmış barda, fill sonraki bar açılışında.
   Pencere sonunda açık pozisyon, pencere içindeki son barda kapanır.
3. **Muhafazakâr belirsizlik.** Aynı barda stop ve hedef varsa stop önce;
   stop boşlukla atlanırsa açılıştan dolar; likidasyon modellenir.
4. **Getiri tabanı.** OOS pencerelerinin ortalaması yıllıklandırıldığında
   **≥ %40** (aylık ~%3). Bu bir hedef değil, kabul tabanıdır.
5. **Out-of-sample.** En az 9 pencere; pencerelerin **≥ 8/9'u pozitif** ve
   ortalama > 0.
6. **Örneklem.** Toplam OOS **≥ 200 işlem** (sembol başına ≥ 30).
7. **Risk.** 10.000 USDT'lik bacakta maksimum DD ≤ **%8** (intrabar).
8. **Dayanıklılık.** En az **5/10 sembolde PF ≥ 1.5** veya seçilmemiş
   semboller üzerinde pozitif; parametre komşuluğunda (±%20) çöküş yok.
9. **Maliyet stresi.** 2× maliyet ve 2× kayma altında hâlâ net pozitif.
10. **Placebo.** Rastgeleleştirilmiş sinyal, gerçek sinyalden anlamlı
    biçimde kötü olmalı.
11. **Çoklu test.** Deneme sayısı kaydedilir; deflated Sharpe veya eşdeğeri
    bir düzeltme olmadan pozitif sonuç kabul edilmez.
12. **Rejim ve kapasite.** Rejim bölmesinde örneklem yeterliyse her rejimde
    ayrı OOS; 5× notional'da edge'in en az yarısı korunmalı.
13. **Gerekçe.** Testten **önce** yazılmış ekonomik neden: bu edge kimin
    zararını kim kazanıyor, ne zaman ölür?

Bu koşullardan biri eksikse sonuç `NO_EDGE` sayılır ve strateji canlıya
geçmez. Tek bir iyi ay geri kalanı kurtarmaz. Tier S'i geçen aday yoksa
doğru karar `NO_TRADE`'dir; barajı düşürmek yasaktır.

## 6. Emeklilik (kill) kriterleri

Bir strateji şu durumlarda emekli edilir:

- 9 walk-forward penceresinin ortalaması maliyet sonrası ≤ 0, veya
- Tier S barajının herhangi bir maddesi iki çeyrek üst üste sağlanmıyorsa, veya
- intrabar DD > %8 (10k bacak), veya
- 2× maliyet altında net negatifse, veya
- canlı/paper kayması (slippage) varsayımı 2 bps'i 3 kat aşıyorsa.

Emekli edilen strateji "iyileştirilir" diye aynı veri üzerinde yeniden
optimize edilmez; yeni hipotez olarak yeniden kaydedilir.

## 7. Promosyon merdiveni

| Aşama | Koşul | Risk |
|---|---|---|
| Research | Bu tüzüğün 5. bölümü | 0 |
| Paper forward | ≥ 100 işlem, gerçek spread/slippage ile | 0 |
| Micro live | Paper'da 3 ay pozitif beklenti + DD < %5 | %0.25 / işlem |
| Normal | Micro live'da 100 işlem, kayma varsayımı doğrulandı | %0.50 / işlem |

Hiçbir aşama limitleri gevşetmez; yalnızca riski kademeli açar.

## 8. Hedef hakkında dürüst not

Eski "kaldıraçsız aylık %3–5" hedefi, hatalı backtest'e dayandığı için **geri
çekildi**. Tier S bu seviyeyi bir **kabul tabanı** olarak yeniden tanımlar:

> Yıllık ≥ %40 (aylık ~%3), DD ≤ %8, OOS ≥ 200 işlem, 8/9 pozitif pencere,
> 2× maliyette net pozitif.

Bu bir vaat değildir. Dokuz hipotez test edildi ve hiçbiri bu tabanı
geçmedi; doğru sonuç `NO_TRADE`. Aylık yüzde ancak Tier S kanıtı geldikten
sonra, ölçülen beklenti üzerinden konuşulur.

## 9. Yönetişim

- Kill switch her an, herkes tarafından çekilebilir.
- Her execute kararı journal'a yazılır; journal'sız işlem yok sayılır.
- Backtest raporları yalnızca gerçek koşulardan üretilir; sayı elle yazılmaz.
- Denetim kaydı: `docs/AUDIT_2026-11-22.md`.
