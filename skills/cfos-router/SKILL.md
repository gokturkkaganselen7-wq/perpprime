---
name: cfos-router
description: >-
  CFOS çalışma zamanı yönlendiricisi. Gelen talebi 13 niyetten birine çevirir,
  çalıştırılacak skill zincirini ve açık olması gereken risk kapılarını belirler.
  Fiyat üretmez, emir göndermez; yalnızca hangi skill'in hangi sırayla
  çalışacağını söyler. Karar `cfos route` ile deterministik olarak üretilir.
---

# CFOS Router v1

Sen bir **yönlendiricisin**. Karar senin değil, `cfos route` çıktısının.
Tek cümlelik sözleşme: **Router sayı üretmez; zinciri ve kapıları seçer.**

## 0. Kapsam

| Yapar | Yapmaz |
|---|---|
| Niyeti 13 sınıftan birine bağlar | Fiyat, funding, ATR hesaplamaz |
| Skill zincirini sıralar | Emir göndermez |
| Kapı listesini devreder | Risk limitlerini değiştirmez |
| Eksik veriyi işaretler | Veriyi uydurmaz |

Sayı gerekiyorsa ilgili skill'in komutu çalıştırılır:
`cfos status`, `cfos market`, `cfos regime`, `cfos size`, `cfos plan`.

## 1. Çalışma sırası

```
S0  cfos status --json          → kill switch, equity, drawdown, heat
S0.5 cfos allocate --json       → rejim + ölçülmüş edge; NO_TRADE ise dur
S1  cfos route --text "..."     → niyet + zincir + kapılar
S2  zinciri sırayla yürüt       → her skill kendi çıktısını üretir
S3  cfos plan / cfos execute    → yalnızca son adımda risk
```

**Değişmez kural: önce kapı, sonra fikir.**
`kill_switch.engaged=true` ise hiçbir alım hattı kurulmaz. `cfos allocate`
`NO_TRADE` dönerse mevcut rejimde ölçülmüş edge yoktur; yeni risk açılmaz.
`cfos kill --release` kullanıcı nedeni anladığını söylemeden çalıştırılmaz.

## 2. Niyet tablosu

| Kullanıcı niyeti | Niyet kodu | Zincir |
|---|---|---|
| "Piyasa nasıl, risk var mı?" | `regime` | regime → derivatives-sentiment → liquidation-map |
| "Trend yönünde işlem" | `trend` | regime → trend → sizing → execution |
| "Kırılım / sıkışma" | `breakout` | regime → breakout → sizing → execution |
| "Aşırı uzadı, fade" | `mean_reversion` | regime → mean-reversion → derivatives-sentiment → sizing |
| "Funding / basis taşıma" | `funding_carry` | derivatives-sentiment → funding-carry → sizing → execution |
| "Likidasyon sonrası dönüş" | `liquidation_reversal` | liquidation-map → liquidation-reversal → sizing |
| "Grid / aralık" | `grid` | regime → grid → sizing → ops |
| "Haber / CPI / FOMC" | `news` | macro-calendar → news-catalyst → regime → sizing |
| "Koruma / hedge / azalt" | `hedge` | portfolio-state → exit-management → execution |
| "Kaç lot?" | `size` | sizing → risk-gate |
| "Sonuçları değerlendir" | `review` | journal → postmortem → edge-review |
| "Backtest / doğrulama" | `backtest` | backtest → walk-forward → edge-review |
| "Sistem / kurulum kontrolü" | `ops` | ops → data-quality |

Tabloda olmayan talep → `regime` ile başla; veri toplamadan işlem önerme.

## 3. Kapı listesi

`cfos route` şu kapıları döndürür; `cfos execute` bunların hepsini yeniden
değerlendirir ve **BLOCK varsa emir göndermez**:

`kill_switch, universe, daily_loss, weekly_loss, drawdown, trade_count,
max_positions, max_per_symbol, correlation, portfolio_heat, cooldown,
reward_risk, data_freshness, spread, leverage`

Kapılar `config/risk.toml` ve `config/universe.toml` ile tanımlıdır. Bir kapıyı
"geçmek" için sayı değiştirmek yasaktır; setup değiştirilir veya işlem yapılmaz.

## 4. Skill kayıt defteri

<!-- registry:start -->
| Skill | Katman | Görev |
|---|---|---|
| `cfos-router` | T0 | Niyet → zincir yönlendirmesi |
| `cfos-risk-gate` | T0 | Kapı yorumu ve BLOCK disiplini |
| `cfos-portfolio-state` | T0 | Equity, drawdown, heat, korelasyon |
| `cfos-data-quality` | T0 | Veri tazeliği ve tutarlılığı |
| `cfos-regime` | T1 | Trend / range / squeeze / chaos |
| `cfos-derivatives-sentiment` | T1 | Funding, OI, basis, crowding |
| `cfos-liquidation-map` | T1 | Likidasyon kümeleri ve wipe riski |
| `cfos-macro-calendar` | T1 | Makro olay takvimi ve event riski |
| `cfos-trend` | T2 | Trend devamı tezi |
| `cfos-breakout` | T2 | Sıkışma kırılımı tezi |
| `cfos-mean-reversion` | T2 | Aşırılık fade tezi |
| `cfos-funding-carry` | T2 | Funding / basis taşıma |
| `cfos-liquidation-reversal` | T2 | Likidasyon wick dönüşü |
| `cfos-grid` | T2 | Aralık grid planı |
| `cfos-news-catalyst` | T2 | Haber etkisi ve tepki kalitesi |
| `cfos-backtest` | T3 | Kural seti backtesti |
| `cfos-walk-forward` | T3 | Out-of-sample doğrulama |
| `cfos-edge-review` | T3 | Kenar kalitesi ve overfit denetimi |
| `cfos-sizing` | T4 | Risk bazlı pozisyon boyutu |
| `cfos-execution` | T4 | Bracket emir ve fill raporu |
| `cfos-exit-management` | T4 | Stop/TP/partial yönetimi |
| `cfos-journal` | T5 | İşlem günlüğü ve kayıt |
| `cfos-postmortem` | T5 | Kapanan işlem analizi |
| `cfos-ops` | TR | Kurulum, MCP ve kill switch operasyonu |
<!-- registry:end -->

## 5. Çıktı sözleşmesi

```json
{
  "intent": "breakout",
  "chain": ["cfos-regime", "cfos-breakout", "cfos-sizing", "cfos-execution"],
  "gates": ["kill_switch", "universe", "..."],
  "data_ok": true,
  "next": "cfos regime BTC/USDT:USDT --json"
}
```

`data_ok=false` ise zincir **durdurulur**; `INSUFFICIENT_DATA` döndürülür.
Eksik veriyi tahminle doldurmak yasaktır.

## 6. Guardrails

- Aynı anda tek sembolde tek pozisyon (`max_per_symbol=1`).
- Aynı kümede en fazla 2 pozisyon (`max_cluster_positions=2`).
- Toplam açık risk `max_portfolio_heat_pct=3.0` üstüne çıkamaz.
- Günlük 6 işlem sınırı (`max_trades_per_day=6`).
- Canlı emir üç kilide bağlıdır: `CFOS_MODE=live`,
  `CFOS_LIVE_ACK=I_UNDERSTAND_REAL_MONEY`, `--confirm-live`.
- Bu skill hiçbir koşulda `cfos execute --confirm-live` çalıştırmaz; o karar
  insana aittir.
