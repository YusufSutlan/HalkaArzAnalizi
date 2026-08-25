# config.py
import os
from enum import Enum
from dataclasses import dataclass
from typing import ClassVar, Optional


@dataclass(frozen=True)
class AppSettings:
    BASE_URL: str = os.environ.get("BASE_URL", "https://halkarz.com/")
    TIMEOUT: int = int(os.environ.get("TIMEOUT", "15"))
    CACHE_TTL: int = int(os.environ.get("CACHE_TTL", "300"))
    MAX_RETRY: int = int(os.environ.get("MAX_RETRY", "3"))
    MAX_SIRKET: int = int(os.environ.get("MAX_SIRKET", "15"))
    ISTEK_ARASI_BEKLEME: float = float(os.environ.get("ISTEK_ARASI_BEKLEME", "0.3"))
    ESZAMANLI_ISTEK_LIMITI: int = int(os.environ.get("ESZAMANLI_ISTEK_LIMITI", "5"))
    DEBUG_API_KEY: Optional[str] = os.environ.get("DEBUG_API_KEY")
    MIN_SKOR: float = float(os.environ.get("MIN_SKOR", "0.0"))
    ALLOWED_ORIGINS: str = os.environ.get("ALLOWED_ORIGINS", "*")
    PORT: int = int(os.environ.get("PORT", "8000"))


SETTINGS = AppSettings()


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
    BUYUKLUK = "Buyukluk"
    ISLEM_TARIHI = "IslemTarihi"
    ACIKLIK = "Aciklik"
    ISKONTO = "Iskonto"
    TAAHHUT = "Taahhut"
    HALKA_ARZ_SEKLI = "HalkaArzSekli"
    FON_KULLANIM = "FonKullanim"
    SATIS_YONTEMI = "SatisYontemi"
    FIYAT_ISTIKRARI = "FiyatIstikrari"
    PAY_SAYISI = "PaySayisi"
    FINANSAL_TABLO = "FinansalTablo"
    TAHSISAT = "Tahsisat"
    DAGITIM_TABLOSU = "DagitimTablosu"
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
    FAVOK = "Favok"
    NAKIT_AKISI = "NakitAkisi"


@dataclass(frozen=True)
class ScoreWeights:
    MAX: ClassVar[dict[Category, float]] = {
        Category.FINANSAL: 35.0,
        Category.DEGERLEME: 20.0,
        Category.FON_KULLANIM: 15.0,
        Category.ARZ_YAPISI: 10.0,
        Category.KURUMSALLIK: 10.0,
        Category.ISKONTO: 5.0,
        Category.SATMAMA: 3.0,
        Category.ACIKLIK: 2.0,
        Category.FIYAT_ISTIKRARI: 0.0,
    }
    FINANSAL_NET_KAR: float = 14.0
    FINANSAL_CARI: float = 9.0
    FINANSAL_BORC: float = 12.0
    DEGERLEME_FK: float = 12.0
    DEGERLEME_PDDD: float = 8.0


WEIGHTS = ScoreWeights()

YATIRIM_UYARISI = (
    "Bu değerlendirmeler otomatik bir algoritma tarafından, izahnamede yayınlanan "
    "bilgilerden üretilmiştir; yatırım tavsiyesi değildir. Kaynak veriler eksik veya "
    "hatalı olabilir; yatırım kararı vermeden önce izahnameyi ve KAP açıklamalarını "
    "kendiniz doğrulayın."
)