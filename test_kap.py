"""KAP modülünü GERÇEK KAP verisiyle test eder (TEB sayfasından alınmıştır)."""

import sys
from kap import (KapIstemci, sayi_coz, donem_coz, sema_sec,
                 kalem_normalize, KapFinansal)

ok = fail = 0
def check(l, g, e):
    global ok, fail
    if g == e: ok += 1; print(f"  OK   {l}: {g}")
    else: fail += 1; print(f"  FAIL {l}\n       got={g!r}\n       exp={e!r}")

print("=== Sayı çözümleme ===")
check("binlik", sayi_coz("421.592.142"), 421592142.0)
check("negatif", sayi_coz("-1.410.374"), -1410374.0)
check("sıfır", sayi_coz("0"), 0.0)
check("ondalıklı", sayi_coz("12.345,67"), 12345.67)
check("boş", sayi_coz("-"), None)
check("None", sayi_coz(None), None)

print("\n=== Dönem çözümleme ===")
check("yıl sonu", donem_coz("2025/12"), (2025, 12))
check("çeyrek", donem_coz("2026/03"), (2026, 3))
check("geçersiz", donem_coz("Konsolide"), None)

# ── GERÇEK VERİ: TEB (kap.org.tr'den çekildi) ──
teb_bilanco = [
    ["Finansal Durum Tablosu (Bilanço)", "2023/12", "2024/12", "2025/12", "2026/03"],
    ["Para Birimi", "1000TL", "1000TL", "1000TL", "1000TL"],
    ["Finansal Tabloların Niteliği", "Konsolide", "Konsolide", "Konsolide", "Konsolide"],
    ["Toplam Varlıklar", "421.592.142", "635.783.902", "797.636.801", "830.595.213"],
    ["Mevduat", "284.567.201", "405.483.606", "509.387.078", "521.070.652"],
    ["Alınan Krediler", "29.594.113", "42.866.676", "67.475.577", "73.727.823"],
    ["Karşılıklar", "6.628.887", "6.183.788", "6.994.594", "7.903.306"],
    ["Özkaynaklar", "37.284.416", "47.766.491", "64.184.547", "70.036.378"],
    ["Ödenmiş Sermaye", "2.204.390", "2.204.390", "2.204.390", "2.204.390"],
]
teb_gelir = [
    ["Kar veya Zarar ve Diğer Kapsamlı Gelir Tablosu", "2023/12", "2024/12", "2025/12", "2026/03"],
    ["Para Birimi", "1000TL", "1000TL", "1000TL", "1000TL"],
    ["Net Faiz Geliri veya Gideri", "18.811.424", "30.794.804", "46.682.082", "14.950.016"],
    ["Net Ücret ve Komisyon Gelirleri veya Giderleri", "5.573.589", "10.794.147", "17.022.434", "5.012.718"],
    ["Faaliyet Brüt Karı", "35.180.116", "41.102.012", "67.332.014", "21.127.291"],
    ["Net Faaliyet Karı (Zararı)", "16.766.503", "16.697.340", "22.814.497", "7.058.737"],
    ["Dönem Net Kar (Zarar)", "13.175.086", "12.538.029", "16.388.329", "5.020.962"],
]

print("\n=== GERÇEK VERİ: TEB (banka şeması) ===")
f = KapIstemci.tablo_ayristir([teb_bilanco, teb_gelir], sektor="FİNANS",
                              sirket_adi="Türk Ekonomi Bankası A.Ş.")
check("şema otomatik BANKA", f.sema, "BANKA")
check("para birimi 1000TL algılandı", f.para_birimi_carpani, 1000.0)
check("son dönem", f.son_donem(), (2026, 3))

g = f.guncel_degerler()
print("\n  Güncel dönem (2026/03), gerçek TL:")
for k, v in sorted(g.items()):
    print(f"    {k:<20} {v:>22,.0f}")
check("özkaynak x1000 uygulandı", g["Ozkaynak"], 70_036_378_000.0)
check("net kâr x1000", g["NetKar"], 5_020_962_000.0)
check("toplam varlık", g["ToplamVarlik"], 830_595_213_000.0)

print("\n  Yıllık seriler (sadece /12 dönemleri):")
ys = f.yillik_seriler()
for k, seri in sorted(ys.items()):
    print(f"    {k:<20} " + "  ".join(f"{y}:{v/1e9:>7,.1f}mlr" for y, v in sorted(seri.items())))
check("çeyrek verisi yıllıktan dışlandı",
      all(2026 not in s for s in ys.values()), True)
check("3 yıllık net kâr serisi", sorted(ys["NetKar"].keys()), [2023, 2024, 2025])

