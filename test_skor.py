"""
Skor motoru testleri.  Çalıştırma:  python test_skor.py

Ağ erişimi gerektirmez. Geçmiş arz verisi yerine sabit, küçük bir örneklem
kullanılır; böylece testler veri dosyası güncellendiğinde bozulmaz.
"""

import asyncio
import datetime as dt
import logging
import sys

logging.disable(logging.CRITICAL)

import proje
from Gecmisveri import GecmisArzlar
from proje import (ArzDurumu, Category, DataExtractor, FinKey, InfoKey,
                   PiyasaBaglami, ScoreAnalyzer, TextUtils)

ok = fail = 0


def check(ad, sonuc, beklenen=True):
    global ok, fail
    if sonuc == beklenen:
        ok += 1
        print(f"  OK   {ad}")
    else:
        fail += 1
        print(f"  FAIL {ad}\n       gelen={sonuc!r}\n       beklenen={beklenen!r}")


def yakin(a, b, tol=0.05):
    return a is not None and abs(a - b) <= tol


# ── Sabit geçmiş: 20 arz, katılım 600 bin, kişi başı 2.000 TL, arz 2 mlr ──
def _gecmis(n=20):
    kayitlar = []
    for i in range(n):
        gun = dt.date(2026, 1, 1) + dt.timedelta(days=7 * i)
        skor = 20 + i * 4
        kayitlar.append({
            "kod": f"T{i:02d}", "talep_bit": gun.isoformat(),
            "arz": 2_000_000_000.0, "fiyat": 20.0, "katilimci": 600_000,
            "kisi_basi_tl": 2_000, "kisa_vade_skoru": skor,
            "tavan": i // 2, "ilk_gun": 0.1 if i > 3 else -0.05,
            "getiri_20g": (i - 5) / 20,
        })
    return GecmisArzlar(kayitlar=kayitlar, ozet={
        "guncelleme": dt.date.today().isoformat(),
        "emsal": {"fk": 30.0, "pddd": 2.0, "n": 14},
        "bist100_medyan": {"fk": 10.0, "pddd": 0.8},
        "finans_medyan": {"fk": 6.0, "pddd": 1.0},
    })


proje.GECMIS = _gecmis()
A = ScoreAnalyzer()
F = FinKey

print("=== Geçmiş arz verisi ===")
g = proje.GECMIS
check("beklenen katılımcı = son 5 arzın medyanı", g.beklenen_katilimci()[0], 600_000)
check("referans kişi başı TL", g.referans_kisi_basi_tl()[0], 2000.0)
check("tarih filtresi: ilk arzdan önce veri yok -> varsayılan",
      g.beklenen_katilimci(dt.date(2025, 12, 1))[1], "varsayılan")
b = g.benzer_arzlar(60)
check("benzer arzlar bulunuyor", b is not None and b["ornek_sayisi"] >= 12)
check("benzer arzlar: düşük skorda medyan tavan daha düşük",
      g.benzer_arzlar(25, en_az=5, pencere=10)["tavan_medyan"]
      < g.benzer_arzlar(90, en_az=5, pencere=10)["tavan_medyan"])
check("yetersiz örnekte None", GecmisArzlar(kayitlar=[], ozet={}).benzer_arzlar(50), None)
check("boş veride varsayılan emsal", GecmisArzlar(kayitlar=[], ozet={}).emsal_carpanlar()["fk"] > 0)

print("=== Metin çözümleme ===")
check("arz şekli: lotlar",
      TextUtils.arz_sekli_coz("Sermaye Artırımı : 49.000.000 Lot\nOrtak Satışı : 7.000.000 Lot (A)\n"
                              "Ortak Satışı : 14.000.000 Lot (B)"),
      (49_000_000.0, 21_000_000.0))
check("arz şekli: lot yok", TextUtils.arz_sekli_coz("Sermaye Artırımı"), (0.0, 0.0))
check("tutar: milyar", TextUtils.tutar_coz("2,7 Milyar TL"), 2_700_000_000.0)
check("tarih aralığı", TextUtils.tarih_araligi_coz("22-23 Temmuz 2026"),
      (dt.date(2026, 7, 22), dt.date(2026, 7, 23)))

