from fastapi import FastAPI, Query
import uvicorn
import re
import time
import cloudscraper
import logging
import threading
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import Optional, ClassVar
from enum import Enum
from pydantic import BaseModel
from datetime import datetime, date

# ═══════════════════════════════════════════════════════════════════
# ⚙️ 1. SİSTEM AYARLARI VE LOGLAMA
# ═══════════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

YATIRIM_UYARISI = (
    "Bu değerlendirmeler otomatik bir algoritma tarafından, izahnamede yayınlanan "
    "bilgilerden üretilmiştir; yatırım tavsiyesi değildir. Kaynak veriler eksik veya "
    "hatalı olabilir; yatırım kararı vermeden önce izahnameyi ve KAP açıklamalarını "
    "kendiniz doğrulayın."
)


class Category(str, Enum):
    FINANSAL = "finansal"
    DEGERLEME = "degerleme"
    FON_KULLANIM = "fon_kullanim"
    ARZ_YAPISI = "arz_yapisi"
    ISKONTO = "iskonto"
    ACIKLIK = "aciklik"
    FIYAT_ISTIKRARI = "fiyat_istikrari"
    SATMAMA = "satmama"
    KURUMSALLIK = "kurumsallik"


class ArzDurumu(str, Enum):
    HAZIRLANIYOR = "Hazırlanıyor"
    SPK_ONAYLI = "SPK Onaylı (Tarih Bekleniyor)"
    TALEP_YAKLASIYOR = "Talep Toplama Yaklaşıyor"
    TALEP_TOPLANIYOR = "Talep Toplanıyor"
    DAGITIM_BEKLENIYOR = "Dağıtım Bekleniyor"
    ISLEME_BEKLENIYOR = "İşleme Girmesi Bekleniyor"
    ISLEM_GORMEYE_BASLADI = "Borsada İşlem Görüyor"


class Gorunum(str, Enum):
    HAZIRLIK = "Hazırlık Aşamasında"
    COK_GUCLU = "Çok Güçlü"
    DENGELI = "Dengeli"
    RISKLI = "Yüksek Riskli"


class InfoKey(str, Enum):
    BIST_KODU = "BistKodu"
    TARIH = "Tarih"
    FIYAT = "Fiyat"
    BUYUKLUK = "Büyüklük"
    ISLEM_TARIHI = "IslemTarihi"
    ACIKLIK = "Açıklık"
    ISKONTO = "İskonto"
    TAAHHUT = "Taahhüt"
    HALKA_ARZ_SEKLI = "HalkaArzSekli"
    FON_KULLANIM = "FonKullanim"
    SATIS_YONTEMI = "SatisYontemi"
    FIYAT_ISTIKRARI = "FiyatIstikrari"
    PAY_SAYISI = "PaySayisi"
    FINANSAL_TABLO = "FinansalTablo"
    TAHSISAT = "Tahsisat"
    DAGITIM_TABLOSU = "DağıtımTablosu"
    DAGITIM_TIPI = "DagitimTipi"
    DAGITIM_YONTEMI = "DagitimYontemi"
    ARACI_KURUM = "AraciKurum"
    PAZAR = "Pazar"


class FinKey(str, Enum):
    NET_KAR = "NetKar"
    OZKAYNAK = "Ozkaynak"
    DONEN_VARLIK = "DonenVarlik"
    KISA_VADELI_YUKUMLULUK = "KisaVadeliYukumluluk"
    TOPLAM_BORC = "ToplamBorc"
    HASILAT = "Hasilat"


@dataclass(frozen=True)
class AppSettings:
    BASE_URL: str = "https://halkarz.com/"
    MIN_SKOR: float = 5.0
    TIMEOUT: int = 15
    CACHE_TTL: int = 300
    MAX_RETRY: int = 3
    MAX_SIRKET: int = 15
    ISTEK_ARASI_BEKLEME: float = 0.3


@dataclass(frozen=True)
class ScoreWeights:
    MAX: ClassVar[dict[Category, float]] = {
        Category.FINANSAL: 28.0,
        Category.DEGERLEME: 17.0,
        Category.FON_KULLANIM: 15.0,
        Category.ARZ_YAPISI: 10.0,
        Category.ISKONTO: 8.0,
        Category.ACIKLIK: 8.0,
        Category.FIYAT_ISTIKRARI: 3.0,
        Category.SATMAMA: 4.0,
        Category.KURUMSALLIK: 7.0,
    }
    FINANSAL_NET_KAR: float = MAX[Category.FINANSAL] * 0.40
    FINANSAL_CARI: float = MAX[Category.FINANSAL] * 0.25
    FINANSAL_BORC: float = MAX[Category.FINANSAL] * 0.35
    DEGERLEME_FK: float = MAX[Category.DEGERLEME] * 0.55
    DEGERLEME_PDDD: float = MAX[Category.DEGERLEME] * 0.45


SETTINGS = AppSettings()
WEIGHTS = ScoreWeights()
assert abs(sum(WEIGHTS.MAX.values()) - 100.0) < 1e-9, "Kategori ağırlıkları 100'e tamamlanmıyor!"

_CACHE = {"timestamp": 0.0, "data": []}
_CACHE_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════
# 🏗️ 2. VERİ MODELLERİ
# ═══════════════════════════════════════════════════════════════════
@dataclass
class ScoreResult:
    score: float
    explanation: str
    has_data: bool


class ScoreDetail(BaseModel):
    kategori: Category
    puan: float
    max_puan: float
    aciklama: str
    veri_bulundu: bool


