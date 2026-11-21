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
| Swing | 1–5 gün | 1h trend devamı / range fade, 4h rejim | `docs/BACKTEST.md`, `docs/EDGES_2026-11-22.md` |
| Carry | 8 saat–3 gün | 8h funding aşırılığı | `docs/EDGES_2026-11-22.md` |

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

## 5. Kanıt standardı (önceden ilan edilir)

Bir strateji "edge var" sayılmak için **hepsini** geçmek zorundadır:

1. **Maliyet sonrası.** 5.5 bps taker + 2 bps kayma + gerçek funding
   düşüldükten sonra pozitif.
2. **Look-ahead yok.** Sinyal kapanmış barda, fill sonraki bar açılışında.
3. **Muhafazakâr belirsizlik.** Aynı barda stop ve hedef varsa stop önce;
   stop boşlukla atlanırsa açılıştan dolar; likidasyon modellenir.
4. **Out-of-sample.** 90 gün train / 30 gün test, 30 gün adım, en az 9 pencere.
   Pencerelerin **≥ %60'ı pozitif** ve ortalama > 0.
5. **Örneklem.** Sembol başına OOS ≥ 30 işlem; toplam ≥ 100.
6. **Risk.** 10.000 USDT'lik bacakta maksimum DD ≤ %10 (intrabar).
7. **Dayanıklılık.** En az 2/3 sembolde PF ≥ 1.2; parametre komşuluğunda
   çöküş yok (plato, sivri tepe değil).
8. **Gerekçe.** Testten **önce** yazılmış ekonomik neden: bu edge kimin
   zararını kim kazanıyor, ne zaman ölür?

Bu koşullardan biri eksikse sonuç `NO_EDGE` sayılır ve strateji canlıya
geçmez. Tek bir iyi ay geri kalanı kurtarmaz.

## 6. Emeklilik (kill) kriterleri

Bir strateji şu durumlarda emekli edilir:

- 9 walk-forward penceresinin ortalaması maliyet sonrası ≤ 0, veya
- ardışık iki çeyrekte PF < 1.0, veya
- intrabar DD > %10 (10k bacak), veya
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

Eski hedef "kaldıraçsız aylık %3–5" **geçersizdir**. Düzeltilmiş backtest ve
üç alternatif hipotez bu hedefi desteklemiyor. Doğru hedef şudur:

> Maliyet sonrası pozitif beklenti, %10 DD tavanı içinde, en az 100 OOS
> işlemle kanıtlanmış.

Aylık yüzde hedefi ancak bu kanıt geldikten sonra, ölçülen beklenti üzerinden
konuşulur.

## 9. Yönetişim

- Kill switch her an, herkes tarafından çekilebilir.
- Her execute kararı journal'a yazılır; journal'sız işlem yok sayılır.
- Backtest raporları yalnızca gerçek koşulardan üretilir; sayı elle yazılmaz.
- Denetim kaydı: `docs/AUDIT_2026-11-22.md`.