print("=== Değerleme ===")
fin = {F.NET_KAR: 100e6, F.OZKAYNAK: 500e6}
d = A.degerleme_carpanlari(3_000e6, fin, "SANAYİ", birincil_tutar=500e6)
c = {x["ad"]: x for x in d["carpanlar"]}
check("F/K = PD / net kâr", c["F/K"]["deger"], 30.0)
check("PD/DD halka arz SONRASI özkaynakla (500+500)", c["PD/DD"]["deger"], 3.0)
check("PD/DD düzeltmesiz daha yüksek olurdu",
      A.degerleme_carpanlari(3_000e6, fin, "SANAYİ")["carpanlar"][1]["deger"], 6.0)
check("F/K emsale yakın", c["F/K"]["yorum"], "Emsallere yakın")
check("PD/DD emsalin üzerinde", c["PD/DD"]["yorum"], "Emsallerin üzerinde")
r = A.degerleme_puanla(3_000e6, fin, "SANAYİ", [], 500e6)
check("değerleme puanı = 10×0,6 + 10×0,2", r.score, 8.0)
check("değerleme ölçülebilen max", r.max_possible, 20.0)

bayrak = []
r = A.degerleme_puanla(3_000e6, {F.NET_KAR: -50e6, F.OZKAYNAK: 500e6}, "SANAYİ", bayrak)
check("zarar: F/K ağırlığı paydada kalır, 0 puan", (r.max_possible, r.score), (20.0, 0.0))
bayrak = []
A.degerleme_puanla(7_000e6, fin, "SANAYİ", bayrak)
check("F/K emsalin 2 katından yüksekse uyarı", any("2 katından" in x for x in bayrak))
d = A.degerleme_carpanlari(1_000e6, {F.NET_KAR: 300e6, F.OZKAYNAK: 1_000e6}, "GYO")
check("GYO: F/K hesaplanmaz", [x["ad"] for x in d["carpanlar"]], ["PD/DD"])
d = A.degerleme_carpanlari(1_200e6, {F.NET_KAR: 200e6, F.OZKAYNAK: 1_000e6}, "FİNANS")
check("finans: kıyas BIST finans medyanı", d["carpanlar"][0]["referans_adi"], "BIST finans medyanı")
check("piyasa değeri yoksa puanlanmaz", A.degerleme_puanla(None, fin, "SANAYİ", []).has_data, False)

print("=== Kârlılık / borç ===")
ceza = {}
r = A.karlilik_puanla({F.NET_KAR: 10e6, F.OZKAYNAK: -5e6, F.HASILAT: 100e6}, [], ceza)
check("negatif özkaynakta ROE ağırlığı paydada", r.max_possible, 14.0)
r = A.karlilik_puanla({F.NET_KAR: 30e6, F.OZKAYNAK: 100e6, F.HASILAT: 100e6,
                       F.FAALIYET_KARI: 25e6}, [], {})
check("güçlü kârlılık tam puan", (r.score, r.max_possible), (20.0, 20.0))
ceza, bayrak = {}, []
A.karlilik_puanla({F.NET_KAR: -1e6, F.OZKAYNAK: 100e6}, bayrak, ceza)
check("zarar cezası ve bayrağı", ("zarar" in ceza, len(bayrak)), (True, 1))
ceza = {}
A.borc_puanla({F.OZKAYNAK: -1.0, F.TOPLAM_BORC: 10.0}, "SANAYİ", [], ceza)
check("negatif özsermaye cezası", ceza.get("negatif_ozsermaye"), 30)

print("=== Büyüme ===")
check("yıllık bileşik büyüme", yakin(A._seri_buyume({2023: 100.0, 2025: 144.0}), 20.0, 0.01))
check("zarardan kâra", A._seri_buyume({2024: -5.0, 2025: 5.0}), 100.0)
check("tek dönem -> None", A._seri_buyume({2025: 5.0}), None)

