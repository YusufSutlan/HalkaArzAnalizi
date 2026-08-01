"""
═══════════════════════════════════════════════════════════════════
KAP (Kamuyu Aydınlatma Platformu) ENTEGRASYONU
═══════════════════════════════════════════════════════════════════

NE İŞE YARAR
    halkarz.com finansal tablo yayınlamıyor. Bu modül eksik olan
    kâr, hasılat, özkaynak, borç gibi kalemleri KAP'tan çeker.

ÖNEMLİ KISIT (mutlaka okuyun)
    KAP'ın yapısal finansal sayfası
        kap.org.tr/tr/sirket-finansal-bilgileri/{id}-{slug}
    yalnızca BORSADA İŞLEM GÖREN şirketlerde vardır.

    Halka arz sürecindeki bir şirket henüz BIST şirketi olmadığı için
    bu sayfaya sahip DEĞİLDİR. Yani asıl kullanım durumumuzda
    (talep toplayan arz) bu modül veri BULAMAZ.

    Bu modül şu iki durumda işe yarar:
      1. Arz tamamlanıp şirket borsada işlem görmeye başladıktan sonra
      2. Geriye dönük test (backtest) için: geçmiş arzların bugünkü
         finansallarıyla model kalibrasyonu

    Talep toplama aşamasındaki finansallar KAP'ta yalnızca izahname
    EKİ olarak PDF halindedir ("Ek3 ... Konsolide Finansal Tablolar").
    Bu ayrı bir iştir ve bu modülün kapsamı dışındadır.

TASARIM NOTU
    Sigorta/banka şirketlerinin tablo şeması sanayi şirketinden
    tamamen farklıdır (hasılat yoktur, "Net Faiz Geliri" vardır).
    Bu yüzden kalem eşleştirmesi sektöre duyarlı yazıldı.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# KALEM EŞLEŞTİRME
# ═══════════════════════════════════════════════════════════════════
# KAP'taki satır adı -> bizim FinKey karşılığı.
# Anahtarlar normalize edilmiş (küçük harf, Türkçe-güvenli) halde
# tutulur. Eşleştirme "tam eşleşme önce, sonra içerir" sırasıyla yapılır.

# Klasik (sanayi/ticaret/hizmet) şirket şeması
KALEM_ESLEME_GENEL: dict[str, str] = {
    # Gelir tablosu
    "hasılat": "Hasilat",
    "satış gelirleri": "Hasilat",
    "net satışlar": "Hasilat",
    "brüt kar (zarar)": "BrutKar",
    "brüt kâr (zarar)": "BrutKar",
    "brüt kar": "BrutKar",
    "esas faaliyet karı (zararı)": "FaaliyetKari",
    "esas faaliyet kârı (zararı)": "FaaliyetKari",
    "faaliyet karı (zararı)": "FaaliyetKari",
    "dönem karı (zararı)": "NetKar",
    "dönem kârı (zararı)": "NetKar",
    "net dönem karı (zararı)": "NetKar",
    "net dönem kârı (zararı)": "NetKar",
    "sürdürülen faaliyetler dönem karı (zararı)": "NetKar",
    "finansman giderleri (-)": "FinansmanGideri",
    "finansman giderleri": "FinansmanGideri",
    # Bilanço
    "dönen varlıklar": "DonenVarlik",
    "toplam dönen varlıklar": "DonenVarlik",
    "duran varlıklar": "DuranVarlik",
    "toplam varlıklar": "ToplamVarlik",
    "kısa vadeli yükümlülükler": "KisaVadeliYukumluluk",
    "uzun vadeli yükümlülükler": "UzunVadeliYukumluluk",
    "toplam yükümlülükler": "ToplamBorc",
    "toplam kaynaklar": "ToplamKaynak",
    "özkaynaklar": "Ozkaynak",
    "toplam özkaynaklar": "Ozkaynak",
    "ana ortaklığa ait özkaynaklar": "Ozkaynak",
    "ödenmiş sermaye": "OdenmisSermaye",
    "nakit ve nakit benzerleri": "Nakit",
    # Nakit akış
    "işletme faaliyetlerinden nakit akışları": "IsletmeNakitAkisi",
    "işletme faaliyetlerinden elde edilen nakit akışları": "IsletmeNakitAkisi",
    "amortisman ve itfa gideri ile ilgili düzeltmeler": "Amortisman",
    "amortisman ve itfa giderleri": "Amortisman",
}

# Banka / finansal kuruluş şeması
KALEM_ESLEME_BANKA: dict[str, str] = {
    "net faiz geliri veya gideri": "Hasilat",           # gelir vekili
    "net ücret ve komisyon gelirleri veya giderleri": "KomisyonGeliri",
    "faaliyet brüt karı": "BrutKar",
    "net faaliyet karı (zararı)": "FaaliyetKari",
    "sürdürülen faaliyetler vergi öncesi karı (zararı)": "VergiOncesiKar",
    "dönem net kar (zarar)": "NetKar",
    "net dönem karı (zararı)": "NetKar",
    "toplam aktifler": "ToplamVarlik",
    "toplam varlıklar": "ToplamVarlik",
    "özkaynaklar": "Ozkaynak",
    "ödenmiş sermaye": "OdenmisSermaye",
    "mevduat": "Mevduat",
    "alınan krediler": "FinansalBorc",
    "toplam yükümlülükler": "ToplamBorc",
    "karşılıklar": "Karsiliklar",
}

# Sigorta şirketi şeması
KALEM_ESLEME_SIGORTA: dict[str, str] = {
    "teknik bölüm dengesi": "FaaliyetKari",
    "teknik gelirler": "Hasilat",
    "brüt yazılan primler": "YazilanPrim",
    "kazanılmış primler": "Hasilat",
    "net kazanılmış primler": "Hasilat",
    "dönem net karı (zararı)": "NetKar",
    "dönem karı (zararı)": "NetKar",
    "net dönem karı (zararı)": "NetKar",
    "toplam varlıklar": "ToplamVarlik",
    "toplam aktifler": "ToplamVarlik",
    "özsermaye": "Ozkaynak",
    "özkaynaklar": "Ozkaynak",
    "toplam yükümlülükler": "ToplamBorc",
    "ödenmiş sermaye": "OdenmisSermaye",
    "nakit ve nakit benzeri varlıklar": "Nakit",
    "teknik karşılıklar": "TeknikKarsilik",
}

SEMA_HARITASI: dict[str, dict[str, str]] = {
    "GENEL": KALEM_ESLEME_GENEL,
    "BANKA": KALEM_ESLEME_BANKA,
    "SIGORTA": KALEM_ESLEME_SIGORTA,
}


def kucult(s: Optional[str]) -> str:
    """Türkçe-güvenli küçültme (bkz. proje.py TextUtils.kucult)."""
    return (s or "").replace("İ", "i").lower()


def kalem_normalize(s: Optional[str]) -> str:
    n = kucult(s)
    n = n.replace("(net)", " ").replace("(-)", " ")
    n = re.sub(r"\s+", " ", n)
    return n.strip(" .:")


def sema_sec(sektor: str, kalem_adlari: list[str]) -> str:
    """
    Hangi tablo şemasının kullanılacağını belirler.
    Önce sektör bilgisine, o yetmezse tablodaki kalem adlarına bakar —
    çünkü asıl belirleyici olan tablonun kendi yapısıdır.
    """
    birlesik = " ".join(kalem_normalize(k) for k in kalem_adlari)
    if any(x in birlesik for x in ("teknik bölüm dengesi", "yazılan prim",
                                   "kazanılmış prim", "teknik karşılık")):
        return "SIGORTA"
    if any(x in birlesik for x in ("net faiz geliri", "mevduat", "alınan krediler")):
        return "BANKA"
    if sektor == "FİNANS":
        return "BANKA"
    return "GENEL"


def sayi_coz(ham: Optional[str]) -> Optional[float]:
    """
    '1.234.567' -> 1234567.0 ; '-12.345,67' -> -12345.67 ; '0' -> 0.0
    Boş/çizgi değerler None döner.
    """
    if ham is None:
        return None
    s = str(ham).strip()
    if not s or s in {"-", "—", "n/a"}:
        return None
    negatif = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()-").strip()
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    if "," in s:                       # 1.234.567,89
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") >= 1 and len(s.split(".")[-1]) == 3:
        s = s.replace(".", "")         # 1.234.567 (binlik)
    try:
        d = float(s)
    except ValueError:
        return None
    return -d if negatif else d


def donem_coz(baslik: str) -> Optional[tuple[int, int]]:
    """'2025/12' -> (2025, 12). Yıl/ay olmayan başlıklar None döner."""
    m = re.search(r"(20\d{2})\s*/\s*(\d{1,2})", str(baslik))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


@dataclass
class KapFinansal:
    """Bir şirketin KAP'tan çekilmiş finansal verisi."""
    sirket_adi: str = ""
    kap_id: str = ""
    sema: str = "GENEL"
    para_birimi_carpani: float = 1.0          # "1000TL" ise 1000
    # {kalem_adi: {(yil, ay): deger}}
    seriler: dict[str, dict[tuple[int, int], float]] = field(default_factory=dict)
    uyarilar: list[str] = field(default_factory=list)

    def son_donem(self) -> Optional[tuple[int, int]]:
        tum = {d for s in self.seriler.values() for d in s}
        return max(tum) if tum else None

    def guncel_degerler(self) -> dict[str, float]:
        """En güncel dönemin değerleri (para birimi çarpanı uygulanmış)."""
        d = self.son_donem()
        if not d:
            return {}
        return {
            k: v[d] * self.para_birimi_carpani
            for k, v in self.seriler.items() if d in v
        }

    def yillik_seriler(self) -> dict[str, dict[int, float]]:
        """
        Büyüme hesabı için yıl bazlı seri.
        DİKKAT: Yalnızca 12 aylık (yıl sonu) dönemler alınır. Çeyrek
        verisini yıllıkla karşılaştırmak sahte büyüme/daralma üretir.
        """
        sonuc: dict[str, dict[int, float]] = {}
        for kalem, seri in self.seriler.items():
            yillik = {
                yil: deger * self.para_birimi_carpani
                for (yil, ay), deger in seri.items() if ay == 12
            }
            if len(yillik) >= 2:
                sonuc[kalem] = yillik
        return sonuc


