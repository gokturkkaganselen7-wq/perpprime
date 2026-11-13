---
name: cfos-exit-management
description: >-
  Açık pozisyonun çıkışını yönetir: stop taşıma, kısmi kâr alma, TP iptali,
  zaman bazlı çıkış ve rejim bozulmasında kapatma. Yeni risk açmaz; yalnızca
  mevcut riski azaltır.
---

# CFOS Exit Management

## Amaç

Giriş kararı kadar önemli olan çıkış disiplini. Kural: **kaybedeni büyütme,
kazananı erken kesme, rejim bozulunca bekleme.**

## Ne zaman

- Pozisyon TP1'e ulaştığında.
- Stop taşınacaksa (yalnızca kâr yönünde).
- Rejim değiştiğinde (`cfos regime`).
- Haber öncesi risk azaltılacağında.
- Kullanıcı "ne yapayım, tutayım mı?" dediğinde.

## Girdi

```bash
cfos positions --json
cfos status --json
cfos regime BTC/USDT:USDT --tf 4h --json
cfos market BTC/USDT:USDT --json
```

## Çıkış kuralları

| Durum | Aksiyon |
|---|---|
| TP1 doldu | Kalan için stop'u entry'ye çek (breakeven) |
| TP1 doldu, rejim ters döndü | Kalanı kapat |
| Stop'a yaklaşıyor, tez bozuldu | Bekleme; kapat |
| Rejim trend → range | Kârı koru; stop sıkılaştır |
| Rejim → chaos | Kapat; kaldıraç felaket riski |
| Haber T-2 saat | Risk yarıya indir veya kapat |
| Zaman stopu aşıldı | Tez tazelenmiyorsa kapat |

## Stop taşıma kuralları

1. Stop yalnızca kâr yönünde hareket eder; asla geri çekilmez.
2. Yeni stop, likidite bandının içine konmaz.
3. Breakeven, TP1 dolduktan sonra; daha erken değil.
4. Trail: 1.5 ATR mesafe veya son swing; ikisinden sıkı olanı değil, yapısal
   olanı kullan (stop avına dayanıklılık).

## Kısmi kâr

- TP1'de %50 kapat, kalan TP2'ye.
- TP1 tek hedefse tamamını kapat; "biraz bırakayım" kural dışıdır.
- Kısmi kapatma `cfos close SYMBOL --qty N` ile yapılır; sonra kalan qty için
  stop/TP'nin gerçekten güncel olduğu doğrulanır.

## Karar kuralları

1. "Nasılsa döner" gerekçesiyle stop genişletme yasaktır.
2. Rejim `chaos` ise pozisyon boyutu ne olursa olsun kapatılır.
3. Aynı anda hem stop hem TP aynı fiyatta olamaz; mantık hatasıdır.
4. Kısmi kapatma sonrası kalan qty sıfıra yakınsa tamamı kapatılır.
5. Çıkış emri de kapılardan geçer; `cfos close` risk artırmaz ama kill switch
   varsa yine de manuel onay gerekir.

## Çıktı sözleşmesi

```json
{
  "skill": "cfos-exit-management",
  "symbol": "BTC/USDT:USDT",
  "action": "PARTIAL_CLOSE",
  "qty": 0.025,
  "new_stop": 65013.0,
  "reason": "TP1 hit, move stop to breakeven",
  "remaining": 0.025
}
```

## Guardrails

- Bu skill yeni pozisyon açmaz, ortalama düşürmez.
- Stop'u genişletmek yasaktır; tek istisna yoktur.
- Paper'da çıkışı tetiklemek için `cfos tick SYMBOL PRICE` kullanılır.