print("=== Arz yapısı / kurumsallık / fon ===")
r = A.arz_yapisi_puanla("Sermaye Artırımı : 75.000.000 Lot\nOrtak Satışı : 25.000.000 Lot", [], {})
check("arz yapısı orantılı (%75 şirkete)", r.score, 3.8)
ceza, bayrak = {}, []
r = A.arz_yapisi_puanla("Ortak Satışı : 10.000.000 Lot (X)", bayrak, ceza)
check("tamamen ortak satışı", (r.score, ceza.get("ortak_satis")), (0, 15))
ceza, bayrak = {}, []
A.arz_yapisi_puanla("Sermaye Artırımı : 3.000.000 Lot\nOrtak Satışı : 7.000.000 Lot", bayrak, ceza)
check("çoğunluk ortak satışı uyarısı", (ceza.get("ortak_satis"), len(bayrak)), (6, 1))
check("tam sermaye artırımı", A.arz_yapisi_puanla("Sermaye Artırımı : 5.000.000 Lot", [], {}).score, 5.0)
yil = dt.date.today().year
check("kurumsallık: 25 yıl tam puan", A.kurumsallik_puanla("", yil - 25).score, 3.0)
check("kurumsallık: anahtar kelime puan vermez",
      A.kurumsallik_puanla("bağımsız denetim iso sürdürülebilirlik", None).has_data, False)
r = A.fon_kullanim_puanla("%60 Yeni fabrika yatırımı\n%40 İşletme sermayesi", {}, [])
check("fon kullanımı ağırlıklı ortalama", r.score, 8.0)
check("iskonto ağırlığı 2", A.iskonto_puanla("%25").max_possible, 2.0)
check("toplam ağırlık", sum(A.weights.MAX.values()), 116.0)

print("=== Kısa vade modeli ===")


def baski(arz, mom=None, tahsisat="Henüz açıklanmadı.", olasi=None):
    veri = {InfoKey.DAGITIM_YONTEMI: "Eşit Dağıtım", InfoKey.TAHSISAT: tahsisat}
    bag = PiyasaBaglami(bist_mom60=mom)
    return A.ilk_gun_satis_baskisi(veri, arz, None, 20.0, bag, "", olasi), bag


b1, _ = baski(1_200_000_000.0)         # 600 bin kişi -> 2.000 TL = referans
check("referans düzeyinde arz: kişi başı 2.000 TL", b1["kisi_basi_tahmini_tutar"], 2000.0)
check("referans düzeyinde baskı orta", (b1["seviye"], b1["skor"] < 45), ("Orta", True))
b2, _ = baski(6_000_000_000.0)
check("büyük arzda baskı yüksek", b2["skor"] > b1["skor"] + 30 and b2["seviye"] in ("Yüksek", "Çok Yüksek"))
b3, _ = baski(300_000_000.0)
check("küçük arzda baskı düşük", b3["seviye"], "Düşük")
b4, _ = baski(1_200_000_000.0, tahsisat="6.000.000 Lot (%50) Yurt İçi Bireysel Yatırımcı")
check("bireysel tahsisat %50 -> kişi başı yarıya iner", b4["kisi_basi_tahmini_tutar"], 1000.0)
b5, _ = baski(1_200_000_000.0, olasi=[{"katilimci": 500_000, "lot": 100, "tutar": 2000.0},
                                      {"katilimci": 2_000_000, "lot": 25, "tutar": 500.0}])
check("kaynak senaryo tablosu: beklenen katılıma en yakın satır", b5["kisi_basi_tahmini_tutar"], 2000.0)
b0, _ = baski(None)
check("arz bilinmiyorsa baskı belirsiz", b0["seviye"], "Belirsiz")