class KapIstemci:
    """
    KAP'tan finansal veri çeker.

    Not: Ağ katmanı dışarıdan enjekte edilir (fetch_fn), böylece hem
    curl_cffi ile üretimde hem de test verisiyle çalışabilir.
    """

    BASE = "https://kap.org.tr"
    SIRKET_LISTESI = f"{BASE}/tr/bist-sirketler"

    def __init__(self, fetch_fn, istek_arasi_bekleme: float = 1.0):
        self._fetch = fetch_fn
        self._bekleme = istek_arasi_bekleme
        self._sirket_dizini: Optional[dict[str, str]] = None

    # ── Şirket eşleştirme ──

    @staticmethod
    def _ad_anahtari(ad: str) -> str:
        """
        'Quick Sigorta A.Ş.' ve 'QUICK SİGORTA ANONİM ŞİRKETİ' aynı
        anahtara indirgensin diye şirket adını sadeleştirir.
        """
        n = kucult(ad)
        for ek in ("anonim şirketi", "a.ş.", "a.s.", "a.ş", "ltd. şti.",
                   "ticaret", "sanayi", "ve", "holding"):
            n = n.replace(ek, " ")
        n = re.sub(r"[^\wçğıöşü ]", " ", n)
        return re.sub(r"\s+", " ", n).strip()

    def sirket_eslestir(self, halkarz_adi: str, bist_kodu: str,
                        dizin: dict[str, str]) -> Optional[str]:
        """
        Önce BIST koduyla (kesin), olmazsa sadeleştirilmiş adla eşleştirir.
        dizin: {bist_kodu veya ad_anahtari: kap_url_slug}
        """
        kod = (bist_kodu or "").strip().upper()
        if kod and kod in dizin:
            return dizin[kod]
        anahtar = self._ad_anahtari(halkarz_adi)
        if anahtar in dizin:
            return dizin[anahtar]
        # Kısmi eşleşme son çare; birden fazla aday varsa eşleştirme yapma
        adaylar = [v for k, v in dizin.items()
                   if anahtar and (anahtar in k or k in anahtar)]
        if len(adaylar) == 1:
            return adaylar[0]
        return None

    # ── Finansal tablo ayrıştırma ──

    @staticmethod
    def tablo_ayristir(tablolar: list[list[list[str]]], sektor: str = "GENEL",
                       sirket_adi: str = "") -> KapFinansal:
        """
        KAP finansal sayfasındaki tabloları KapFinansal'a çevirir.

        tablolar: her tablo -> satır listesi -> hücre listesi.
                  İlk satır başlık (ilk hücre boş, sonrası dönemler).
        Bu imza HTML'den bağımsızdır; böylece ayrıştırma mantığı
        seçicilerden ayrı test edilebilir.
        """
        sonuc = KapFinansal(sirket_adi=sirket_adi)
        tum_kalemler = [s[0] for t in tablolar for s in t if s]
        sonuc.sema = sema_sec(sektor, tum_kalemler)
        esleme = SEMA_HARITASI[sonuc.sema]

        for tablo in tablolar:
            if not tablo:
                continue
            basliklar = tablo[0]
            donemler: dict[int, tuple[int, int]] = {}
            for i, b in enumerate(basliklar[1:], start=1):
                d = donem_coz(b)
                if d:
                    donemler[i] = d
            if not donemler:
                continue

            for satir in tablo[1:]:
                if not satir:
                    continue
                ham_ad = satir[0]
                ad = kalem_normalize(ham_ad)

                # Para birimi çarpanı ("1000TL" -> tüm rakamlar bin TL)
                if "para birimi" in ad or "presentation currency" in ad:
                    for h in satir[1:]:
                        if "1000" in str(h):
                            sonuc.para_birimi_carpani = 1000.0
                            break
                    continue
                if not ad:
                    continue

                hedef = esleme.get(ad)
                if hedef is None:
                    for kap_ad, fk in esleme.items():
                        if kap_ad in ad:
                            hedef = fk
                            break
                if hedef is None:
                    continue

                for idx, donem in donemler.items():
                    if idx >= len(satir):
                        continue
                    deger = sayi_coz(satir[idx])
                    if deger is None:
                        continue
                    sonuc.seriler.setdefault(hedef, {})[donem] = deger

        if not sonuc.seriler:
            sonuc.uyarilar.append(
                "KAP tablosu okundu ancak tanınan finansal kalem bulunamadı; "
                "şirketin tablo şeması beklenenden farklı olabilir."
            )
        return sonuc

    async def finansal_getir(self, kap_slug: str, sektor: str = "GENEL",
                             sirket_adi: str = "") -> Optional[KapFinansal]:
        """
        KAP finansal bilgileri sayfasını indirir ve ayrıştırır.

        NOT: HTML seçici katmanı canlı bir istekle DOĞRULANMADI
        (geliştirme ortamında ağ erişimi yok). İlk çalıştırmada
        `debug=true` ile çıktıyı kontrol edin.
        """
        url = f"{self.BASE}/tr/sirket-finansal-bilgileri/{kap_slug}"
        html = await self._fetch(url)
        if not html:
            return None
        await asyncio.sleep(self._bekleme)
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            tablolar = []
            for tbl in soup.find_all("table"):
                satirlar = []
                for tr in tbl.find_all("tr"):
                    hucreler = [
                        td.get_text(separator=" ", strip=True)
                        for td in tr.find_all(["th", "td"])
                    ]
                    if hucreler:
                        satirlar.append(hucreler)
                if len(satirlar) > 1:
                    tablolar.append(satirlar)
            if not tablolar:
                logger.warning(f"KAP sayfasında tablo bulunamadı: {url}")
                return None
            return self.tablo_ayristir(tablolar, sektor, sirket_adi)
        except Exception as e:
            logger.exception(f"KAP ayrıştırma hatası ({url}): {e}")
            return None