class CompanyResponse(BaseModel):
    sirket: str
    bist_kodu: str
    durum: ArzDurumu
    islem_tarihi: str
    skor: float
    yildiz: str
    genel_gorunum: Gorunum
    genel_degerlendirme: str
    guclu_yanlar: list[str]
    riskler: list[str]
    puan_detaylari: list[ScoreDetail]
    tarih: str
    fiyat: str
    buyukluk: str
    aciklik: str
    yorum_aciklik: str
    iskonto: str
    taahhut: str
    halka_arz_sekli: str
    fon_kullanim: str
    satis_yontemi: str
    fiyat_istikrari: str
    finansal_tablo: str
    tahsisat: str
    dagitim_tablosu: str
    dagitim_tipi: str
    dagitim_yontemi: str
    pay_miktari: str
    araci_kurum: str
    pazar: str
    t1_t2_kullanilabilir: bool
    katilim_endeksine_uygun: bool
    islem_menusu: str
    debug_bilgisi: Optional[dict] = None


class APIResponse(BaseModel):
    halka_arzlar: list[CompanyResponse]
    uyari: str = YATIRIM_UYARISI


# ═══════════════════════════════════════════════════════════════════
# 🔤 3. ARAÇLAR
# ═══════════════════════════════════════════════════════════════════
class TextUtils:
    @staticmethod
    def normalize(s: Optional[str]) -> str:
        s = (s or "").replace("İ", "i")
        return re.sub(r"\s+", " ", s).strip().rstrip(":").strip().lower()

    @staticmethod
    def yuzde_bul(metin: Optional[str]) -> Optional[float]:
        if not metin:
            return None
        eslesme = re.search(r"%\s*(\d+[.,]?\d*)|(\d+[.,]?\d*)\s*%", metin)
        if not eslesme:
            return None
        deger = eslesme.group(1) or eslesme.group(2)
        try:
            return float(deger.replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def sayi_bul(metin: Optional[str]) -> Optional[float]:
        if not metin:
            return None
        desenler = re.findall(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+,\d+|-?\d+", metin)
        for d in desenler:
            try:
                return float(d.replace(".", "").replace(",", "."))
            except ValueError:
                continue
        return None

    @staticmethod
    def multiplier_from_ranges(value: float, ranges: list[tuple[float, float]]) -> float:
        for threshold, multiplier in ranges:
            if value < threshold:
                return multiplier
        return 0.0


# ═══════════════════════════════════════════════════════════════════
# 💰 4. PUANLAMA MOTORU
# ═══════════════════════════════════════════════════════════════════
class ScoreAnalyzer:
    def __init__(self, weights: ScoreWeights):
        self.weights = weights
        self.BUYUME_ANAHTAR = ["kapasite artırım", "yeni tesis", "yenilenebilir", "ar-ge", "yatırım", "makine", "teçhizat", "büyüme"]
        self.BORC_ANAHTAR = ["borç", "kredi", "finansal borç"]
        self.ISLETME_ANAHTAR = ["işletme sermayesi", "işletme ihtiyac", "hammadde"]

    def finansal_puanla(self, fin: dict) -> ScoreResult:
        puan, aciklamalar, veri_var = 0.0, [], False
        net_kar, hasilat = fin.get(FinKey.NET_KAR), fin.get(FinKey.HASILAT)
        
        if net_kar is not None:
            veri_var = True
            if net_kar < 0:
                zarar_orani = min(abs(net_kar) / hasilat, 1) if hasilat else 0.5
                puan += max(0, self.weights.FINANSAL_NET_KAR * 0.3 * (1 - zarar_orani))
                aciklamalar.append(f"Zarar ({net_kar:,.0f} TL).")
            elif hasilat:
                marj = (net_kar / hasilat) * 100
                mult = TextUtils.multiplier_from_ranges(marj, [(5.0, 0.5), (15.0, 0.8), (float("inf"), 1.0)])
                puan += self.weights.FINANSAL_NET_KAR * mult
                aciklamalar.append(f"Net kâr marjı %{marj:.1f}.")
            else:
                puan += self.weights.FINANSAL_NET_KAR * 0.6
                aciklamalar.append(f"Kârlı ({net_kar:,.0f} TL) ama hasılat verisi yok, marj hesaplanamadı.")
        else:
            puan += self.weights.FINANSAL_NET_KAR / 2
            aciklamalar.append("Net kâr verisi bulunamadı (nötr).")

        donen, kv_yuk = fin.get(FinKey.DONEN_VARLIK), fin.get(FinKey.KISA_VADELI_YUKUMLULUK)
        if donen is not None and kv_yuk:
            veri_var = True
            oran = donen / kv_yuk
            mult = TextUtils.multiplier_from_ranges(oran, [(1.0, 0.15), (1.5, 0.5), (3.0, 1.0), (float("inf"), 0.65)])
            puan += self.weights.FINANSAL_CARI * mult
            aciklamalar.append(f"Cari oran {oran:.2f}.")
        else:
            puan += self.weights.FINANSAL_CARI / 2
            aciklamalar.append("Cari oran hesaplanamadı (nötr).")

        borc, ozk = fin.get(FinKey.TOPLAM_BORC), fin.get(FinKey.OZKAYNAK)
        if borc is not None and ozk:
            veri_var = True
            oran = borc / ozk
            mult = TextUtils.multiplier_from_ranges(oran, [(0.5, 1.0), (1.0, 0.65), (2.0, 0.3), (float("inf"), 0.0)])
            puan += self.weights.FINANSAL_BORC * mult
            aciklamalar.append(f"Borç/Özkaynak {oran:.2f}.")
        else:
            puan += self.weights.FINANSAL_BORC / 2
            aciklamalar.append("Borç/Özkaynak oranı hesaplanamadı (nötr).")

        return ScoreResult(round(min(self.weights.MAX[Category.FINANSAL], puan), 1), " ".join(aciklamalar), veri_var)

    def degerleme_puanla(self, fiyat_metni: str, pay_metni: str, fin: dict) -> ScoreResult:
        puan, aciklamalar, veri_var = 0.0, [], False
        fiyat = TextUtils.sayi_bul(fiyat_metni)
        pay = TextUtils.sayi_bul(pay_metni)
        net_kar, ozk = fin.get(FinKey.NET_KAR), fin.get(FinKey.OZKAYNAK)

        if fiyat and pay and net_kar and net_kar > 0:
            veri_var = True
            fk = fiyat / (net_kar / pay)
            mult = TextUtils.multiplier_from_ranges(fk, [(8.0, 1.0), (15.0, 0.75), (25.0, 0.5), (float("inf"), 0.15)])
            puan += self.weights.DEGERLEME_FK * mult
            aciklamalar.append(f"F/K {fk:.1f}.")
        else:
            puan += self.weights.DEGERLEME_FK / 2
            aciklamalar.append("F/K hesaplanamadı (zarar var veya fiyat/pay sayısı verisi eksik) — nötr.")

        if fiyat and pay and ozk and ozk > 0:
            veri_var = True
            pddd = (fiyat * pay) / ozk
            mult = TextUtils.multiplier_from_ranges(pddd, [(1.0, 1.0), (2.0, 0.7), (4.0, 0.4), (float("inf"), 0.15)])
            puan += self.weights.DEGERLEME_PDDD * mult
            aciklamalar.append(f"PD/DD {pddd:.2f}.")
        else:
            puan += self.weights.DEGERLEME_PDDD / 2
            aciklamalar.append("PD/DD hesaplanamadı (sermaye/özkaynak verisi eksik) — nötr.")

        return ScoreResult(round(min(self.weights.MAX[Category.DEGERLEME], puan), 1), " ".join(aciklamalar), veri_var)

    def fon_kullanim_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.FON_KULLANIM]
        taban = mx / 2
        norm = TextUtils.normalize(metin)
        if not metin or norm in ("", "açıklanmadı"):
            return ScoreResult(round(taban, 1), "Fon kullanım amacı belirtilmemiş (nötr).", False)

        satirlar = metin.lower().split("\n")
        buyume_toplam, borc_toplam, herhangi_yuzde = 0.0, 0.0, False
        for satir in satirlar:
            yuzde = TextUtils.yuzde_bul(satir)
            if yuzde is not None:
                herhangi_yuzde = True
            if any(k in satir for k in self.BUYUME_ANAHTAR):
                buyume_toplam += yuzde if yuzde is not None else 0
            if any(k in satir for k in self.BORC_ANAHTAR):
                borc_toplam += yuzde if yuzde is not None else 0

        if not herhangi_yuzde:
            buyume_var = any(any(k in s for k in self.BUYUME_ANAHTAR) for s in satirlar)
            borc_var = any(any(k in s for k in self.BORC_ANAHTAR) for s in satirlar)
            puan = taban
            parcalar = []
            if buyume_var:
                puan += mx * 0.35
                parcalar.append("büyüme/yatırım ifadesi var ama oranı belirtilmemiş")
            if borc_var:
                puan -= mx * 0.30
                parcalar.append("borç ödemesi ifadesi var ama oranı belirtilmemiş")
            puan = max(0, min(mx, puan))
            return ScoreResult(round(puan, 1), ("; ".join(parcalar) or "Belirgin bir sinyal yok."), True)

        buyume_katki = (min(buyume_toplam, 100) / 100) * mx * 0.55
        borc_ceza = (min(borc_toplam, 100) / 100) * mx * 0.45
        puan = max(0, min(mx, taban + buyume_katki - borc_ceza))
        aciklama = f"Fonun ~%{min(buyume_toplam, 100):.0f}'i büyüme yatırımlarına, ~%{min(borc_toplam, 100):.0f}'i borç ödemesine ayrılmış."
        return ScoreResult(round(puan, 1), aciklama, True)

    def arz_yapisi_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ARZ_YAPISI]
        if not metin or metin in ("-", "açıklanmadı"):
            return ScoreResult(round(mx / 2, 1), "Arz şekli belirsiz.", False)

        m_lower = metin.lower()
        olumsuzlanmis = re.sub(
            r"(ortak satış[ıi]?|mevcut pay satış[ıi]?)\s*[^.]{0,15}\b(yok|bulunmuyor|bulunmamaktadır)\b",
            "", m_lower,
        )
        
        ortak_satisi_var = "ortak satış" in olumsuzlanmis or "mevcut pay satış" in olumsuzlanmis

        if "sermaye artırımı" in m_lower and not ortak_satisi_var:
            return ScoreResult(mx, "Tamamen sermaye artırımı (fon şirkette kalır).", True)
        elif "sermaye artırımı" in m_lower and ortak_satisi_var:
            return ScoreResult(round(mx * 0.5, 1), "Kısmi ortak satışı var.", True)
        elif ortak_satisi_var:
            return ScoreResult(round(mx * 0.1, 1), "Tamamen ortak satışı (fon kasaya girmez).", True)

        return ScoreResult(round(mx / 2, 1), "Belirsiz arz yapısı.", True)

    def iskonto_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ISKONTO]
        isk = TextUtils.yuzde_bul(metin)
        if isk is None:
            return ScoreResult(round(mx / 2, 1), "İskonto belirsiz (nötr).", False)
        if isk >= 30:
            mult = 1.0
        elif isk >= 25:
            mult = 0.85
        elif isk >= 20:
            mult = 0.65
        elif isk >= 10:
            mult = 0.4
        elif isk >= 5:
            mult = 0.2
        else:
            mult = 0.1
        return ScoreResult(round(mx * mult, 1), f"İskonto %{isk:.0f}.", True)

    def aciklik_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ACIKLIK]
        aciklik = TextUtils.yuzde_bul(metin)
        if aciklik is None:
            return ScoreResult(round(mx / 2, 1), "Açıklık belirsiz (nötr).", False)
        if aciklik < 10:
            p, not_ = 0.2, "çok düşük (dar işlem hacmi riski)"
        elif aciklik <= 20:
            p, not_ = 0.8, "iyi"
        elif aciklik <= 35:
            p, not_ = 1.0, "dengeli"
        elif aciklik <= 45:
            p, not_ = 0.7, "biraz yüksek"
        else:
            p, not_ = 0.4, "fazla yüksek"
        return ScoreResult(round(mx * p, 1), f"Açıklık %{aciklik:.0f} ({not_}).", True)

    def fiyat_istikrari_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.FIYAT_ISTIKRARI]
        m_lower = (metin or "").lower()
        if not m_lower or m_lower in ("-", "açıklanmadı"):
            return ScoreResult(round(mx / 2, 1), "İstikrar bilgisi yok (nötr).", False)
        if "planlanmamaktadır" in m_lower or m_lower == "yok":
            return ScoreResult(0.0, "Fiyat istikrarı planlanmıyor.", True)
        return ScoreResult(mx, "Fiyat istikrarı taahhüdü mevcut.", True)

    def satmama_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.SATMAMA]
        m_lower = (metin or "").lower()
        if not m_lower or m_lower in ("-", "açıklanmadı"):
            return ScoreResult(round(mx / 2, 1), "Taahhüt bilgisi yok (nötr).", False)
        if "yok" in m_lower or "bulunmuyor" in m_lower:
            return ScoreResult(0.0, "Satmama taahhüdü bulunmuyor.", True)
        if "ihraççı" in m_lower and "ortak" in m_lower and ("1 yıl" in m_lower or "2 yıl" in m_lower):
            return ScoreResult(mx, "Hem ihraççı hem ortaklar için uzun süreli satmama taahhüdü var.", True)
        if "1 yıl" in m_lower or "2 yıl" in m_lower:
            return ScoreResult(mx, "Satmama taahhüdü mevcut.", True)
        return ScoreResult(round(mx * 0.5, 1), "Kısmi veya belirsiz taahhüt.", True)

    def kurumsallik_puanla(self, raw_text: str) -> ScoreResult:
        mx = self.weights.MAX[Category.KURUMSALLIK]
        m = raw_text.lower()
        desenler = re.findall(
            r"(19[5-9]\d|20[0-2]\d)\s*yılında\s*kurul"
            r"|kurul\w*\s*(19[5-9]\d|20[0-2]\d)"
            r"|(19[5-9]\d|20[0-2]\d)\s*yılından bu yana"
            r"|faaliyete\s*(19[5-9]\d|20[0-2]\d)"
            r"|kuruluş\s*:?\s*(19[5-9]\d|20[0-2]\d)"
            r"|kuruluş tarihi\s*:?\s*(19[5-9]\d|20[0-2]\d)",
            m,
        )
        yillar = [int(y) for grup in desenler for y in grup if y]
        if not yillar:
            return ScoreResult(round(mx / 2, 1), "Kuruluş yılı bulunamadı (nötr, best-effort taramaydı).", False)

        yas = datetime.now().year - min(yillar)
        mult = TextUtils.multiplier_from_ranges(yas, [(5, 0.25), (15, 0.5), (25, 0.75), (float("inf"), 1.0)])
        return ScoreResult(round(mx * mult, 1), f"Şirket {min(yillar)} yılında kurulmuş (~{yas} yıllık).", True)

    def skoru_topla(self, veri: dict, fin: dict, durum: ArzDurumu, raw_text: str):
        if durum == ArzDurumu.HAZIRLANIYOR:
            return 0.0, [], [], []

        hesaplamalar = [
            (Category.FINANSAL, self.finansal_puanla(fin)),
            (Category.DEGERLEME, self.degerleme_puanla(veri.get(InfoKey.FIYAT, ""), veri.get(InfoKey.PAY_SAYISI, ""), fin)),
            (Category.FON_KULLANIM, self.fon_kullanim_puanla(veri.get(InfoKey.FON_KULLANIM, ""))),
            (Category.ARZ_YAPISI, self.arz_yapisi_puanla(veri.get(InfoKey.HALKA_ARZ_SEKLI, ""))),
            (Category.ISKONTO, self.iskonto_puanla(veri.get(InfoKey.ISKONTO, ""))),
            (Category.ACIKLIK, self.aciklik_puanla(veri.get(InfoKey.ACIKLIK, ""))),
            (Category.FIYAT_ISTIKRARI, self.fiyat_istikrari_puanla(veri.get(InfoKey.FIYAT_ISTIKRARI, ""))),
            (Category.SATMAMA, self.satmama_puanla(veri.get(InfoKey.TAAHHUT, ""))),
            (Category.KURUMSALLIK, self.kurumsallik_puanla(raw_text)),
        ]

        toplam, puan_detaylari, guclu, risk = 0.0, [], [], []
        for kategori, res in hesaplamalar:
            mx = self.weights.MAX[kategori]
            toplam += res.score
            puan_detaylari.append(ScoreDetail(
                kategori=kategori, puan=res.score, max_puan=mx, aciklama=res.explanation, veri_bulundu=res.has_data
            ))
            oran = res.score / mx if mx else 0
            if res.has_data and oran >= 0.7:
                guclu.append(f"[{kategori.value}] {res.explanation} (+{res.score:.1f}/{mx:.0f})")
            elif res.has_data and oran <= 0.3:
                risk.append(f"[{kategori.value}] {res.explanation} ({res.score:.1f}/{mx:.0f})")

        skor = round(max(SETTINGS.MIN_SKOR, min(100.0, toplam)), 1)
        return skor, guclu, risk, puan_detaylari