# Büyüme hesabı doğrulaması
kar = ys["NetKar"]
buyume = ((kar[2025] / kar[2023]) ** (1/2) - 1) * 100
print(f"\n  Net kâr YBBO 2023->2025: %{buyume:.1f}")
check("büyüme makul aralıkta", 10 < buyume < 20, True)

print("\n=== Sigorta şeması tespiti (Quick Sigorta senaryosu) ===")
sigorta_tablo = [
    ["Bilanço", "2024/12", "2025/12"],
    ["Para Birimi", "TL", "TL"],
    ["Toplam Varlıklar", "78.900.000.000", "95.000.000.000"],
    ["Özkaynaklar", "10.399.847.058", "23.182.118.750"],
    ["Teknik Karşılıklar", "40.000.000.000", "48.000.000.000"],
    ["Brüt Yazılan Primler", "31.000.000.000", "43.200.000.000"],
    ["Teknik Bölüm Dengesi", "3.100.000.000", "4.900.000.000"],
    ["Dönem Net Karı (Zararı)", "2.400.000.000", "3.800.000.000"],
]
fs = KapIstemci.tablo_ayristir([sigorta_tablo], sektor="FİNANS",
                               sirket_adi="Quick Sigorta A.Ş.")
check("şema SİGORTA olarak tanındı", fs.sema, "SIGORTA")
gs = fs.guncel_degerler()
print("  Çözülen kalemler:", {k: f"{v:,.0f}" for k, v in sorted(gs.items())})
check("özkaynak doğru", gs["Ozkaynak"], 23_182_118_750.0)
check("prim -> Hasilat vekili", gs["YazilanPrim"], 43_200_000_000.0)
check("para birimi TL (çarpan 1)", fs.para_birimi_carpani, 1.0)

print("\n=== Klasik sanayi şeması ===")
sanayi = [
    ["Bilanço", "2024/12", "2025/12"],
    ["Para Birimi", "TL", "TL"],
    ["Hasılat", "2.000.000.000", "3.500.000.000"],
    ["Brüt Kar (Zarar)", "600.000.000", "1.100.000.000"],
    ["Esas Faaliyet Karı (Zararı)", "400.000.000", "800.000.000"],
    ["Dönem Karı (Zararı)", "300.000.000", "650.000.000"],
    ["Dönen Varlıklar", "1.500.000.000", "2.200.000.000"],
    ["Kısa Vadeli Yükümlülükler", "900.000.000", "1.100.000.000"],
    ["Toplam Yükümlülükler", "1.800.000.000", "2.000.000.000"],
    ["Özkaynaklar", "1.200.000.000", "2.100.000.000"],
    ["İşletme Faaliyetlerinden Nakit Akışları", "350.000.000", "700.000.000"],
]
fg = KapIstemci.tablo_ayristir([sanayi], sektor="SANAYİ", sirket_adi="Örnek Beton A.Ş.")
check("şema GENEL", fg.sema, "GENEL")
gg = fg.guncel_degerler()
print("  Çözülen kalemler:", sorted(gg.keys()))
for beklenen in ["Hasilat", "NetKar", "Ozkaynak", "DonenVarlik",
                 "KisaVadeliYukumluluk", "ToplamBorc", "IsletmeNakitAkisi",
                 "FaaliyetKari", "BrutKar"]:
    check(f"  {beklenen} var", beklenen in gg, True)

print("\n=== Şirket adı eşleştirme ===")
dizin = {
    "QUICK": "9999-quick-sigorta-a-s",
    "quick sigorta": "9999-quick-sigorta-a-s",
    "TEB": "2421-turk-ekonomi-bankasi-a-s",
}
ist = KapIstemci(fetch_fn=None)
check("BIST koduyla", ist.sirket_eslestir("Quick Sigorta A.Ş.", "QUICK", dizin),
      "9999-quick-sigorta-a-s")
check("adla (kod yok)", ist.sirket_eslestir("QUICK SİGORTA ANONİM ŞİRKETİ", "", dizin),
      "9999-quick-sigorta-a-s")
check("bulunamayan", ist.sirket_eslestir("Bilinmeyen A.Ş.", "XXXX", dizin), None)

print("\n=== Tanınmayan şema uyarı veriyor mu ===")
bos = KapIstemci.tablo_ayristir([[["Tablo", "2025/12"], ["Alakasız Kalem", "123"]]])
check("uyarı üretildi", len(bos.uyarilar), 1)
check("seri boş", bos.seriler, {})

print(f"\n{'='*58}\nSONUÇ: {ok} başarılı, {fail} başarısız")
sys.exit(1 if fail else 0)