kv_notr = A.kisa_vade_gostergesi(b1, PiyasaBaglami(bist_mom60=0.10), 55.0)
check("nötr koşullarda gösterge ~50", yakin(kv_notr["skor"], 50.0, 6.0))
kv_iyi = A.kisa_vade_gostergesi(b3, PiyasaBaglami(bist_mom60=0.35), 75.0)
kv_kotu = A.kisa_vade_gostergesi(b2, PiyasaBaglami(bist_mom60=-0.15), 40.0)
check("küçük arz + güçlü piyasa > büyük arz + zayıf piyasa", kv_iyi["skor"] > kv_kotu["skor"] + 40)
check("gösterge 3–97 aralığında", 3.0 <= kv_kotu["skor"] and kv_iyi["skor"] <= 97.0)
m_yok = A.kisa_vade_gostergesi(b1, PiyasaBaglami(bist_mom60=None), 55.0)
check("momentum bilinmiyorsa nötr ve işaretli",
      [e["etki"] for e in m_yok["etkenler"] if e["ad"] == "Piyasa momentumu"], ["bilinmiyor"])
check("momentum artınca gösterge artar",
      A.kisa_vade_gostergesi(b1, PiyasaBaglami(bist_mom60=0.30), 55.0)["skor"]
      > A.kisa_vade_gostergesi(b1, PiyasaBaglami(bist_mom60=0.0), 55.0)["skor"])
check("ölçülemiyorsa benzer arz verilmez",
      A.kisa_vade_gostergesi(b0, PiyasaBaglami(), 55.0)["benzer_arzlar"], None)
check("etken puanları toplamı skoru verir",
      yakin(50 + sum(e["puan"] for e in kv_iyi["etkenler"]), kv_iyi["skor"], 0.3)
      or kv_iyi["skor"] in (3.0, 97.0))

print("=== Uçtan uca puanlama ===")
veri = {
    InfoKey.FIYAT: "20,00 TL", InfoKey.PAY_SAYISI: "60.000.000 Lot", InfoKey.ACIKLIK: "%25",
    InfoKey.ISKONTO: "%20", InfoKey.TAAHHUT: "1 Yıl, İhraççı.",
    InfoKey.HALKA_ARZ_SEKLI: "Sermaye Artırımı : 45.000.000 Lot\nOrtak Satışı : 15.000.000 Lot",
    InfoKey.FON_KULLANIM: "%70 Yeni tesis yatırımı\n%30 İşletme sermayesi",
    InfoKey.FIYAT_ISTIKRARI: "30 gün", InfoKey.DAGITIM_YONTEMI: "Eşit Dağıtım",
    InfoKey.TAHSISAT: "Henüz açıklanmadı.", InfoKey.BUYUKLUK: "1,2 Milyar TL",
}
fin = {F.NET_KAR: 160e6, F.OZKAYNAK: 900e6, F.HASILAT: 1_500e6, F.FAALIYET_KARI: 260e6,
       F.DONEN_VARLIK: 800e6, F.KISA_VADELI_YUKUMLULUK: 500e6, F.TOPLAM_BORC: 700e6,
       F.ISLETME_NAKIT_AKISI: 180e6, F.FINANSMAN_GIDERI: 40e6}
seri = {F.HASILAT: {2024: 1_100e6, 2025: 1_500e6}, F.NET_KAR: {2024: 120e6, 2025: 160e6}}
s = A.skoru_topla(veri, fin, seri, ArzDurumu.TALEP_TOPLANIYOR, "Örnek Makine Sanayi 2001 yılında kuruldu",
                  PiyasaBaglami(bist_mom60=0.12), kurulus_yili=2001, sirket_adi="Örnek Makine Sanayi A.Ş.")
check("skor 0–100", 0 <= s["temel_kalite"] <= 100)
check("sağlam şirket yüksek puan alır", s["temel_kalite"] >= 65)
check("piyasa değeri = arz / açıklık", s["degerleme"]["piyasa_degeri"], 4_800_000_000.0)
check("veri kapsamı yüksek, skor güvenilir", (s["veri_guvenilirligi"] >= 80, s["skor_guvenilir"]), (True, True))
check("kısa vade ölçülebilir", s["kisa_vade"]["olculebilir"])
check("tavan_potansiyeli = kısa vade skoru", s["tavan_potansiyeli"], s["kisa_vade"]["skor"])
check("çıktı JSON'a çevrilebiliyor", bool(__import__("json").dumps(s, default=str, allow_nan=False)))