# ═══════════════════════════════════════════════════════════════════
# 🕸️ 5. SCRAPING VE PARSING
# ═══════════════════════════════════════════════════════════════════
class DataExtractor:
    def __init__(self, analyzer: ScoreAnalyzer):
        self.analyzer = analyzer
        self.scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "darwin", "desktop": True})

        self.FIELD_LABELS: dict[InfoKey, list[str]] = {
            InfoKey.BIST_KODU: ["bist kodu"],
            InfoKey.TARIH: ["halka arz tarihi", "talep toplama tarihi"],
            InfoKey.FIYAT: ["halka arz fiyatı"],
            InfoKey.BUYUKLUK: ["halka arz büyüklüğü"],
            InfoKey.ISLEM_TARIHI: ["işlem tarihi", "borsada işlem tarihi"],
            InfoKey.ACIKLIK: ["halka açıklık", "halka açıklık oranı"],
            InfoKey.ISKONTO: ["halka arz iskontosu", "iskonto oranı"],
            InfoKey.TAAHHUT: ["satmama taahhüdü"],
            InfoKey.HALKA_ARZ_SEKLI: ["halka arz şekli"],
            InfoKey.FON_KULLANIM: ["fonun kullanım yeri", "fon kullanım yeri"],
            InfoKey.SATIS_YONTEMI: ["halka arz satış yöntemi"],
            InfoKey.FIYAT_ISTIKRARI: ["fiyat istikrarı"],
            InfoKey.PAY_SAYISI: ["dağıtılacak pay miktarı", "dağıtılacak pay", "toplam pay miktarı", "toplam pay", "çıkarılmış sermaye", "ödenmiş sermaye", "halka arz sonrası sermaye", "toplam pay sayısı", "pay"],
            InfoKey.DAGITIM_YONTEMI: ["dağıtım yöntemi"],
            InfoKey.ARACI_KURUM: ["aracı kurum", "konsorsiyum lideri"],
            InfoKey.PAZAR: ["pazar"],
        }
        self.TUM_ETIKETLER = {e for etiketler in self.FIELD_LABELS.values() for e in etiketler}

        self.FIN_LABELS: dict[FinKey, list[str]] = {
            FinKey.NET_KAR: ["net dönem karı", "net dönem kârı", "net kar", "net kâr", "dönem net karı", "dönem net kârı"],
            FinKey.OZKAYNAK: ["özkaynaklar", "toplam özkaynaklar", "öz kaynaklar"],
            FinKey.DONEN_VARLIK: ["dönen varlıklar", "toplam dönen varlıklar"],
            FinKey.KISA_VADELI_YUKUMLULUK: ["kısa vadeli yükümlülükler", "kısa vadeli borçlar"],
            FinKey.TOPLAM_BORC: ["toplam yükümlülükler", "toplam borçlar", "finansal borçlar"],
            FinKey.HASILAT: ["hasılat", "satış gelirleri", "net satışlar"],
        }

        self.DEFAULTS: dict[InfoKey, str] = {k: "Açıklanmadı" for k in self.FIELD_LABELS}
        self.DEFAULTS.update({
            InfoKey.BIST_KODU: "Belli Değil",
            InfoKey.HALKA_ARZ_SEKLI: "-",
            InfoKey.FON_KULLANIM: "Açıklanmadı.",
            InfoKey.SATIS_YONTEMI: "-",
            InfoKey.FIYAT_ISTIKRARI: "-",
            InfoKey.FINANSAL_TABLO: "Açıklanmadı.",
            InfoKey.TAHSISAT: "Henüz açıklanmadı.",
            InfoKey.DAGITIM_TABLOSU: "Lot tablosu bulunamadı.",
            InfoKey.DAGITIM_TIPI: "Tahmini Lot Dağıtımı",
        })

    def _tablodan_doldur(self, veri: dict, detay_soup: BeautifulSoup):
        for tr in detay_soup.find_all("tr"):
            tds = tr.find_all(["th", "td"])
            if len(tds) < 2:
                continue
            baslik_norm = TextUtils.normalize(tds[0].get_text(strip=True))
            deger = tds[1].get_text(separator=" ", strip=True)
            if not deger:
                continue
            for alan, etiketler in self.FIELD_LABELS.items():
                if veri.get(alan) != self.DEFAULTS.get(alan):
                    continue
                if any(e in baslik_norm for e in etiketler):
                    veri[alan] = deger
                    break

    def _satirdan_deger_al(self, lines: list[str], normalized_lines: list[str], i: int) -> str:
        orijinal = lines[i]
        if ":" in orijinal:
            sonraki = orijinal.split(":", 1)[1].strip()
            if sonraki:
                return sonraki
        toplanan = []
        for j in range(i + 1, min(i + 9, len(lines))):
            satir = lines[j].strip()
            if not satir:
                break
            if normalized_lines[j] in self.TUM_ETIKETLER:
                break
            toplanan.append(satir.lstrip("-•").strip())
        return "\n".join(t for t in toplanan if t)

    def _satirlardan_doldur(self, veri: dict, raw_text: str):
        lines = [l.strip() for l in raw_text.split("\n")]
        normalized_lines = [TextUtils.normalize(l) for l in lines]

        for alan, etiketler in self.FIELD_LABELS.items():
            if veri.get(alan) != self.DEFAULTS.get(alan):
                continue
            for i, nl in enumerate(normalized_lines):
                tam_eslesti = nl in etiketler
                onek_eslesti = any(
                    nl.startswith(e) and (len(nl) == len(e) or nl[len(e)] in (":", " ")) for e in etiketler
                )
                if not (tam_eslesti or onek_eslesti):
                    continue
                deger = self._satirdan_deger_al(lines, normalized_lines, i)
                if deger:
                    veri[alan] = deger
                break

    def _dagitim_tahsisat_finansal_doldur(self, veri: dict, raw_text: str):
        lines = raw_text.split("\n")

        lot_baslik = None
        if "Dağıtılan Pay Miktarı" in raw_text:
            lot_baslik = "Dağıtılan Pay Miktarı"
            veri[InfoKey.DAGITIM_TIPI] = "Dağıtılan Pay Miktarı (Kesin Sonuç)"
        elif "Dağıtılacak Pay Miktarı" in raw_text:
            lot_baslik = "Dağıtılacak Pay Miktarı"
            veri[InfoKey.DAGITIM_TIPI] = "Tahmini Lot Dağıtımı"

        if lot_baslik:
            try:
                bolunmus = raw_text.split(lot_baslik)[1]
                lot_lines = []
                for satir_metni in bolunmus.split("\n")[1:20]:
                    kucuk = satir_metni.lower()
                    if "katılım" in kucuk or "lot" in kucuk or "kişi" in kucuk:
                        temiz = satir_metni.replace("-", "").strip()
                        if temiz:
                            lot_lines.append("• " + temiz)
                    elif "finansal" in kucuk or "halka arz" in kucuk or "bist" in kucuk:
                        break
                if lot_lines:
                    veri[InfoKey.DAGITIM_TABLOSU] = "\n".join(lot_lines)
            except Exception:
                pass

        for i, line in enumerate(lines):
            nl = TextUtils.normalize(line)
            if nl == "tahsisat grupları" and veri[InfoKey.TAHSISAT] == self.DEFAULTS[InfoKey.TAHSISAT]:
                t_list = [
                    "• " + lines[i + j].replace("-", "").strip()
                    for j in range(1, 6)
                    if i + j < len(lines) and ("%" in lines[i + j] or "Lot" in lines[i + j])
                ]
                if t_list:
                    veri[InfoKey.TAHSISAT] = "\n".join(t_list)
            
            elif "finansal tablo" in nl and veri[InfoKey.FINANSAL_TABLO] == self.DEFAULTS[InfoKey.FINANSAL_TABLO]:
                t_list = [
                    lines[i + j].strip()
                    for j in range(1, 10)
                    if i + j < len(lines) and "*" not in lines[i + j] and lines[i + j].strip()
                ]
                if t_list:
                    veri[InfoKey.FINANSAL_TABLO] = "\n".join(t_list)

    def _finansal_tablo_cikar(self, detay_soup: BeautifulSoup, raw_text: str) -> dict:
        bulunan = {}
        for tr in detay_soup.find_all("tr"):
            tds = tr.find_all(["th", "td"])
            if len(tds) < 2:
                continue
            baslik_norm = TextUtils.normalize(tds[0].get_text(strip=True))
            for alan, etiketler in self.FIN_LABELS.items():
                if alan in bulunan:
                    continue
                if any(e in baslik_norm for e in etiketler):
                    for td in tds[1:]:
                        sayi = TextUtils.sayi_bul(td.get_text(strip=True))
                        if sayi is not None:
                            bulunan[alan] = sayi
                            break

        lines = raw_text.split("\n")
        for i, line in enumerate(lines):
            nl = TextUtils.normalize(line)
            for alan, etiketler in self.FIN_LABELS.items():
                if alan in bulunan:
                    continue
                if any(nl == e or nl.startswith(e) for e in etiketler):
                    sayi = TextUtils.sayi_bul(line)
                    if sayi is None and i + 1 < len(lines):
                        sayi = TextUtils.sayi_bul(lines[i + 1])
                    if sayi is not None:
                        bulunan[alan] = sayi
        return bulunan

    def _fetch_url_with_retry(self, url: str):
        for i in range(SETTINGS.MAX_RETRY):
            try:
                res = self.scraper.get(url, timeout=SETTINGS.TIMEOUT)
                if res.status_code == 200:
                    return res
                logger.warning(f"Durum {res.status_code} ({url}). Deneme {i + 1}/{SETTINGS.MAX_RETRY}...")
            except Exception as e:
                logger.warning(f"Bağlantı hatası ({url}): {e}. Deneme {i + 1}...")
            time.sleep(1)
        return None

    # 💡 YEPYENİ VE AKILLI ZAMAN MOTORU
    def _durum_belirle(self, tarih_metni: str, islem_tarihi_metni: str, raw_text: str, kart_metni: str) -> ArzDurumu:
        rt_lower = raw_text.lower()
        bugun = datetime.now().date()
        aylar = {
            "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
            "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12
        }

        # 1. Gong! kelimesi veya izahnamedeki metin ile anlık durum
        if "işlem görmeye başlamıştır" in rt_lower or "gong!" in kart_metni.lower():
            return ArzDurumu.ISLEM_GORMEYE_BASLADI

        # 2. İşlem Tarihi "O Gün Geldiğinde" Otomatik Borsada İşlem Görüyor Yap
        is_trh = str(islem_tarihi_metni).lower().strip()
        if is_trh and is_trh not in ("-", "açıklanmadı", "belli değil"):
            ay_str = next((ay for ay in aylar if ay in is_trh), None)
            sayilar = re.findall(r'\d+', is_trh)
            if ay_str and sayilar:
                try:
                    gun = int(sayilar[0])
                    yil_listesi = [int(s) for s in sayilar if len(s) == 4]
                    yil = yil_listesi[0] if yil_listesi else bugun.year
                    islem_dt = date(yil, aylar[ay_str], gun)
                    
                    # Eğer bugünün tarihi işlem tarihine eşit veya onu geçmişse doğrudan yeşil yap!
                    if bugun >= islem_dt:
                        return ArzDurumu.ISLEM_GORMEYE_BASLADI
                except Exception:
                    pass

        # 3. Kesinleşen / Dağıtılan Pay Oranı açıklandıysa dağıtım bitmiş, işleme girmesi bekleniyor demektir
        if "dağıtılan pay miktarı" in rt_lower or "kesinleşen" in rt_lower:
            return ArzDurumu.ISLEME_BEKLENIYOR

        # 4. Talep Toplama Tarihi Analizi (Çok Daha Kesin)
        t_metin = str(tarih_metni).lower().strip()
        if not t_metin or t_metin in ("-", "açıklanmadı", "belli değil"):
            if "taslak" in kart_metni or "hazırlanıyor" in kart_metni:
                return ArzDurumu.HAZIRLANIYOR
            return ArzDurumu.SPK_ONAYLI
            
        ay_str = next((ay for ay in aylar if ay in t_metin), None)
        sayilar = re.findall(r'\d+', t_metin)
        
        if ay_str and len(sayilar) >= 2:
            try:
                gun_baslangic = int(sayilar[0])
                gunler = [int(s) for s in sayilar if len(s) < 4]
                gun_bitis = max(gunler) if gunler else gun_baslangic
                yil_listesi = [int(s) for s in sayilar if len(s) == 4]
                yil = yil_listesi[0] if yil_listesi else bugun.year
                
                bas_dt = date(yil, aylar[ay_str], gun_baslangic)
                bit_dt = date(yil, aylar[ay_str], gun_bitis)
                
                if bugun < bas_dt:
                    return ArzDurumu.TALEP_YAKLASIYOR
                elif bas_dt <= bugun <= bit_dt:
                    return ArzDurumu.TALEP_TOPLANIYOR
                elif bugun > bit_dt:
                    return ArzDurumu.DAGITIM_BEKLENIYOR
            except Exception:
                pass 
                
        if "hazırlanıyor" in kart_metni or "taslak" in kart_metni:
            return ArzDurumu.HAZIRLANIYOR
        return ArzDurumu.SPK_ONAYLI

    def _sirket_isle(self, sirket_adi: str, detay_linki: str, kart_metni: str, debug: bool) -> Optional[CompanyResponse]:
        detay_res = self._fetch_url_with_retry(detay_linki)
        if not detay_res:
            return None

        detay_soup = BeautifulSoup(detay_res.text, "html.parser")
        detay_metin = detay_soup.get_text(separator=" ", strip=True).lower()

        veri = dict(self.DEFAULTS)
        self._tablodan_doldur(veri, detay_soup)
        raw_text = detay_soup.get_text(separator="\n", strip=True)
        self._satirlardan_doldur(veri, raw_text)
        self._dagitim_tahsisat_finansal_doldur(veri, raw_text)
        fin = self._finansal_tablo_cikar(detay_soup, raw_text)

        t1_t2 = "t1-t2 kullanılabilir" in raw_text.lower() or "t1 ve t2 kullanılabilir" in raw_text.lower()
        katilim = "katılım endeksine uygun değildir" not in raw_text.lower() and "katılım endeksine uygun" in raw_text.lower()
        islem_menusu = "Hisse Alış/Satış Menüsü" if "borsada satış" in veri.get(InfoKey.DAGITIM_YONTEMI, "").lower() else "Halka Arz Menüsü"

        if veri.get(InfoKey.TARIH) == self.DEFAULTS.get(InfoKey.TARIH):
            tarih_match = re.search(
                r"(\d{1,2}(?:-\d{1,2})*\s+(?:ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)\s+\d{4})", 
                kart_metni, 
                re.IGNORECASE
            )
            if tarih_match:
                veri[InfoKey.TARIH] = tarih_match.group(1).title()

        buyukluk_metni = str(veri.get(InfoKey.BUYUKLUK, ""))
        if "**" in buyukluk_metni:
            veri[InfoKey.BUYUKLUK] = buyukluk_metni.split("**")[0].strip()
        elif "Grafiği" in buyukluk_metni:
            veri[InfoKey.BUYUKLUK] = buyukluk_metni.split("Grafiği")[0].strip()

        # 💡 YENİ: Hem TARIH hem de ISLEM_TARIHI parametrelerini karar motoruna gönderiyoruz
        durum = self._durum_belirle(veri.get(InfoKey.TARIH, ""), veri.get(InfoKey.ISLEM_TARIHI, ""), raw_text, kart_metni)
        
        skor, guclu, risk, detaylar = self.analyzer.skoru_topla(veri, fin, durum, raw_text)

        if durum == ArzDurumu.HAZIRLANIYOR:
            gorunum = Gorunum.HAZIRLIK
            degerlendirme = "Bu şirket henüz hazırlık aşamasındadır. SPK onaylı nihai izahname ve kesin tarihler beklenmektedir."
        else:
            gorunum = Gorunum.COK_GUCLU if skor >= 75 else Gorunum.DENGELI if skor >= 50 else Gorunum.RISKLI
            deg_metin = f"Algoritmamız bu halka arzı {skor} puan ile '{gorunum.value}' olarak sınıflandırdı. "
            isk_val = TextUtils.yuzde_bul(veri.get(InfoKey.ISKONTO, ""))
            if isk_val is not None:
                deg_metin += f"Şirket %{isk_val} iskonto ile halka arz ediliyor. "
            deg_metin += "Katılım endeksine UYGUN. " if katilim else "Katılım endeksine UYGUN DEĞİL. "
            if "fiyat istikrarı taahhüdü mevcut" in str(guclu).lower():
                deg_metin += "Fiyat istikrarı planlanması olumlu bir sinyaldir."
            degerlendirme = deg_metin

        yildiz_sayisi = 0 if skor == 0 else max(1, int(skor / 20))
        yildiz = "★" * yildiz_sayisi + "☆" * (5 - yildiz_sayisi)
        yorum_aciklik = (
            "Düşük açıklık oranı piyasadaki lot sayısını sınırlar, fiyat istikrarını destekler."
            if "1" in veri[InfoKey.ACIKLIK] or "2" in veri[InfoKey.ACIKLIK]
            else "Geniş açıklık oranı dolaşımdaki payı artırır."
        )

        debug_bilgisi = None
        if debug:
            debug_bilgisi = {
                "veri_ham": {k.value: v for k, v in veri.items()},
                "finansal_ham": {k.value: v for k, v in fin.items()},
                "default_kalanlar": [a.value for a in self.FIELD_LABELS if veri.get(a) == self.DEFAULTS.get(a)],
                "raw_text_ilk_1500": raw_text[:1500],
            }

        return CompanyResponse(
            sirket=sirket_adi,
            bist_kodu=veri[InfoKey.BIST_KODU],
            durum=durum,
            islem_tarihi=veri[InfoKey.ISLEM_TARIHI],
            skor=skor,
            yildiz=yildiz,
            genel_gorunum=gorunum,
            genel_degerlendirme=degerlendirme,
            guclu_yanlar=guclu,
            riskler=risk,
            puan_detaylari=detaylar,
            tarih=veri[InfoKey.TARIH],
            fiyat=veri[InfoKey.FIYAT],
            buyukluk=veri[InfoKey.BUYUKLUK],
            aciklik=veri[InfoKey.ACIKLIK],
            yorum_aciklik=yorum_aciklik,
            iskonto=veri[InfoKey.ISKONTO],
            taahhut=veri[InfoKey.TAAHHUT],
            halka_arz_sekli=veri[InfoKey.HALKA_ARZ_SEKLI],
            fon_kullanim=veri[InfoKey.FON_KULLANIM],
            satis_yontemi=veri[InfoKey.SATIS_YONTEMI],
            fiyat_istikrari=veri[InfoKey.FIYAT_ISTIKRARI],
            finansal_tablo=veri[InfoKey.FINANSAL_TABLO],
            tahsisat=veri[InfoKey.TAHSISAT],
            dagitim_tablosu=veri[InfoKey.DAGITIM_TABLOSU],
            dagitim_tipi=veri[InfoKey.DAGITIM_TIPI],
            dagitim_yontemi=veri.get(InfoKey.DAGITIM_YONTEMI, "Açıklanmadı"),
            pay_miktari=veri.get(InfoKey.PAY_SAYISI, "Açıklanmadı"),
            araci_kurum=veri.get(InfoKey.ARACI_KURUM, "Açıklanmadı"),
            pazar=veri.get(InfoKey.PAZAR, "Açıklanmadı"),
            t1_t2_kullanilabilir=t1_t2,
            katilim_endeksine_uygun=katilim,
            islem_menusu=islem_menusu,
            debug_bilgisi=debug_bilgisi,
        )

    def analiz_et(self, debug: bool = False) -> list[CompanyResponse]:
        sirket_listesi: list[CompanyResponse] = []
        gorulen_sirketler = set()

        try:
            logger.info(f"🌍 {SETTINGS.BASE_URL} aranıyor...")
            response = self._fetch_url_with_retry(SETTINGS.BASE_URL)
            if not response:
                logger.error("Ana sayfa yüklenemedi.")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            for etiket in soup.find_all("h3"):
                link = etiket.find("a") or etiket.find_parent("a")
                if not link or "href" not in link.attrs:
                    continue

                sirket_adi = etiket.get_text(strip=True)
                if sirket_adi in gorulen_sirketler or len(sirket_adi) <= 3:
                    continue

                detay_linki = link["href"]
                if not detay_linki.startswith("https://halkarz.com/"):
                    continue

                satir = etiket.find_parent("li")
                kart_metni = (
                    satir.get_text(separator=" ", strip=True).lower() if satir else sirket_adi.lower()
                )

                if not any(b in kart_metni for b in ["yeni!", "talep toplan", "taslak", "onaylı", "yaklaşan", "hazırlanıyor", "işlem görüyor", "gong!"]):
                    continue

                gorulen_sirketler.add(sirket_adi)

                try:
                    sonuc = self._sirket_isle(sirket_adi, detay_linki, kart_metni, debug)
                except Exception:
                    logger.exception(f"{sirket_adi} işlenirken hata:")
                    continue

                if sonuc is None:
                    continue

                sirket_listesi.append(sonuc)
                logger.info(f"Yakaladı ({sonuc.durum.value}): {sirket_adi} — skor={sonuc.skor}")
                time.sleep(SETTINGS.ISTEK_ARASI_BEKLEME)

                if len(sirket_listesi) >= SETTINGS.MAX_SIRKET:
                    break

            logger.info(f"✅ Toplam {len(sirket_listesi)} şirket listelendi.")
            return sirket_listesi
        except Exception:
            logger.exception("Kritik scraper hatası!")
            return sirket_listesi


# ═══════════════════════════════════════════════════════════════════
# 🚀 6. FASTAPI UYGULAMASI
# ═══════════════════════════════════════════════════════════════════
app = FastAPI(title="Halka Arz Asistanı Pro")
score_analyzer = ScoreAnalyzer(WEIGHTS)
extractor = DataExtractor(score_analyzer)


@app.get("/api/halkarzlar", response_model=APIResponse)
def get_halka_arzlar(debug: bool = Query(False, description="True ise debug_bilgisi alanı eklenir.")):
    su_an = time.time()

    if not debug:
        with _CACHE_LOCK:
            if su_an - _CACHE["timestamp"] < SETTINGS.CACHE_TTL and _CACHE["data"]:
                logger.info("Cache hit.")
                return APIResponse(halka_arzlar=_CACHE["data"])

    veriler = extractor.analiz_et(debug=debug)

    if veriler and not debug:
        with _CACHE_LOCK:
            _CACHE["data"] = veriler
            _CACHE["timestamp"] = time.time()

    return APIResponse(halka_arzlar=veriler)


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("proje:app", host="0.0.0.0", port=port)