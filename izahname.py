"""
═══════════════════════════════════════════════════════════════════
İZAHNAME PDF FİNANSAL TABLO ÇIKARICI
═══════════════════════════════════════════════════════════════════

NEDEN BU YOL
    halkarz.com finansal tablo yayınlamıyor. KAP'ın yapısal finansal
    sayfası ise yalnızca BORSADA İŞLEM GÖREN şirketlerde var; halka
    arz aşamasındaki şirkette yok. Talep toplama sırasında şirketin
    GERÇEK rakamlarına ulaşmanın tek yolu izahname PDF'i.

TASARIM KARARI: TABLO DEĞİL, SATIR ÇIKARIMI
    İlk akla gelen yöntem camelot/tabula ile tablo çıkarmaktır ancak
    SPK finansal tablolarında hücre çizgisi YOKTUR. Test edildi:
        pdfplumber.extract_table()  -> None
        text stratejisi             -> başlıklar bölünüyor, kaymalar var
        extract_text() satırları    -> TEMİZ
    Finansal tablolar "Kalem  Değer1  Değer2" biçiminde düzenli
    olduğu için satır bazlı çıkarım bu formatta tablo çıkarımından
    daha güvenilir. Tablo çıkarımı yalnızca yedek olarak kullanılıyor.

KATMANLAR
    1. Sayfa bulma      : Binlerce sayfalık izahnamede bilanço/gelir
                          tablosu/nakit akış sayfalarını bulur
    2. Satır çıkarımı   : Kalem adı + dönem değerleri
    3. Doğrulama        : Bilanço denkliği, işaret ve büyüklük kontrolü
    4. LLM yedeği       : Yalnızca 1-3 başarısız olursa (opsiyonel)

LLM HAKKINDA NOT
    Yapay zekâ ile okuma güçlü bir yedek ama finansal rakamda
    halüsinasyon riski taşır. Bu yüzden burada BİRİNCİL yöntem değil;
    yedek olarak konumlandırıldı ve çıktısı aynı doğrulama
    katmanından geçirilir. Doğrulamayı geçmeyen veri KULLANILMAZ.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. SABİTLER
# ═══════════════════════════════════════════════════════════════════

def kucult(s: Optional[str]) -> str:
    """Türkçe-güvenli küçültme. Bkz. proje.py TextUtils.kucult."""
    return (s or "").replace("İ", "i").lower()


def sadelestir(s: Optional[str]) -> str:
    """
    Türkçe karakterleri ASCII'ye indirger ve boşlukları normalize eder.
    İzahnamelerin bir kısmı Türkçe karakterleri bozuk gömüyor
    ("Hasılat" yerine "Hasilat" veya "Has›lat"), bu yüzden karşılaştırma
    sadeleştirilmiş biçim üzerinden yapılıyor.
    """
    n = kucult(s)
    # Türkçe + sık görülen bozuk kodlamalar + OCR'ın ürettiği aksanlı
    # varyantlar. OCR "Dönem" kelimesini "Dénem", "Dônem", "D6nem"
    # gibi okuyabildiği için bunların hepsi aynı biçime indirgenir.
    for a, b in (("ı","i"),("ş","s"),("ğ","g"),("ü","u"),("ö","o"),("ç","c"),
                 ("›","i"),("þ","s"),("ð","g"),("ý","i"),
                 ("é","o"),("è","o"),("ê","o"),("ô","o"),("ó","o"),
                 ("à","a"),("â","a"),("á","a"),("ä","a"),
                 ("î","i"),("ï","i"),("í","i"),("ì","i"),
                 ("û","u"),("ù","u"),("ú","u"),
                 ("ñ","n")):
        # NOT: OCR bazen "ö" yerine "6" okur ama "6" -> "o" dönüşümü
        # içinde 6 geçen TÜM SAYILARI bozar (31/03/2026 -> 31/03/2o26).
        # Bu tür harf hataları bulanık eşleştirmeye bırakıldı.
        n = n.replace(a, b)
    n = re.sub(r"[^\w%().,\-/ ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _ocr_benzerlik(a: str, b: str) -> float:
    """
    İki kalem adı arasındaki benzerlik (0-1).
    OCR harf düşürüp ekleyebildiği için tam eşleşme yetmez;
    difflib ile bulanık eşleştirme yapılır.
    """
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


# Tablo türü -> sayfayı tanıtan başlık kalıpları
TABLO_IMZALARI: dict[str, list[str]] = {
    "bilanco": [
        "finansal durum tablosu", "bilanco", "konsolide bilanco",
        "finansal durum tablolari",
    ],
    "gelir": [
        "kar veya zarar tablosu", "kar zarar tablosu",
        "gelir tablosu", "kapsamli gelir tablosu",
        "kar veya zarar ve diger kapsamli gelir",
    ],
    "nakit": [
        "nakit akis tablosu", "nakit akislari tablosu",
    ],
}

# Sayfanın gerçekten finansal tablo olduğunu doğrulayan kalemler
# (sadece başlığa güvenmek yetmez; içindekiler tablosu da eşleşir)
TEYIT_KALEMLERI: dict[str, list[str]] = {
    "bilanco": ["toplam varliklar", "ozkaynaklar", "donen varliklar",
                "toplam kaynaklar", "kisa vadeli yukumlulukler"],
    "gelir": ["hasilat", "brut kar", "donem kari", "esas faaliyet",
              "satislarin maliyeti", "net faiz geliri", "yazilan prim"],
    "nakit": ["isletme faaliyetlerinden", "yatirim faaliyetlerinden",
              "amortisman"],
}

# Kalem adı kalıpları -> standart alan adı.
# Sıra ÖNEMLİ: daha spesifik kalıplar üstte olmalı.
KALEM_KALIPLARI: list[tuple[str, str]] = [
    # --- Gelir tablosu ---
    (r"^hasilat$|^net satislar|^satis gelirleri|^kazanilmis prim|^net kazanilmis prim", "Hasilat"),
    (r"^brut yazilan prim|^brut prim", "YazilanPrim"),
    (r"^brut (kar|esas faaliyet kari)", "BrutKar"),
    (r"^esas faaliyet kari|^faaliyet kari|^teknik bolum dengesi|^net faaliyet kari", "FaaliyetKari"),
    (r"^net faiz geliri", "NetFaizGeliri"),
    (r"finansman gider", "FinansmanGideri"),
    (r"^(surdurulen faaliyetler )?vergi oncesi kar", "VergiOncesiKar"),
    (r"^donem kari|^donem net kari|^net donem kari|^donem (net )?kar[ıi]? \(zarar", "NetKar"),
    (r"^satislarin maliyeti", "SatisMaliyeti"),
    # --- Bilanço ---
    (r"^toplam donen varlik|^donen varliklar$", "DonenVarlik"),
    (r"^toplam duran varlik|^duran varliklar$", "DuranVarlik"),
    (r"^toplam varliklar|^toplam aktif", "ToplamVarlik"),
    (r"^toplam kaynaklar|^toplam pasif", "ToplamKaynak"),
    (r"^kisa vadeli yukumlulukler$|^toplam kisa vadeli", "KisaVadeliYukumluluk"),
    (r"^uzun vadeli yukumlulukler$|^toplam uzun vadeli", "UzunVadeliYukumluluk"),
    (r"^toplam yukumlulukler", "ToplamBorc"),
    (r"^odenmis sermaye|^cikarilmis sermaye", "OdenmisSermaye"),
    (r"^ozkaynaklar$|^toplam ozkaynak|^ozsermaye$|^ana ortakliga ait ozkaynak", "Ozkaynak"),
    (r"^nakit ve nakit benzer", "Nakit"),
    (r"^(toplam )?finansal borc|^banka kredileri", "FinansalBorc"),
    (r"^teknik karsilik", "TeknikKarsilik"),
    # --- Nakit akış ---
    (r"^isletme faaliyetlerinden", "IsletmeNakitAkisi"),
    (r"^yatirim faaliyetlerinden", "YatirimNakitAkisi"),
    (r"amortisman", "Amortisman"),
]

_DERLI_KALIPLAR = [(re.compile(k), a) for k, a in KALEM_KALIPLARI]

# Sayı: 1.234.567,89 / -1.234.567 / (1.234.567)
_SAYI = re.compile(
    r"\(?-?\d{1,3}(?:\.\d{3})+(?:,\d+)?\)?"     # 1.234.567,89
    r"|\(?-?\d{1,3}(?:,\d{3})+\)?"                # 433,300,000 (OCR)
    r"|\(?-?\d+,\d+\)?"                           # 1234,56
    r"|\(?-?\d{4,}\)?"                             # 12345
)
# Dönem başlığı: 31.12.2025 / 31/12/2025 / 2025/12 / 31 Aralık 2025
_DONEM_KALIPLARI = [
    re.compile(r"(\d{1,2})[./](\d{1,2})[./](20\d{2})"),
    re.compile(r"(20\d{2})\s*/\s*(\d{1,2})"),
]
_AYLAR = {"ocak":1,"subat":2,"mart":3,"nisan":4,"mayis":5,"haziran":6,
          "temmuz":7,"agustos":8,"eylul":9,"ekim":10,"kasim":11,"aralik":12}


def sayi_coz(ham: str) -> Optional[float]:
    """'1.234.567,89' -> 1234567.89 ; '(1.234)' -> -1234.0"""
    if not ham:
        return None
    s = ham.strip()
    negatif = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    # OCR bazen binlik ayracı virgül olarak okur: "433,300,000"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s):
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "." in s and len(s.split(".")[-1]) == 3:
        s = s.replace(".", "")
    try:
        d = float(s)
    except ValueError:
        return None
    return -d if negatif else d


def donem_gecerli(yil: int, ay: int, bugun_yil: int = 2026) -> bool:
    """
    OCR yılı yanlış okuyabiliyor ("31.12.2025" -> "31.12.2028").
    Gelecek yıla ait finansal tablo olamayacağı için makul aralık dışı
    dönemler elenir.
    """
    return 1990 <= yil <= bugun_yil and 1 <= ay <= 12


def donem_coz(metin: str, ele: bool = True) -> list[tuple[int, int]]:
    """Bir başlık satırındaki tüm dönemleri (yıl, ay) olarak döndürür."""
    sonuc: list[tuple[int, int]] = []
    d = sadelestir(metin)
    for m in _DONEM_KALIPLARI[0].finditer(d):
        sonuc.append((int(m.group(3)), int(m.group(2))))
    if not sonuc:
        for m in _DONEM_KALIPLARI[1].finditer(d):
            sonuc.append((int(m.group(1)), int(m.group(2))))
    if not sonuc:
        for ay_ad, ay_no in _AYLAR.items():
            for m in re.finditer(rf"\d{{1,2}}\s+{ay_ad}\s+(20\d{{2}})", d):
                sonuc.append((int(m.group(1)), ay_no))
    if ele:
        sonuc = [d for d in sonuc if donem_gecerli(*d)]
    return sonuc


# Bulanık eşleştirme için referans kalem adları (OCR bozuk okursa)
_REFERANS_ADLAR: list[tuple[str, str]] = [
    ("hasilat", "Hasilat"), ("net satislar", "Hasilat"),
    ("brut kar", "BrutKar"),
    ("esas faaliyet kari", "FaaliyetKari"), ("teknik bolum dengesi", "FaaliyetKari"),
    ("donem net kari", "NetKar"), ("donem kari", "NetKar"),
    ("net donem kari", "NetKar"),
    ("toplam varliklar", "ToplamVarlik"), ("varliklar toplami", "ToplamVarlik"),
    ("aktif toplami", "ToplamVarlik"),
    ("toplam kaynaklar", "ToplamKaynak"), ("pasif toplami", "ToplamKaynak"),
    ("yukumlulukler toplami", "ToplamBorc"), ("toplam yukumlulukler", "ToplamBorc"),
    ("ozsermaye", "Ozkaynak"), ("ozkaynaklar", "Ozkaynak"),
    ("ozsermaye toplami", "Ozkaynak"),
    ("odenmis sermaye", "OdenmisSermaye"),
    ("donen varliklar", "DonenVarlik"), ("duran varliklar", "DuranVarlik"),
    ("kisa vadeli yukumlulukler", "KisaVadeliYukumluluk"),
    ("uzun vadeli yukumlulukler", "UzunVadeliYukumluluk"),
    ("nakit ve nakit benzerleri", "Nakit"),
    ("isletme faaliyetlerinden nakit akislari", "IsletmeNakitAkisi"),
    ("brut yazilan primler", "YazilanPrim"),
]


def kalem_esle(ad: str, bulanik_esik: float = 0.82) -> Optional[str]:
    """
    Kalem adını standart alana eşler.
    Önce regex (hızlı ve kesin), tutmazsa bulanık eşleştirme —
    çünkü OCR "Dönem Net Kârı" yerine "Dénem Net Kart" üretebiliyor.
    """
    a = sadelestir(ad)
    # OCR'ın ürettiği tablo çizgisi artıkları ve noktalama temizliği
    a = re.sub(r"[|)(\]\[}{]", " ", a)
    a = re.sub(r"[^\w ]", " ", a)
    a = re.sub(r"\s+", " ", a).strip(" .:-")
    if not a or len(a) < 3:
        return None
    for kalip, alan in _DERLI_KALIPLAR:
        if kalip.search(a):
            return alan
    # Bulanık: yalnızca yeterince uzun adlarda, yanlış eşleşmeyi önlemek için
    if len(a) >= 8:
        en_iyi, en_iyi_oran = None, 0.0
        for ref, alan in _REFERANS_ADLAR:
            # Uzunluk koruması: "maddi duran varliklar" ile
            # "duran varliklar" bulanık olarak benziyor ama farklı
            # kalemler. Niteleyici önek eklenmiş kalemlerin ana kalemle
            # karışmasını uzunluk oranı engelliyor.
            if min(len(a), len(ref)) / max(len(a), len(ref)) < 0.8:
                continue
            oran = _ocr_benzerlik(a, ref)
            if oran > en_iyi_oran:
                en_iyi, en_iyi_oran = alan, oran
        if en_iyi_oran >= bulanik_esik:
            return en_iyi
    return None


# ═══════════════════════════════════════════════════════════════════
# 2. VERİ YAPILARI
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CikarimSonucu:
    kaynak: str = "pdf"
    # {alan: {(yil, ay): deger}}
    seriler: dict[str, dict[tuple[int, int], float]] = field(default_factory=dict)
    donemler: list[tuple[int, int]] = field(default_factory=list)
    bulunan_sayfalar: dict[str, int] = field(default_factory=dict)
    olcek: float = 1.0
    uyarilar: list[str] = field(default_factory=list)
    dogrulama: dict[str, bool] = field(default_factory=dict)

    @property
    def guvenilir(self) -> bool:
        """Doğrulamaların tamamı geçtiyse ve en az temel kalemler varsa."""
        if not self.seriler:
            return False
        if any(v is False for v in self.dogrulama.values()):
            return False
        return "Ozkaynak" in self.seriler and "NetKar" in self.seriler

    def guncel(self) -> dict[str, float]:
        if not self.donemler:
            return {}
        son = max(self.donemler)
        return {k: v[son] * self.olcek for k, v in self.seriler.items() if son in v}

    def yillik(self) -> dict[str, dict[int, float]]:
        """Yalnızca 12 aylık dönemler; çeyrek verisi büyümeyi bozar."""
        out: dict[str, dict[int, float]] = {}
        for alan, seri in self.seriler.items():
            y = {yil: d * self.olcek for (yil, ay), d in seri.items() if ay == 12}
            if len(y) >= 2:
                out[alan] = y
        return out


# ═══════════════════════════════════════════════════════════════════
# 3. ÇIKARICI
# ═══════════════════════════════════════════════════════════════════

class IzahnameCikarici:
    """
    İzahname PDF'inden finansal tablo çıkarır.

    Kullanım:
        c = IzahnameCikarici()
        sonuc = c.cikar("izahname.pdf")
        if sonuc.guvenilir:
            ...
    """

    def __init__(self, llm_fn: Optional[Callable] = None,
                 max_sayfa_tara: int = 1200):
        # llm_fn(sayfa_metni, istenen_alanlar) -> dict | None
        self._llm = llm_fn
        self._max_sayfa = max_sayfa_tara

    # ── 3.1 Sayfa bulma ──

    @staticmethod
    def _sayfa_puanla(metin: str, tur: str) -> float:
        """
        Bir sayfanın aranan tablo olma olasılığı.
        Sadece başlığa bakmak yetmez (içindekiler sayfası da eşleşir);
        teyit kalemleri ve sayı yoğunluğu da hesaba katılır.
        """
        d = sadelestir(metin)
        puan = 0.0
        for imza in TABLO_IMZALARI[tur]:
            if imza in d:
                puan += 3.0
                break
        else:
            return 0.0
        teyit = sum(1 for k in TEYIT_KALEMLERI[tur] if k in d)
        puan += teyit * 2.0
        sayi_adedi = len(_SAYI.findall(metin))
        puan += min(sayi_adedi / 10.0, 4.0)
        # İçindekiler sayfası: çok sayıda nokta dizisi, az sayı
        if d.count("....") > 3 or (sayi_adedi < 6 and "sayfa" in d):
            puan -= 5.0
        return puan

    def sayfalari_bul(self, sayfa_metinleri: list[str]) -> dict[str, int]:
        """Her tablo türü için en yüksek puanlı sayfayı döndürür."""
        bulunan: dict[str, int] = {}
        for tur in TABLO_IMZALARI:
            en_iyi, en_iyi_puan = None, 0.0
            for i, metin in enumerate(sayfa_metinleri[:self._max_sayfa]):
                if not metin:
                    continue
                p = self._sayfa_puanla(metin, tur)
                if p > en_iyi_puan:
                    en_iyi, en_iyi_puan = i, p
            if en_iyi is not None and en_iyi_puan >= 6.0:
                bulunan[tur] = en_iyi
        return bulunan

    # ── 3.2 Satır çıkarımı ──

    @staticmethod
    def _olcek_bul(metin: str) -> float:
        """
        'Bin TL' / '(1.000 TL)' gibi ifadeleri yakalar. Ölçek atlanırsa
        tüm oranlar bin kat yanlış çıkar.
        """
        d = sadelestir(metin)
        if re.search(r"\bbin tl\b|\(000\)|1\.000 tl|000 tl olarak", d):
            return 1000.0
        if re.search(r"\bmilyon tl\b", d):
            return 1_000_000.0
        return 1.0

    def sayfadan_cikar(self, metin: str) -> tuple[dict[str, dict[int, float]],
                                                  list[tuple[int, int]], float]:
        """
        Bir sayfanın metninden kalem/dönem/değer üçlülerini çıkarır.
        Dönüş: ({alan: {kolon_index: deger}}, donemler, olcek)
        """
        satirlar = [s for s in metin.split("\n") if s.strip()]
        donemler: list[tuple[int, int]] = []
        for s in satirlar[:12]:
            d = donem_coz(s)
            if len(d) >= 1:
                donemler = d
                break
        olcek = self._olcek_bul(metin)

        cikti: dict[str, dict[int, float]] = {}
        for satir in satirlar:
            sayilar = _SAYI.findall(satir)
            if not sayilar:
                continue
            # Kalem adı = ilk sayıdan önceki kısım
            ilk = satir.find(sayilar[0])
            ad = satir[:ilk].strip()
            alan = kalem_esle(ad)
            if alan is None:
                continue
            degerler = [sayi_coz(x) for x in sayilar]
            degerler = [d for d in degerler if d is not None]
            if not degerler:
                continue
            # Aynı alan birden fazla satırda geçerse İLK bulunanı koru
            # (özet tablolar genelde başta, dipnotlar sonda)
            if alan in cikti:
                continue
            cikti[alan] = {i: d for i, d in enumerate(degerler)}
        return cikti, donemler, olcek

    # ── 3.3 Doğrulama ──

    @staticmethod
    def dogrula(guncel: dict[str, float]) -> dict[str, bool]:
        """
        Çıkarılan verinin iç tutarlılığını kontrol eder.
        Bu katman, hem ayrıştırma hatalarını hem de (LLM yedeği
        kullanılırsa) halüsinasyonu yakalamak için kritiktir.
        """
        d: dict[str, bool] = {}

        # Bilanço denkliği: Toplam Varlıklar = Toplam Kaynaklar
        tv, tk = guncel.get("ToplamVarlik"), guncel.get("ToplamKaynak")
        if tv and tk:
            d["bilanco_denkligi"] = abs(tv - tk) / max(tv, 1) < 0.01

        # Varlık = Dönen + Duran
        dv, dur = guncel.get("DonenVarlik"), guncel.get("DuranVarlik")
        if tv and dv and dur:
            d["varlik_toplami"] = abs((dv + dur) - tv) / max(tv, 1) < 0.02

        # Kaynak = Borç + Özkaynak
        tb, ozk = guncel.get("ToplamBorc"), guncel.get("Ozkaynak")
        if tk and tb and ozk:
            d["kaynak_toplami"] = abs((tb + ozk) - tk) / max(tk, 1) < 0.02

        # Kısa + uzun vadeli = toplam yükümlülük
        kv, uv = guncel.get("KisaVadeliYukumluluk"), guncel.get("UzunVadeliYukumluluk")
        if tb and kv and uv:
            d["yukumluluk_toplami"] = abs((kv + uv) - tb) / max(tb, 1) < 0.02

        # Net kâr faaliyet kârından büyük olabilir ama 5 katı olamaz
        nk, fk = guncel.get("NetKar"), guncel.get("FaaliyetKari")
        if nk and fk and fk > 0:
            d["kar_tutarliligi"] = nk <= fk * 5

        # Özkaynak toplam varlığı aşamaz
        if ozk and tv:
            d["ozkaynak_makul"] = ozk <= tv * 1.01

        # Brüt kâr hasılatı aşamaz
        bk, has = guncel.get("BrutKar"), guncel.get("Hasilat")
        if bk and has and has > 0:
            d["brut_kar_makul"] = bk <= has * 1.01

        return d

    # ── 3.4 Ana akış ──

    def cikar(self, pdf_yolu: str,
              sayfa_metinleri: Optional[list[str]] = None) -> CikarimSonucu:
        """
        sayfa_metinleri verilirse PDF açılmaz (test için).
        """
        sonuc = CikarimSonucu()

        if sayfa_metinleri is None:
            try:
                import pdfplumber
                with pdfplumber.open(pdf_yolu) as pdf:
                    sayfa_metinleri = [
                        (s.extract_text() or "") for s in pdf.pages[:self._max_sayfa]
                    ]
            except Exception as e:
                logger.exception(f"PDF açılamadı ({pdf_yolu}): {e}")
                sonuc.uyarilar.append("PDF okunamadı.")
                return sonuc

        # Taranmış (görüntü) PDF tespiti
        toplam_metin = sum(len(s) for s in sayfa_metinleri)
        if toplam_metin < 200 * max(len(sayfa_metinleri), 1) / 10:
            sonuc.uyarilar.append(
                "PDF metin içermiyor gibi görünüyor (taranmış olabilir); "
                "OCR gerekebilir."
            )

        sonuc.bulunan_sayfalar = self.sayfalari_bul(sayfa_metinleri)
        if not sonuc.bulunan_sayfalar:
            sonuc.uyarilar.append("İzahnamede finansal tablo sayfası bulunamadı.")
            return sonuc

        birlesik_donemler: list[tuple[int, int]] = []
        for tur, idx in sonuc.bulunan_sayfalar.items():
            kalemler, donemler, olcek = self.sayfadan_cikar(sayfa_metinleri[idx])
            if olcek > sonuc.olcek:
                sonuc.olcek = olcek
            if donemler and not birlesik_donemler:
                birlesik_donemler = donemler
            for alan, kolonlar in kalemler.items():
                if alan in sonuc.seriler:
                    continue
                hedef = donemler or birlesik_donemler
                for kolon_idx, deger in kolonlar.items():
                    if kolon_idx < len(hedef):
                        sonuc.seriler.setdefault(alan, {})[hedef[kolon_idx]] = deger

        sonuc.donemler = sorted({d for s in sonuc.seriler.values() for d in s})
        sonuc.dogrulama = self.dogrula(sonuc.guncel())

        basarisiz = [k for k, v in sonuc.dogrulama.items() if v is False]
        if basarisiz:
            sonuc.uyarilar.append(
                "Tutarlılık kontrolü başarısız: " + ", ".join(basarisiz) +
                ". Veri güvenilmez sayıldı."
            )

        # LLM yedeği: yalnızca satır çıkarımı yetersizse
        if not sonuc.guvenilir and self._llm is not None:
            sonuc.uyarilar.append("Satır çıkarımı yetersiz; LLM yedeği denendi.")
            llm_sonuc = self._llm_dene(sayfa_metinleri, sonuc)
            if llm_sonuc is not None:
                return llm_sonuc
        return sonuc

    def _llm_dene(self, sayfa_metinleri: list[str],
                  onceki: CikarimSonucu) -> Optional[CikarimSonucu]:
        """
        LLM yedeği. Çıktı AYNI doğrulama katmanından geçirilir;
        geçmezse kullanılmaz. Finansal rakamda halüsinasyon riski
        olduğu için bu kontrol zorunludur.
        """
        try:
            parcalar = [sayfa_metinleri[i] for i in onceki.bulunan_sayfalar.values()]
            if not parcalar:
                return None
            ham = self._llm("\n\n".join(parcalar), sorted(
                {a for _, a in KALEM_KALIPLARI}))
            if not isinstance(ham, dict) or not ham:
                return None
            yeni = CikarimSonucu(kaynak="pdf+llm", olcek=onceki.olcek,
                                 bulunan_sayfalar=onceki.bulunan_sayfalar,
                                 uyarilar=list(onceki.uyarilar))
            for alan, donem_deger in ham.items():
                for donem_str, deger in (donem_deger or {}).items():
                    d = donem_coz(str(donem_str))
                    v = sayi_coz(str(deger))
                    if d and v is not None:
                        yeni.seriler.setdefault(alan, {})[d[0]] = v
            yeni.donemler = sorted({d for s in yeni.seriler.values() for d in s})
            yeni.dogrulama = self.dogrula(yeni.guncel())
            if not yeni.guvenilir:
                yeni.uyarilar.append(
                    "LLM çıktısı da doğrulamayı geçemedi; veri kullanılmadı."
                )
                return None
            return yeni
        except Exception as e:
            logger.warning(f"LLM yedeği başarısız: {e}")
            return None