kotu = dict(fin)
kotu.update({F.NET_KAR: -80e6, F.FAALIYET_KARI: -20e6, F.ISLETME_NAKIT_AKISI: -50e6,
             F.KISA_VADELI_YUKUMLULUK: 1_200e6, F.TOPLAM_BORC: 4_000e6})
k = A.skoru_topla(veri, kotu, {}, ArzDurumu.TALEP_TOPLANIYOR, "", PiyasaBaglami(bist_mom60=0.12),
                  sirket_adi="Örnek Makine Sanayi A.Ş.")
check("zarar eden, borçlu şirket düşük puan alır", k["temel_kalite"] < 35)
check("riski daha yüksek", k["risk"] > s["risk"] + 30)
check("kırmızı bayraklar üretildi", len(k["kirmizi_bayraklar"]) >= 4)

h = A.skoru_topla({}, {}, {}, ArzDurumu.HAZIRLANIYOR, "", PiyasaBaglami())
check("hazırlık aşamasında puan yok", (h["temel_kalite"], h["kisa_vade"]["olculebilir"]), (0.0, False))
yalniz_yapi = A.skoru_topla(veri, {}, {}, ArzDurumu.TALEP_TOPLANIYOR, "", PiyasaBaglami())
check("finansal veri yoksa işaretlenir",
      (yalniz_yapi["finansal_veri_var"], yalniz_yapi["skor_guvenilir"]), (False, False))

print("=== Sayfa ayrıştırma ===")
SAYFA = """<html><body>
<table class="sp-table">
<tr><td>Halka Arz Tarihi :</td><td>Hazırlanıyor...</td></tr>
<tr><td>Halka Arz Fiyatı/Aralığı :</td><td>Hazırlanıyor...</td></tr>
<tr><td>Pay :</td><td>72.814.782 Lot</td></tr>
</table>
<ul class="aex-in">
<li><h5>Fiyat İstikrarı</h5><p>- *** gün.<small>* Taslak İzahname, Sayfa 244.</small></p></li>
<li><h5>Halka Açıklık</h5><p>- %10.</p></li>
</ul>
<h3>Fiyat İstikrarı</h3><div>*** gün.</div><div>* Taslak İzahname, Sayfa 244.</div>
</body></html>"""
ex = DataExtractor(A)
ex.finansal_depo = None


async def _sahte(_url):
    return SAYFA
ex._fetch_url_with_retry = _sahte
p = asyncio.run(ex._sirket_parse("Taslak Enerji A.Ş.", "https://ornek", "taslak enerji a.ş."))
check("taslak arzda 'Fiyat İstikrarı' metni fiyat sanılmaz", p["fiyat"], None)
check("taslak arzda arz büyüklüğü uydurulmaz", p["arz_buyuklugu"], None)
check("pay sayısı okunur", p["pay"], 72_814_782.0)

print("=== Endeks (EVDS) ===")
import os
import Endeksveri
seri = [(dt.date(2026, 1, 1) + dt.timedelta(days=i), 100.0 + i) for i in range(80)]
check("momentum: son kapanış / 60 işlem günü önceki - 1",
      yakin(Endeksveri.momentum(seri), 179.0 / 119.0 - 1, 1e-9))
check("momentum: tarih verilince o günden ÖNCEKİ veri kullanılır",
      yakin(Endeksveri.momentum(seri, dt.date(2026, 3, 11)), 168.0 / 108.0 - 1, 1e-9))
check("momentum: yetersiz veri -> None", Endeksveri.momentum(seri[:50]), None)
check("momentum: bariz veri hatası -> None",
      Endeksveri.momentum([(d, 1.0) for d, _ in seri[:20]] + [(d, 1000.0) for d, _ in seri[20:]]), None)
eski = os.environ.pop("EVDS_API_KEY", None)
m, gun, durum = Endeksveri.guncel_momentum()
check("anahtar yoksa hata fırlatmaz, None ve açıklama döner",
      (m, gun, "EVDS_API_KEY" in durum), (None, None, True))
if eski is not None:
    os.environ["EVDS_API_KEY"] = eski

print(f"\n{ok} başarılı, {fail} başarısız")
sys.exit(1 if fail else 0)
