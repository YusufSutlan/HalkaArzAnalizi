"""
Kazıma ve API durum testleri.  Çalıştırma:  python test_kazima.py

Ağ erişimi gerektirmez. Kaynak sitenin (halkarz.com) ana sayfa ve detay
sayfası yapısını taklit eden küçük örnek HTML'ler kullanılır; şirketler
sahtedir. Kaynak site yapısını değiştirirse önce bu testlerdeki örnekleri
gerçek sayfaya göre güncelleyin, sonra ayrıştırıcıyı düzeltin.

Sınanan:
  - örnek sayfadan alanlar doğru ayrıştırılıyor mu
  - "aktif arz yok" (veri_yok) ile "kaynak hatası" ayrılıyor mu
  - bozuk / boş HTML'de hata durumu dönüyor mu
  - boş sonuç ve hata kısa süre önbelleğe alınıyor mu, aynı anda gelen
    istekler tek taramada birleşiyor mu
"""

import asyncio
import datetime as dt
import logging
import sys

logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient

import proje
from Gecmisveri import GecmisArzlar
from proje import (AYLAR, CacheManager, DataExtractor, GOSTERILEN_DURUMLAR,
                   KaynakHatasi, liste_yaniti_uret)

ok = fail = 0


def check(ad, sonuc, beklenen=True):
    global ok, fail
    if sonuc == beklenen:
        ok += 1
        print(f"  OK   {ad}")
    else:
        fail += 1
        print(f"  FAIL {ad}\n       gelen={sonuc!r}\n       beklenen={beklenen!r}")


# ───────────────────────── örnek HTML ─────────────────────────

_AY_ADI = {no: ad.capitalize() for ad, no in AYLAR.items()}


def _tarih_metni(bas: dt.date, gun_sayisi: int = 2) -> str:
    """'12-13 Aralık 2026' biçimi; ay değişirse tek gün yazılır."""
    son = bas + dt.timedelta(days=gun_sayisi - 1)
    if son.month != bas.month:
        return f"{bas.day} {_AY_ADI[bas.month]} {bas.year}"
    gunler = "-".join(str(bas.day + i) for i in range(gun_sayisi))
    return f"{gunler} {_AY_ADI[bas.month]} {bas.year}"


GUNCEL_TARIH = _tarih_metni(dt.date.today() + dt.timedelta(days=5))
ESKI_TARIH = "11-12 Ocak 2024"
URL_GUNCEL = "https://halkarz.com/ornek-enerji-a-s/"
URL_ESKI = "https://halkarz.com/eski-gida-a-s/"


def _kart(kod: str, ad: str, url: str, tarih: str) -> str:
    # Kaynak sitenin ana sayfa kartıyla aynı iskelet (article.index-list).
    return f"""
<li><article class="index-list"><div class="il-badge"></div>
<a href="{url}" title="{ad}"><img class="slogo" src="x.jpg"/></a>
<div class="il-content"><span class="il-bist-kod">{kod}</span>
<h3 class="il-halka-arz-sirket"><a href="{url}" title="{ad}">{ad}</a></h3>
<span class="il-halka-arz-tarihi"><time datetime="{tarih}">{tarih}</time></span>
</div></article></li>"""


def ana_sayfa(*kartlar: str) -> str:
    return f"<html><body><ul class='halka-arz-list'>{''.join(kartlar)}</ul></body></html>"


KART_GUNCEL = _kart("ORNEK", "Örnek Enerji A.Ş.", URL_GUNCEL, GUNCEL_TARIH)
KART_ESKI = _kart("ESKIG", "Eski Gıda A.Ş.", URL_ESKI, ESKI_TARIH)

