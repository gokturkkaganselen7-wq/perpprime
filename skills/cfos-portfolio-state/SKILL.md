---
name: cfos-portfolio-state
description: >-
  Hesap durumunu tek sayfada toplar: equity, peak, drawdown, günlük/haftalık
  P&L, açık risk (heat), pozisyon sayısı, sembol ve korelasyon kümesi dağılımı.
  Yeni işlem kararı vermez; mevcut riskin fotoğrafını çeker.
---

# CFOS Portfolio State

## Amaç

"Şu an ne kadar risk taşıyorum?" sorusunun tek doğru cevabı. Tüm sayılar
`cfos status` ve `cfos positions` çıktısından gelir; elle hesaplanmaz.

## Ne zaman

- Her oturumun ilk komutu.
- Yeni pozisyon açmadan önce (heat ve küme kontrolü).
- Hedge/azalt kararından önce (→ `cfos-exit-management`).
- Günlük review başlangıcında.

## Girdi

```bash
cfos status --json
cfos positions --json
cfos journal --limit 50 --json
```

## Okuma sırası

1. `kill_switch.engaged` — true ise burada dur.
2. `account.equity` ve `account.drawdown_pct`.
3. `account.daily_pnl_pct`, haftalık için `week_start_equity` ile karşılaştır.
4. `account.open_risk / account.equity * 100` → heat.
5. `account.cluster_counts` → küme yoğunluğu.
6. `positions[]` → her pozisyonun stop'u gerçekten var mı?
7. `summary` → expectancy ve profit factor (istatistiksel iddia için ≥20 işlem şartı).

## Sağlık eşikleri

| Metrik | Normal | Dikkat | Kritik |
|---|---|---|---|
| Drawdown | < %5 | %5-10 | ≥ %10 (kapı kapalı) |
| Heat | < %1.5 | %1.5-3 | > %3 (kapı kapalı) |
| Açık pozisyon | 0-2 | 3 | > 3 (kapı kapalı) |
| Küme pozisyonu | 0-1 | 2 | > 2 (kapı kapalı) |
| Günlük P&L | > -%1 | -%1…-%2 | ≤ -%2 (kapı kapalı) |

## Karar kuralları

1. Heat %2.5 üzerindeyse yeni işlem önerilmez; önce en zayıf pozisyon kapatılır.
2. Aynı kümede 2 pozisyon varsa üçüncü sembol adayı elenir.
3. Stop'u olmayan pozisyon varsa **acil** kabul edilir: `cfos close` önerilir.
4. Paper equity ile journal equity tutmuyorsa `cfos tick` ile senkronize et.
5. 20'den az kapanmış işlem varsa win-rate üzerinden istatistiksel iddia kurma.

## Çıktı sözleşmesi

```json
{
  "equity": 9945.16,
  "drawdown_pct": 0.55,
  "heat_pct": 0.0,
  "open_positions": 0,
  "clusters": {},
  "risk_posture": "NORMAL",
  "notes": ["3 işlem hakkı kaldı", "cooldown yok"]
}
```

## Guardrails

- Bu skill emir açmaz; yalnızca `cfos close` gibi azaltıcı yönlendirme yapar.
- Hesap dışı (başka borsa/cüzdan) bakiyeyi hesaba katma; CFOS yalnızca
  yapılandırılmış venue'yu görür.