DETAY_GUNCEL = f"""
<html><body><h1>Örnek Enerji A.Ş.</h1>
<table class="sp-table">
<tr><td><em>Halka Arz Tarihi : </em></td><td><time datetime="{GUNCEL_TARIH}">{GUNCEL_TARIH}</time></td></tr>
<tr><td><em>Halka Arz Fiyatı/Aralığı : </em></td><td><strong>25,52 TL</strong></td></tr>
<tr><td><em>Dağıtım Yöntemi : </em></td><td><strong>Eşit Dağıtım</strong></td></tr>
<tr><td><em>Pay : </em></td><td><strong>87.500.000 Lot</strong></td></tr>
<tr><td><em>Aracı Kurum : </em></td><td><strong>Örnek Yatırım Menkul Değerler A.Ş.</strong></td></tr>
<tr><td><em>Bist Kodu : </em></td><td><strong>ORNEK</strong></td></tr>
<tr><td><em>Pazar : </em></td><td><strong>Yıldız Pazar</strong></td></tr>
</table>
<ul class="aex-in"><div class="aexi-title">Özet Bilgiler</div>
<li><h5>Halka Arz Şekli</h5><p>- Sermaye Artırımı : 62.500.000 Lot<br/>- Ortak Satışı : 25.000.000 Lot<br/></p></li>
<li><h5>Halka Arz Satış Yöntemi</h5><p>- Sabit Fiyatla Talep Toplama. <br/></p></li>
<li><h5>Tahsisat Grupları</h5><p>
- 35.000.000 Lot (%40) Yurt İçi Bireysel Yatırımcı <br/>
- 30.625.000 Lot (%35) Yurt İçi Kurumsal Yatırımcı <br/>
- 21.875.000 Lot (%25) Yurt Dışı Kurumsal Yatırımcı <br/></p></li>
</ul></body></html>"""


# ───────────────────────── düzenek ─────────────────────────

def _gecmis() -> GecmisArzlar:
    """Puan alanları da içeren sahte geçmiş: son_tamamlananlar bunları SÜZMELİ."""
    kayitlar = []
    for i in range(25):
        gun = dt.date(2026, 1, 5) + dt.timedelta(days=7 * i)
        kayitlar.append({
            "kod": f"G{i:02d}", "sirket": f"Geçmiş {i} A.Ş.", "talep_bit": gun.isoformat(),
            "islem": (gun + dt.timedelta(days=6)).isoformat(), "arz": 1_000_000_000.0 + i,
            "fiyat": 10.0 + i, "dagitim": "Eşit Dağıtım" if i % 2 else None,
            "katilimci": 500_000 + i if i % 3 else None,
            "kalite": 70.0, "baski_skoru": 40.0, "kisa_vade_skoru": 55.0, "tavan": 3,
            "getiri_20g": 0.1,
        })
    # İleri tarihli (henüz bitmemiş) bir kayıt: listeye girmemeli.
    kayitlar.append({"kod": "GELEC", "sirket": "Gelecek A.Ş.",
                     "talep_bit": (dt.date.today() + dt.timedelta(days=30)).isoformat(),
                     "arz": 1.0, "fiyat": 1.0})
    return GecmisArzlar(kayitlar=kayitlar, ozet={"guncelleme": dt.date.today().isoformat()})


proje.GECMIS = _gecmis()


def cikarici(sayfalar: dict) -> tuple[DataExtractor, dict]:
    """Sahte sayfalarla çalışan DataExtractor ve istek sayacı."""
    ex = DataExtractor()
    sayac = {"istek": 0}

    async def _getir(url, onbellekten=True):
        sayac["istek"] += 1
        await asyncio.sleep(0)  # gerçek ağ gibi kontrolü bırak
        return sayfalar.get(url)

    async def _momentum():
        return None  # EVDS'e gitme

    ex._fetch_url_with_retry = _getir
    ex.bist_momentumu = _momentum
    return ex, sayac


ANA = proje.SETTINGS.BASE_URL


# ───────────────────────── ayrıştırma ─────────────────────────

print("=== Örnek sayfa doğru ayrıştırılıyor ===")
ex, _ = cikarici({ANA: ana_sayfa(KART_GUNCEL, KART_ESKI), URL_GUNCEL: DETAY_GUNCEL})
liste = asyncio.run(ex.analiz_et())
check("yalnızca güncel arz listelenir (eski kart elenir)", [a["sirket"] for a in liste],
      ["Örnek Enerji A.Ş."])
a = liste[0] if liste else {}
check("BIST kodu", a.get("bist_kodu"), "ORNEK")
check("fiyat sayısı", a.get("fiyat_sayi"), 25.52)
check("pay sayısı", a.get("pay_sayisi_sayi"), 87_500_000.0)
check("dağıtım yöntemi", a.get("dagitim_yontemi"), "Eşit Dağıtım")
check("bireysel tahsisat lotu", a.get("bireysel_pay_sayisi"), 35_000_000.0)
check("durum gösterilen durumlardan biri", a.get("durum") in GOSTERILEN_DURUMLAR)
check("talep başlangıcı makine-okunur tarih", bool(a.get("talep_baslangic")))
check("son tarama 'ok' kaydedilir", ex.son_tarama.get("durum"), "ok")

print("=== Aktif arz yok (veri_yok) ===")
ex, _ = cikarici({ANA: ana_sayfa(KART_ESKI)})
check("yalnızca eski kartlar: boş liste, hata DEĞİL", asyncio.run(ex.analiz_et()), [])
check("son tarama 'veri_yok'", ex.son_tarama.get("durum"), "veri_yok")
kod, govde = asyncio.run(liste_yaniti_uret(ex))
check("veri_yok: HTTP 200", kod, 200)
check("veri_yok: status", govde["status"], "veri_yok")
son = govde.get("son_tamamlananlar") or []
check("son tamamlananlar: adet sınırı", len(son), proje.SETTINGS.SON_TAMAMLANAN_ADET)
check("son tamamlananlar: en yeni önce", son[0]["kod"] if son else None, "G24")
check("son tamamlananlar: bitmemiş arz yok", all(s["kod"] != "GELEC" for s in son))
yasak = {"kalite", "baski_skoru", "kisa_vade_skoru", "tavan", "getiri_20g", "skor", "yildiz"}
check("son tamamlananlar: puan/yorum alanı YOK", any(yasak & set(s) for s in son), False)
check("son tamamlananlar: yalnızca izinli alanlar",
      all(set(s) <= set(GecmisArzlar.SON_ARZ_ALANLARI) for s in son))
check("son tamamlananlar: dağıtım yoksa anahtar hiç yazılmaz (G24)",
      "dagitim" in son[0] if son else None, False)
check("son tamamlananlar: dağıtım varsa yazılır (G23)",
      son[1].get("dagitim") if len(son) > 1 else None, "Eşit Dağıtım")

print("=== Kaynak hatası ===")
for ad, sayfalar in [
    ("ana sayfa alınamadı (None)", {}),
    ("bozuk HTML (arz kartı yok)", {ANA: "<html><body><p>Bakım çalışması</p></body></html>"}),
    ("yarım kalmış HTML", {ANA: "<html><body><div class='il-content'><span>ORNEK"}),
    ("güncel kart var, detay sayfası okunamadı", {ANA: ana_sayfa(KART_GUNCEL)}),
]:
    ex, _ = cikarici(sayfalar)
    try:
        asyncio.run(ex.analiz_et())
        hata = None
    except KaynakHatasi as e:
        hata = e
    check(f"{ad}: KaynakHatasi", hata is not None)
    kod, govde = asyncio.run(liste_yaniti_uret(ex))
    check(f"{ad}: HTTP 503 + status kaynak_hatasi", (kod, govde["status"]), (503, "kaynak_hatasi"))
    check(f"{ad}: son tarama kaynak_hatasi", ex.son_tarama.get("durum"), "kaynak_hatasi")


class _Patlayan(DataExtractor):
    async def analiz_et(self, debug=False):
        raise ValueError("beklenmeyen")


kod, govde = asyncio.run(liste_yaniti_uret(_Patlayan()))
check("beklenmeyen hata: 500 + sunucu_hatasi (kaynak hatasından ayrı)",
      (kod, govde["status"]), (500, "sunucu_hatasi"))

# ───────────────────────── API ucu ─────────────────────────

print("=== API ucu (/api/halkarzlar) ===")
istemci = TestClient(proje.app)  # lifespan çalışmaz; çıkarıcıyı elle veriyoruz


def _uc(sayfalar):
    proje.CACHE = CacheManager()
    ex, sayac = cikarici(sayfalar)
    proje.app.state.extractor = ex
    return sayac


_uc({ANA: ana_sayfa(KART_GUNCEL), URL_GUNCEL: DETAY_GUNCEL})
r = istemci.get("/api/halkarzlar")
check("ok: 200, status ok, 1 arz", (r.status_code, r.json()["status"], len(r.json()["halka_arzlar"])),
      (200, "ok", 1))
check("ok: uyarı ve meta yanıtta", "uyari" in r.json() and "meta" in r.json())

_uc({ANA: ana_sayfa(KART_ESKI)})
r = istemci.get("/api/halkarzlar")
check("veri_yok: 200 + son_tamamlananlar", (r.status_code, r.json()["status"],
                                             len(r.json()["son_tamamlananlar"]) > 0),
      (200, "veri_yok", True))

_uc({ANA: "<html><body>yapı değişti</body></html>"})
r = istemci.get("/api/halkarzlar")
check("kaynak_hatasi: 503 + açıklama + Retry-After",
      (r.status_code, r.json()["status"], bool(r.json().get("detail")), "retry-after" in r.headers),
      (503, "kaynak_hatasi", True, True))
check("kaynak_hatasi: Türkçe karakterli JSON (charset)", "utf-8" in r.headers["content-type"])

# ───────────────────────── önbellek ─────────────────────────

print("=== Önbellek ===")
sayac = _uc({ANA: ana_sayfa(KART_ESKI)})
for _ in range(3):
    istemci.get("/api/halkarzlar")
check("veri_yok önbelleğe alınır: 3 istek, kaynağa 1 istek", sayac["istek"], 1)

sayac = _uc({})
for _ in range(3):
    istemci.get("/api/halkarzlar")
check("kaynak hatası kısa süre önbelleğe alınır: kaynağa 1 istek", sayac["istek"], 1)

check("süreler: ok > veri_yok > hata",
      CacheManager.sure("ok") > CacheManager.sure("veri_yok") > CacheManager.sure("kaynak_hatasi"))


async def _sure_testi():
    simdi = [1000.0]
    c = CacheManager(saat=lambda: simdi[0])
    ex, sayac = cikarici({ANA: ana_sayfa(KART_ESKI)})

    async def uret():
        return await liste_yaniti_uret(ex)

    await c.al_veya_uret(uret)
    simdi[0] += proje.SETTINGS.BOS_CACHE_TTL - 1
    await c.al_veya_uret(uret)
    once = sayac["istek"]
    simdi[0] += 2
    await c.al_veya_uret(uret)
    return once, sayac["istek"]


once, sonra = asyncio.run(_sure_testi())
check("veri_yok süresi dolmadan yeniden taranmaz", once, 1)
check("süre dolunca yeniden taranır", sonra, 2)


async def _es_zamanli():
    c = CacheManager()
    ex, sayac = cikarici({ANA: ana_sayfa(KART_GUNCEL), URL_GUNCEL: DETAY_GUNCEL})

    async def uret():
        return await liste_yaniti_uret(ex)

    sonuclar = await asyncio.gather(*[c.al_veya_uret(uret) for _ in range(5)])
    return sayac["istek"], {s[1]["status"] for s in sonuclar}


istek, durumlar = asyncio.run(_es_zamanli())
check("aynı anda 5 istek tek taramada birleşir (ana + 1 detay = 2 istek)", istek, 2)
check("hepsi aynı sonucu alır", durumlar, {"ok"})

print(f"\n{ok} başarılı, {fail} başarısız")
sys.exit(1 if fail else 0)
