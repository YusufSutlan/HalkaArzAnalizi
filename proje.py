import os
import re
import time
import asyncio
import json
import logging
from datetime import datetime, date
from enum import Enum
from typing import Optional, ClassVar
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

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

# ÖNEMLİ NOT (kalibrasyon):
# Aşağıdaki "ilk gün satış baskısı" modeli, gözlemlenen bir mekanizmaya dayanır
# (büyük arz + eşit dağıtım -> kişi başı yüksek lot -> ilk gün kâr satışı).
# Ancak bu model HENÜZ GERİYE DÖNÜK TEST EDİLMEMİŞTİR. Eşikler
# ortam değişkenleriyle ayarlanabilir bırakıldı; geçmiş arz verisiyle
# doğrulanana kadar çıktı "tahmin" değil "uyarı" olarak sunulmalıdır.
MODEL_SURUMU = "3.0.0-kalibre-edilmemis"

# Sektör çarpanlarının hangi tarihe ait olduğu. Bayat veriyle kıyaslama
# yapmak sessizce yanlış sonuç üretir; bu yüzden tarih tutuluyor ve
# bayatladığında API yanıtında açıkça uyarı veriliyor.
SEKTOR_VERI_TARIHI = os.environ.get("SEKTOR_VERI_TARIHI", "2026-05-01")
SEKTOR_VERI_BAYATLAMA_GUNU = int(os.environ.get("SEKTOR_VERI_BAYATLAMA_GUNU", "120"))


@dataclass(frozen=True)
class AppSettings:
    BASE_URL: str = os.environ.get("BASE_URL", "https://halkarz.com/")
    TIMEOUT: int = int(os.environ.get("TIMEOUT", "15"))
    CACHE_TTL: int = int(os.environ.get("CACHE_TTL", "300"))
    MAX_RETRY: int = int(os.environ.get("MAX_RETRY", "3"))
    # 15 çok düşüktü: kaynak sitede 200+ şirket listeleniyor ve yeni
    # duyurulan arzlar bu sınırın dışında kalabiliyordu.
    MAX_SIRKET: int = int(os.environ.get("MAX_SIRKET", "40"))
    ISTEK_ARASI_BEKLEME: float = float(os.environ.get("ISTEK_ARASI_BEKLEME", "0.3"))
    ESZAMANLI_ISTEK_LIMITI: int = int(os.environ.get("ESZAMANLI_ISTEK_LIMITI", "5"))
    DEBUG_API_KEY: Optional[str] = os.environ.get("DEBUG_API_KEY")
    ALLOWED_ORIGINS: str = os.environ.get("ALLOWED_ORIGINS", "*")

    # ── İlk gün satış baskısı modeli eşikleri (ayarlanabilir) ──
    # Talep toplamanın tarihsel ortalama bireysel katılımcı sayısı.
    TAHMINI_KATILIMCI: int = int(os.environ.get("TAHMINI_KATILIMCI", "2500000"))
    # Bu tutarın üzerinde kişi başı dağıtım = yüksek kâr satışı baskısı.
    # KALİBRASYON NOTU: Türkiye'de tipik bir küçük arzda (~500 mn TL,
    # ~3 mn katılımcı) kişi başı dağıtım ~150-200 TL (1-2 lot) civarındadır.
    # 4-6 milyar TL'lik bir arzda ise bu tutar 1.500-3.000 TL'ye, yani
    # 10-20 katına çıkar. Satış baskısı yaratan asıl fark budur.
    # Bu eşikler geriye dönük test sonrası mutlaka güncellenmelidir.
    KISI_BASI_YUKSEK_TUTAR: float = float(os.environ.get("KISI_BASI_YUKSEK_TUTAR", "1200"))
    KISI_BASI_KRITIK_TUTAR: float = float(os.environ.get("KISI_BASI_KRITIK_TUTAR", "2500"))
    # "Büyük arz" sayılan eşikler (TL).
    BUYUK_ARZ_ESIGI: float = float(os.environ.get("BUYUK_ARZ_ESIGI", "3_000_000_000".replace("_", "")))
    COK_BUYUK_ARZ_ESIGI: float = float(os.environ.get("COK_BUYUK_ARZ_ESIGI", "5_000_000_000".replace("_", "")))


SETTINGS = AppSettings()


# ═══════════════════════════════════════════════════════════════════
# 🔤 2. ENUM'LAR VE AĞIRLIKLAR
# ═══════════════════════════════════════════════════════════════════

class Category(str, Enum):
    # DEĞİŞİKLİK: Eski tek parça "finansal" kategorisi, incelemede
    # önerilen dağılıma uygun şekilde 4 ayrı boyuta ayrıldı. Böylece
    # bir şirketin kârlı ama küçülüyor olması gibi durumlar skorda
    # birbirini maskelemiyor.
    KARLILIK = "karlilik"
    BUYUME = "buyume"
    BORC_YAPISI = "borc_yapisi"
    LIKIDITE = "likidite"
    DEGERLEME = "degerleme"
    NAKIT_AKISI = "nakit_akisi"
    FON_KULLANIM = "fon_kullanim"
    ARZ_YAPISI = "arz_yapisi"
    ISKONTO = "iskonto"
    ACIKLIK = "aciklik"
    SATMAMA = "satmama"
    KURUMSALLIK = "kurumsallik"


class ArzDurumu(str, Enum):
    # DEĞİŞİKLİK: Ana ekranda yalnızca aşağıdaki 5 durum gösteriliyor.
    # "Hazırlanıyor" ve "SPK Onaylı" kartları listeden çıkarıldı; bu
    # aşamadaki şirketlerin izahnamesi henüz yayınlanmadığı için zaten
    # fiyat/pay/finansal verileri boş geliyordu ve boş kart gösteriyorduk.
    # DEĞİŞİKLİK: "Hazırlanıyor" artık GÖSTERİLİYOR. Kaynak sitede
    # yeni duyurulan arzlar "Yeni!" rozetiyle ama "Hazırlanıyor..."
    # metniyle listeleniyor (Teknika Plast, Türker Vangölü, Kapeks
    # Kimya bu durumdaydı ve uygulamada hiç görünmüyorlardı).
    HAZIRLANIYOR = "Hazırlanıyor"
    # YENİ: Kaynak sitede "Ertelendi" rozetiyle işaretlenen arzlar
    # vardı (Bewen Enerji) ve bunlar normal bir arz gibi gösteriliyordu.
    ERTELENDI = "Ertelendi"
    # DÜZELTME: SPK onayı almış ama talep toplama tarihi henüz
    # açıklanmamış arzlar gizleniyordu. Yeni duyurulan halka arzların
    # çoğu ilk günlerde tam olarak bu durumda oluyor ve uygulamada
    # hiç görünmüyorlardı. Artık gösteriliyor.
    SPK_ONAYLI = "Talep Toplama Tarihi Bekleniyor"
    # Tarihi açıklanmış, başlamasına gün kalmış olanlar
    TALEP_YAKLASIYOR = "Talep Toplama Yaklaşıyor"
    TALEP_TOPLANIYOR = "Talep Toplanıyor"
    DAGITIM_BEKLENIYOR = "Dağıtım Bekleniyor"
    ISLEME_BEKLENIYOR = "İşleme Girmesi Bekleniyor"
    ISLEM_GORMEYE_BASLADI = "Borsada İşlem Görüyor"


# Listede sıralama önceliği: yatırımcıyı en çok ilgilendiren durum
# en üstte. Kaynak sitenin DOM sırası güvenilir değil.
DURUM_SIRASI: dict[str, int] = {
    "Talep Toplanıyor": 0,
    "Talep Toplama Yaklaşıyor": 1,
    "Talep Toplama Tarihi Bekleniyor": 2,
    "Hazırlanıyor": 3,
    "Dağıtım Bekleniyor": 4,
    "İşleme Girmesi Bekleniyor": 5,
    "Borsada İşlem Görüyor": 6,
    "Ertelendi": 7,
}

# Borsada işlem görmeye başlayan arzlar bu kadar gün sonra listeden
# düşer; aksi halde liste eski arzlarla dolup taşıyor.
# Borsada işlem görmeye başlayan arz kaç gün listede kalsın?
# 0 = SADECE işlem gününde görünür, ertesi gün listeden düşer.
ISLEM_GOREN_GOSTERIM_GUNU = int(os.environ.get("ISLEM_GOREN_GOSTERIM_GUNU", "0"))
# Talep toplaması bitmiş arzlar kaç gün sonra listeden düşsün?
# (Dağıtım/işleme girme süreci genelde 1-2 hafta sürer.)
ESKI_ARZ_GUNU = int(os.environ.get("ESKI_ARZ_GUNU", "14"))

# Ana ekranda gösterilecek durumlar. Buradan çıkarılan bir durum
# API yanıtına hiç girmez.
GOSTERILEN_DURUMLAR: set[str] = {
    ArzDurumu.HAZIRLANIYOR.value,
    # ERTELENDI listede GÖSTERİLMİYOR: kaynak sitede yıllar öncesine
    # ait ertelenmiş arzlar birikmiş (Dünya Varlık, Zorlu Yenilenebilir,
    # Koray Holding, Biteks...) ve bunlar listeyi dolduruyordu.
    # Durum yine tespit ediliyor; sadece listeye girmiyor.
    ArzDurumu.SPK_ONAYLI.value,
    ArzDurumu.TALEP_YAKLASIYOR.value,
    ArzDurumu.TALEP_TOPLANIYOR.value,
    ArzDurumu.DAGITIM_BEKLENIYOR.value,
    ArzDurumu.ISLEME_BEKLENIYOR.value,
    ArzDurumu.ISLEM_GORMEYE_BASLADI.value,
}


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
    # YENİ: incelemede eksik denilen metrikler
    FAALIYET_KARI = "FaaliyetKari"
    AMORTISMAN = "Amortisman"
    FAVOK = "Favok"
    FINANSAL_BORC = "FinansalBorc"
    NAKIT = "Nakit"
    ISLETME_NAKIT_AKISI = "IsletmeNakitAkisi"
    FINANSMAN_GIDERI = "FinansmanGideri"
    BRUT_KAR = "BrutKar"


@dataclass(frozen=True)
class ScoreWeights:
    """
    DEĞİŞİKLİK: Ağırlıklar incelemede önerilen dağılıma çekildi.
    Çekirdek 100 puan: Kârlılık 20 / Büyüme 15 / Borç 15 / Likidite 10 /
    Değerleme 20 / Fon Kullanımı 10 / Kurumsallık 5 / Arz Yapısı 5.
    Nakit akışı, iskonto, açıklık ve satmama ek boyutlar olarak eklendi;
    nihai skor zaten "elde edilen / ölçülebilen maksimum" şeklinde
    normalize edildiği için toplam 100'ü aşması sorun değil, önemli olan
    boyutlar arası göreli ağırlıktır.
    """
    MAX: ClassVar[dict[Category, float]] = {
        Category.KARLILIK: 20.0,
        Category.BUYUME: 15.0,
        Category.BORC_YAPISI: 15.0,
        Category.LIKIDITE: 10.0,
        Category.DEGERLEME: 20.0,
        Category.NAKIT_AKISI: 10.0,
        Category.FON_KULLANIM: 10.0,
        Category.KURUMSALLIK: 5.0,
        Category.ARZ_YAPISI: 5.0,
        Category.ISKONTO: 4.0,
        Category.ACIKLIK: 3.0,
        Category.SATMAMA: 3.0,
    }
    # Kârlılık alt kırılımı
    KARLILIK_ROE: float = 8.0
    KARLILIK_NET_MARJ: float = 6.0
    KARLILIK_FAALIYET_MARJ: float = 6.0
    # Büyüme alt kırılımı
    BUYUME_HASILAT: float = 8.0
    BUYUME_NET_KAR: float = 7.0
    # Borç alt kırılımı
    BORC_OZKAYNAK: float = 6.0
    BORC_NET_FAVOK: float = 5.0
    BORC_FAIZ_KARSILAMA: float = 4.0
    # Değerleme alt kırılımı
    DEGERLEME_FK: float = 8.0
    DEGERLEME_PDDD: float = 6.0
    DEGERLEME_FD_FAVOK: float = 6.0


WEIGHTS = ScoreWeights()


# Sektör bazlı çarpanlar VE borçluluk eşikleri.
# DEĞİŞİKLİK: incelemede "her sektör için eşikler farklı olmalı"
# denilmişti; borç eşikleri artık sektöre göre değişiyor.
SEKTOR_PROFILLERI: dict[str, dict[str, float]] = {
    "TEKNOLOJİ": {"fk": 18.0, "pddd": 4.5, "fd_favok": 12.0,
                  "borc_iyi": 0.4, "borc_kabul": 1.0, "net_borc_favok_iyi": 1.0},
    "ENERJİ":    {"fk": 14.0, "pddd": 2.5, "fd_favok": 8.0,
                  "borc_iyi": 1.0, "borc_kabul": 2.5, "net_borc_favok_iyi": 3.0},
    "SANAYİ":    {"fk": 10.0, "pddd": 1.8, "fd_favok": 7.0,
                  "borc_iyi": 0.8, "borc_kabul": 2.0, "net_borc_favok_iyi": 2.5},
    "GIDA":      {"fk": 12.0, "pddd": 2.0, "fd_favok": 8.0,
                  "borc_iyi": 0.8, "borc_kabul": 1.8, "net_borc_favok_iyi": 2.5},
    "GYO":       {"fk": 5.0,  "pddd": 0.8, "fd_favok": 12.0,
                  "borc_iyi": 1.0, "borc_kabul": 3.0, "net_borc_favok_iyi": 5.0},
    "FİNANS":    {"fk": 6.0,  "pddd": 1.2, "fd_favok": 0.0,
                  "borc_iyi": 4.0, "borc_kabul": 8.0, "net_borc_favok_iyi": 0.0},
    "İNŞAAT":    {"fk": 8.0,  "pddd": 1.5, "fd_favok": 7.0,
                  "borc_iyi": 1.0, "borc_kabul": 2.5, "net_borc_favok_iyi": 3.5},
    "PERAKENDE": {"fk": 14.0, "pddd": 3.0, "fd_favok": 9.0,
                  "borc_iyi": 0.7, "borc_kabul": 1.8, "net_borc_favok_iyi": 2.5},
    "GENEL":     {"fk": 12.0, "pddd": 2.0, "fd_favok": 8.0,
                  "borc_iyi": 0.7, "borc_kabul": 1.8, "net_borc_favok_iyi": 2.5},
}


# ═══════════════════════════════════════════════════════════════════
# 🔤 3. YARDIMCI ARAÇLAR
# ═══════════════════════════════════════════════════════════════════

AYLAR = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12
}


class TextUtils:
    @staticmethod
    def kucult(s: Optional[str]) -> str:
        """
        YENİ VE KRİTİK: Türkçe'ye güvenli küçük harfe çevirme.

        Python'un str.lower() metodu "İ" harfini "i" + birleşik nokta
        (U+0307) olarak çevirir. Yani:
            "Yurt İçi Bireysel".lower() -> "yurt i̇çi bireysel"
        ve bu metin "içi" ifadesiyle EŞLEŞMEZ. Aynı sorun "İnşaat",
        "İşletme", "İhraççı" gibi tüm kelimelerde yaşanıyordu; sektör
        tespiti ve tahsisat okuması bu yüzden sessizce yanlış çalışıyordu.
        Bu yüzden küçültmeden ÖNCE "İ" -> "i" dönüşümü yapılıyor.
        """
        return (s or "").replace("İ", "i").lower()

    @staticmethod
    def normalize(s: Optional[str]) -> str:
        s = TextUtils.kucult(s)
        return re.sub(r"\s+", " ", s).strip().rstrip(":").strip()

    @staticmethod
    def yuzde_bul(metin: Optional[str]) -> Optional[float]:
        if not metin:
            return None
        eslesme = re.search(r"%\s*(\d+[.,]?\d*)|(\d+[.,]?\d*)\s*%", metin)
        if not eslesme:
            return None
        try:
            return float((eslesme.group(1) or eslesme.group(2)).replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def sayi_bul(metin: Optional[str]) -> Optional[float]:
        sayilar = TextUtils.tum_sayilari_bul(metin)
        return sayilar[0] if sayilar else None

    @staticmethod
    def tum_sayilari_bul(metin: Optional[str]) -> list[float]:
        """
        YENİ: Bir metindeki TÜM sayıları sırayla döndürür.
        Çok dönemli finansal tablo (2023 / 2024 / 2025 kolonları)
        okuyabilmek için gerekli.
        Parantez içindeki değerler muhasebede negatiftir: (1.234) -> -1234.
        """
        if not metin:
            return []
        sonuc: list[float] = []
        # Parantezli negatifleri önce işaretle
        temiz = re.sub(r"\((\s*[\d.,]+\s*)\)", r"-\1", metin)
        for desen in (
            r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?",   # 1.234.567,89
            r"-?\d+,\d+",                        # 1234,56
            r"-?\d+\.\d+",                       # 1234.56
            r"-?\d+",                            # 1234
        ):
            bulunanlar = re.findall(desen, temiz)
            if bulunanlar:
                for b in bulunanlar:
                    try:
                        if "." in b and "," in b:
                            sonuc.append(float(b.replace(".", "").replace(",", ".")))
                        elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", b):
                            sonuc.append(float(b.replace(".", "")))
                        else:
                            sonuc.append(float(b.replace(",", ".")))
                    except ValueError:
                        continue
                break
        return sonuc

    @staticmethod
    def etiket_eslesir(baslik_norm: str, etiketler: list[str]) -> bool:
        """
        Kelime sınırına duyarlı eşleştirme. Saf substring kontrolü
        ("pay" etiketinin "Pay Sahipleri" başlığıyla eşleşmesi gibi)
        veri kirliliğine yol açıyordu.
        """
        if not baslik_norm:
            return False
        for e in etiketler:
            if baslik_norm == e:
                return True
            # "pay", "nakit" gibi çok kısa/genel etiketler bir başlığın
            # içinde ayrı kelime olarak geçse bile ("pay sahipleri
            # hakkında") kastedilen alan olmayabilir. Bu yüzden 5
            # karakterden kısa etiketlerde SADECE tam eşleşme kabul edilir.
            if len(e) < 5:
                continue
            if re.search(rf"(?:^|\s){re.escape(e)}(?:$|\s)", baslik_norm):
                return True
        return False

    @staticmethod
    def tutar_coz(metin: Optional[str]) -> Optional[float]:
        """
        YENİ: "4,5 Milyar TL", "450 Milyon TL", "4.500.000.000 TL" gibi
        ifadelerin hepsini TL cinsinden sayıya çevirir.
        Eskiden sadece sayi_bul kullanılıyordu; "4,5 Milyar TL" ifadesi
        4.5 olarak okunuyor ve arz büyüklüğü milyar yerine 4,5 TL
        sanılıyordu. Bu, satış baskısı ve tavan hesabını sessizce bozan
        ciddi bir hataydı.
        """
        if not metin:
            return None
        m = str(metin).lower()
        sayi = TextUtils.sayi_bul(m)
        if sayi is None:
            return None
        if re.search(r"\bmilyar\b|\bmlr\b", m):
            return sayi * 1_000_000_000
        if re.search(r"\bmilyon\b|\bmn\b", m):
            return sayi * 1_000_000
        if re.search(r"\bbin\b", m):
            return sayi * 1_000
        return sayi

    @staticmethod
    def sayi_formatla(deger: Optional[float]) -> str:
        """
        YENİ: Finansal tabloyu okunabilir hale getirmek için.
        1.234.567.890 -> "1,23 mlr" ; 45.600.000 -> "45,6 mn"
        Ham rakamların alt alta sıralanması tabloyu okunamaz kılıyordu.
        """
        if deger is None:
            return "-"
        isaret = "-" if deger < 0 else ""
        d = abs(deger)
        if d >= 1_000_000_000:
            return f"{isaret}{d / 1_000_000_000:,.2f} mlr".replace(".", "#").replace(",", ".").replace("#", ",")
        if d >= 1_000_000:
            return f"{isaret}{d / 1_000_000:,.1f} mn".replace(".", "#").replace(",", ".").replace("#", ",")
        if d >= 1_000:
            return f"{isaret}{d:,.0f}".replace(",", ".")
        return f"{isaret}{d:,.2f}".replace(".", "#").replace(",", ".").replace("#", ",")

    @staticmethod
    def tarih_araligi_coz(metin: Optional[str]) -> Optional[tuple[date, date]]:
        """
        YENİ / REFAKTÖR: "12-13-14 Ağustos 2026" veya "29-30 Temmuz 2026"
        gibi ifadeleri (başlangıç, bitiş) tarih çiftine çevirir.
        Eskiden bu mantık _durum_belirle içinde iki kez kopyalanmıştı ve
        ay geçişli aralıkları (örn. 30 Temmuz - 1 Ağustos) hiç
        desteklemiyordu.
        """
        if not metin:
            return None
        m = str(metin).lower().strip()
        if not m or m in ("-", "açıklanmadı", "belli değil"):
            return None

        yil_match = re.search(r"(20\d{2})", m)
        yil = int(yil_match.group(1)) if yil_match else datetime.now().year

        # Metinde geçen ayları sırayla topla
        ay_bulgular = [(m.index(ad), no) for ad, no in AYLAR.items() if ad in m]
        if not ay_bulgular:
            return None
        ay_bulgular.sort()

        # Ay adlarından ÖNCE gelen gün sayılarını al
        ilk_ay_pos, ilk_ay = ay_bulgular[0]
        son_ay_pos, son_ay = ay_bulgular[-1]

        onceki_gunler = [int(g) for g in re.findall(r"\b(\d{1,2})\b", m[:ilk_ay_pos])]
        if not onceki_gunler:
            onceki_gunler = [1]
        onceki_gunler = [g for g in onceki_gunler if 1 <= g <= 31] or [1]

        if len(ay_bulgular) > 1:
            # Ay geçişli aralık: "30 Temmuz - 1 Ağustos 2026"
            arasi = m[ilk_ay_pos:son_ay_pos]
            sonraki_gunler = [int(g) for g in re.findall(r"\b(\d{1,2})\b", arasi)]
            sonraki_gunler = [g for g in sonraki_gunler if 1 <= g <= 31] or [1]
            try:
                bas = date(yil, ilk_ay, min(onceki_gunler))
                bit = date(yil, son_ay, max(sonraki_gunler))
                if bit < bas:
                    bit = date(yil + 1, son_ay, max(sonraki_gunler))
                return (bas, bit)
            except ValueError:
                return None

        try:
            bas = date(yil, ilk_ay, min(onceki_gunler))
            bit = date(yil, ilk_ay, max(onceki_gunler))
            return (bas, bit)
        except ValueError:
            return None

    @staticmethod
    def sektor_verisi_bayat_mi() -> tuple[bool, int]:
        try:
            ref = datetime.strptime(SEKTOR_VERI_TARIHI, "%Y-%m-%d").date()
        except ValueError:
            return (True, 9999)
        gun = (datetime.now().date() - ref).days
        return (gun > SEKTOR_VERI_BAYATLAMA_GUNU, gun)


# ═══════════════════════════════════════════════════════════════════
# 💰 4. SKORLAMA VE ANALİZ MOTORU
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoreResult:
    score: float
    max_possible: float
    explanation: str
    has_data: bool


@dataclass
class PiyasaBaglami:
    """
    YENİ: Bir halka arzı tek başına değil, o haftaki diğer arzlarla
    birlikte değerlendirebilmek için taşınan bağlam.
    Aynı hafta birden fazla arz olması, toplam bireysel talebi böldüğü
    için kişi başı dağıtılan lot miktarını artırır -> ilk gün satış
    baskısı yükselir.
    """
    ayni_hafta_arz_sayisi: int = 1
    ayni_hafta_toplam_buyukluk: float = 0.0
    rakip_sirketler: list[str] = field(default_factory=list)


class ScoreAnalyzer:
    def __init__(self, weights: ScoreWeights = WEIGHTS):
        self.weights = weights
        # DEĞİŞİKLİK: incelemede "AR-GE, kapasite artırımı, satın alma
        # hepsi aynı puanı alıyor" denmişti. Artık fon kullanım
        # kalemleri etkilerine göre ağırlıklandırılıyor.
        # DÜZELTME: Liste eksikti. Çitlekçi'nin "%35 yeni şube yatırımı"
        # ve "%15 yeni depo yatırımı" kalemleri hiçbir anahtarla
        # eşleşmediği için SIFIR sayılıyordu — oysa bunlar doğrudan
        # büyüme yatırımı. Perakende/lojistik terimleri eklendi.
        self.FON_AGIRLIKLARI: list[tuple[list[str], float, str]] = [
            (["kapasite artırım", "yeni tesis", "yeni fabrika", "makine", "teçhizat",
              "üretim hattı", "tesis yatırım", "fabrika yatırım",
              "üretim tesisi", "yeni tesis", "ilave tesis",
              "otomasyon", "modernizasyon"], 1.0, "kapasite/üretim yatırımı"),
            (["yeni şube", "şube yatırım", "yeni mağaza", "mağaza yatırım",
              "şube aç", "mağaza aç", "yeni depo", "depo yatırım",
              "lojistik yatırım", "dağıtım ağı"], 0.95,
             "şube/depo yatırımı (organik büyüme)"),
            (["büyüme yatırım", "yatırımların finansmanı", "devam eden yatırım",
              "yeni yatırım"], 0.9, "büyüme yatırımı"),
            (["ar-ge", "arge", "ar ge", "patent", "yazılım geliştirme",
              "dijitalleşme", "teknoloji yatırımı"], 0.9, "Ar-Ge/teknoloji"),
            (["yurt dışı", "yurtdışı", "ihracat", "yeni pazar", "global"], 0.85,
             "yurt dışı büyüme"),
            (["yenilenebilir", "güneş", "rüzgar", "ges", "res", "enerji yatırımı"],
             0.85, "enerji yatırımı"),
            (["satın alma", "şirket alımı", "iştirak", "birleşme", "hisse alımı"],
             0.6, "satın alma (entegrasyon riski)"),
            (["işletme sermayesi", "çalışma sermayesi", "stok",
              "hammadde tedarik", "hammadde alım", "hammadde"], 0.5,
             "işletme sermayesi/hammadde"),
            (["gayrimenkul alım", "arsa", "bina alım", "ofis"], 0.35,
             "gayrimenkul alımı"),
        ]
        self.BORC_ANAHTAR = [
            "borç ödeme", "kredi kapama", "kredi ödeme", "finansal borç",
            "borçların kapatılması", "borç azaltım"
        ]
        self.SEKTOR_PROFILLERI = SEKTOR_PROFILLERI

    # ───────────────────────── Sektör ─────────────────────────

    # Sektör anahtar kelimeleri ve ağırlıkları.
    # DEĞİŞİKLİK: Eski sürüm "ilk eşleşen kazanır" mantığıyla çalışıyordu ve
    # TEKNOLOJİ listesi başta olduğu için, açıklamasında "ileri teknoloji" /
    # "finansal teknoloji" geçen Quick Sigorta gibi şirketler TEKNOLOJİ
    # sayılıyordu — metinde "sigorta" 10 kez geçmesine rağmen.
    # Artık ağırlıklı puanlama yapılıyor ve şirket ADI en güçlü sinyal.
    SEKTOR_ANAHTARLARI: ClassVar[dict[str, list[tuple[str, float]]]] = {
        "FİNANS": [("sigorta", 3.0), ("sigortacılık", 3.0), ("reasürans", 3.0),
                   ("emeklilik", 2.5), ("banka", 3.0), ("katılım bankası", 3.0),
                   ("faktoring", 3.0), ("finansal kiralama", 3.0), ("leasing", 2.5),
                   ("portföy yönetimi", 3.0), ("aracı kurumu", 2.0),
                   ("varlık yönetim", 2.0), ("finansman", 1.0), ("finans", 0.8)],
        "TEKNOLOJİ": [("yazılım", 2.5), ("bilişim", 2.5), ("siber güvenlik", 3.0),
                      ("yapay zeka", 2.0), ("savunma sanayi", 3.0),
                      ("teknoloji", 0.7), ("dijital", 0.4), ("elektronik", 1.5),
                      ("oyun", 1.5), ("veri merkezi", 2.0)],
        "GYO": [("gayrimenkul yatırım ortaklığı", 4.0), ("gyo", 3.0),
                ("emlak", 1.5), ("gayrimenkul", 0.8)],
        "ENERJİ": [("elektrik üretim", 3.0), ("yenilenebilir enerji", 3.0),
                   ("rüzgar enerji", 3.0), ("güneş enerji", 3.0),
                   ("jeotermal", 2.5), ("doğalgaz", 2.5), ("petrol", 2.0),
                   ("enerji", 1.0), ("elektrik", 0.8)],
        "İNŞAAT": [("müteahhit", 3.0), ("inşaat taahhüt", 3.0),
                   ("yapı işleri", 2.5), ("inşaat", 1.2)],
        "PERAKENDE": [("perakende", 3.0), ("zincir mağaza", 3.0),
                      ("e-ticaret", 2.5), ("market", 1.5), ("mağazacılık", 2.0)],
        "GIDA": [("gıda", 2.5), ("tarım", 2.0), ("hayvancılık", 2.5),
                 ("süt ürünleri", 2.5), ("içecek", 2.0), ("un ", 1.0),
                 ("yem ", 1.5)],
        "SANAYİ": [("çimento", 3.0), ("hazır beton", 3.0), ("demir çelik", 3.0),
                   ("otomotiv", 2.5), ("makine imalat", 2.5), ("kimya", 2.0),
                   ("plastik", 2.0), ("tekstil", 2.5), ("ambalaj", 2.0),
                   ("madencilik", 2.5), ("üretim tesisi", 1.5),
                   ("sanayi", 0.8), ("üretim", 0.4), ("makine", 0.6)],
    }

    # Şirket adında geçen sektör kelimeleri çok daha güvenilir bir sinyaldir.
    AD_AGIRLIGI: ClassVar[float] = 6.0

    # Her sayfada bulunan ve sektörle ilgisi olmayan kalıplar. Metinden
    # çıkarılmazsa "Garanti YATIRIM" -> FİNANS, "Satmama TAAHHÜDÜ" -> İNŞAAT
    # gibi yanlış sinyaller üretiyorlar.
    GURULTU_KALIPLARI: ClassVar[list[str]] = [
        r"aracı kurum[^\n]*", r"konsorsiyum lideri[^\n]*",
        r"satmama taahhüd[^\n]*", r"halka arz[^\n]*", r"tahsisat[^\n]*",
        r"fiyat istikrar[^\n]*", r"pazar\s*:[^\n]*", r"bist[^\n]*",
        r"izahname[^\n]*", r"spk bülteni[^\n]*", r"fiyat tespit raporu[^\n]*",
        r"yatırım menkul[^\n]*", r"menkul kıymetler[^\n]*",
        r"yurt içi bireysel[^\n]*", r"yurt içi kurumsal[^\n]*",
    ]

    def _get_sektor(self, metin: str, sirket_adi: str = "") -> str:
        """
        Ağırlıklı sektör tespiti.

        1) Şirket adı en güçlü sinyal ("Quick SİGORTA A.Ş." -> FİNANS)
        2) Sayfadaki sabit kalıplar (aracı kurum, tahsisat vb.) temizlenir
        3) Kalan metinde anahtar kelimeler ağırlıklı ve frekanslı sayılır
        """
        ad = TextUtils.kucult(sirket_adi)
        govde = TextUtils.kucult(metin)
        for kalip in self.GURULTU_KALIPLARI:
            govde = re.sub(kalip, " ", govde)

        puanlar: dict[str, float] = {}
        for sektor, anahtarlar in self.SEKTOR_ANAHTARLARI.items():
            toplam = 0.0
            for kelime, agirlik in anahtarlar:
                if ad and kelime in ad:
                    toplam += agirlik * self.AD_AGIRLIGI
                adet = govde.count(kelime)
                if adet:
                    # Frekansın etkisi sınırlı: 1 kez geçen kelime ile
                    # 50 kez geçen kelime arasında orantısız fark olmasın.
                    toplam += agirlik * min(adet, 6)
            if toplam:
                puanlar[sektor] = toplam

        if not puanlar:
            return "GENEL"
        en_iyi = max(puanlar.items(), key=lambda x: x[1])
        # Çok zayıf sinyalle sektör atamak, yanlış sektör ortalamasıyla
        # değerleme yapmaktan daha kötü; eşiğin altında GENEL kalıyor.
        return en_iyi[0] if en_iyi[1] >= 2.0 else "GENEL"

    def sektor_puan_dokumu(self, metin: str, sirket_adi: str = "") -> dict[str, float]:
        """Tanı amaçlı: hangi sektörün kaç puan aldığını gösterir."""
        ad = TextUtils.kucult(sirket_adi)
        govde = TextUtils.kucult(metin)
        for kalip in self.GURULTU_KALIPLARI:
            govde = re.sub(kalip, " ", govde)
        dokum: dict[str, float] = {}
        for sektor, anahtarlar in self.SEKTOR_ANAHTARLARI.items():
            t = 0.0
            for kelime, agirlik in anahtarlar:
                if ad and kelime in ad:
                    t += agirlik * self.AD_AGIRLIGI
                t += agirlik * min(govde.count(kelime), 6)
            if t:
                dokum[sektor] = round(t, 1)
        return dict(sorted(dokum.items(), key=lambda x: -x[1]))

    def _profil(self, sektor: str) -> dict[str, float]:
        return self.SEKTOR_PROFILLERI.get(sektor, self.SEKTOR_PROFILLERI["GENEL"])

    # ───────────────────── Yardımcı: FAVÖK ─────────────────────

    @staticmethod
    def _favok_hesapla(fin: dict) -> Optional[float]:
        """
        FAVÖK doğrudan tabloda yoksa Faaliyet Kârı + Amortisman'dan
        türetilir. İkisi de yoksa None döner ve FAVÖK'e dayalı
        puanlamalar hiç yapılmaz (uydurma yapılmaz).
        """
        favok = fin.get(FinKey.FAVOK)
        if favok is not None:
            return favok
        faaliyet = fin.get(FinKey.FAALIYET_KARI)
        amortisman = fin.get(FinKey.AMORTISMAN)
        if faaliyet is not None and amortisman is not None:
            return faaliyet + abs(amortisman)
        return None

    @staticmethod
    def _seri_buyume(seri: dict[int, float]) -> Optional[float]:
        """
        Yıl -> değer sözlüğünden yıllık bileşik büyüme (CAGR benzeri)
        yüzdesi üretir. En az 2 dönem gerekir.
        Negatiften pozitife geçişte CAGR anlamsız olduğu için basit
        yön bilgisi döndürülür.
        """
        if not seri or len(seri) < 2:
            return None
        yillar = sorted(seri.keys())
        ilk, son = seri[yillar[0]], seri[yillar[-1]]
        donem = len(yillar) - 1
        if ilk == 0:
            return None
        if ilk < 0 and son > 0:
            return 100.0  # zarardan kâra geçiş: güçlü pozitif sinyal
        if ilk > 0 and son < 0:
            return -100.0  # kârdan zarara geçiş: güçlü negatif sinyal
        if ilk < 0 and son < 0:
            return ((abs(ilk) - abs(son)) / abs(ilk)) * 100 / donem
        try:
            return (((son / ilk) ** (1 / donem)) - 1) * 100
        except (ValueError, ZeroDivisionError):
            return None

    # ───────────────────────── KÂRLILIK ─────────────────────────

    def karlilik_puanla(self, fin: dict, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        """
        DEĞİŞİKLİK: Eskiden ROE veya net marj'dan SADECE BİRİ
        hesaplanıyordu (elif). İncelemede "ROE tek başına yeterli değil"
        denilmişti; artık ROE + net marj + faaliyet marjı birlikte
        ölçülüyor. Böylece tek seferlik gelirle şişmiş net kâr, düşük
        faaliyet marjı üzerinden yakalanabiliyor.
        """
        puan, mx, aciklamalar, has_data = 0.0, 0.0, [], False

        net_kar = fin.get(FinKey.NET_KAR)
        hasilat = fin.get(FinKey.HASILAT)
        ozk = fin.get(FinKey.OZKAYNAK)
        faaliyet_kari = fin.get(FinKey.FAALIYET_KARI)

        if net_kar is not None and net_kar < 0:
            has_data = True
            mx += self.weights.KARLILIK_ROE
            aciklamalar.append(f"Şirket zararda ({net_kar:,.0f} TL).")
            kirmizi_bayraklar.append("🚨 Son açıklanan dönemde net zarar mevcut.")
            ceza_sozlugu["zarar"] = 18
        elif net_kar is not None and ozk is not None and ozk > 0:
            has_data = True
            mx += self.weights.KARLILIK_ROE
            roe = (net_kar / ozk) * 100
            if roe >= 30:
                puan += self.weights.KARLILIK_ROE
                aciklamalar.append(f"Çok güçlü özkaynak kârlılığı (ROE %{roe:.1f}).")
            elif roe >= 15:
                puan += self.weights.KARLILIK_ROE * 0.7
                aciklamalar.append(f"İyi özkaynak kârlılığı (ROE %{roe:.1f}).")
            elif roe >= 5:
                puan += self.weights.KARLILIK_ROE * 0.3
                aciklamalar.append(f"Zayıf özkaynak kârlılığı (ROE %{roe:.1f}).")
            else:
                aciklamalar.append(f"Çok düşük özkaynak kârlılığı (ROE %{roe:.1f}).")

        # Net kâr marjı — ROE'den bağımsız olarak ayrıca ölçülüyor
        if net_kar is not None and hasilat and hasilat > 0:
            has_data = True
            mx += self.weights.KARLILIK_NET_MARJ
            marj = (net_kar / hasilat) * 100
            if marj >= 20:
                puan += self.weights.KARLILIK_NET_MARJ
                aciklamalar.append(f"Yüksek net kâr marjı (%{marj:.1f}).")
            elif marj >= 8:
                puan += self.weights.KARLILIK_NET_MARJ * 0.6
                aciklamalar.append(f"Makul net kâr marjı (%{marj:.1f}).")
            elif marj > 0:
                puan += self.weights.KARLILIK_NET_MARJ * 0.2
                aciklamalar.append(f"İnce net kâr marjı (%{marj:.1f}).")
            else:
                aciklamalar.append(f"Negatif net marj (%{marj:.1f}).")

        # YENİ: Faaliyet marjı — esas faaliyet kârlılığının kalitesi
        if faaliyet_kari is not None and hasilat and hasilat > 0:
            has_data = True
            mx += self.weights.KARLILIK_FAALIYET_MARJ
            f_marj = (faaliyet_kari / hasilat) * 100
            if f_marj >= 15:
                puan += self.weights.KARLILIK_FAALIYET_MARJ
                aciklamalar.append(f"Güçlü esas faaliyet marjı (%{f_marj:.1f}).")
            elif f_marj >= 6:
                puan += self.weights.KARLILIK_FAALIYET_MARJ * 0.6
                aciklamalar.append(f"Makul faaliyet marjı (%{f_marj:.1f}).")
            elif f_marj > 0:
                puan += self.weights.KARLILIK_FAALIYET_MARJ * 0.2
                aciklamalar.append(f"Zayıf faaliyet marjı (%{f_marj:.1f}).")
            else:
                aciklamalar.append(f"Esas faaliyetten zarar (%{f_marj:.1f}).")
                kirmizi_bayraklar.append(
                    "🚨 Esas faaliyet zararı: net kâr faaliyet dışı kaynaklardan geliyor olabilir."
                )
                ceza_sozlugu["faaliyet_zarari"] = 10

            # YENİ: Net kâr faaliyet kârından belirgin şekilde büyükse,
            # kârın tek seferlik/faaliyet dışı olma ihtimali yüksek.
            if net_kar is not None and faaliyet_kari > 0 and net_kar > faaliyet_kari * 1.6:
                kirmizi_bayraklar.append(
                    "🚨 Net kâr, esas faaliyet kârının çok üzerinde: kârın önemli kısmı "
                    "tek seferlik/faaliyet dışı gelir olabilir."
                )
                ceza_sozlugu["tek_seferlik_kar"] = 6

        if not has_data:
            return ScoreResult(0, 0, "Kârlılık verisi bulunamadı.", False)
        return ScoreResult(round(puan, 1), mx, " ".join(aciklamalar), True)

    # ───────────────────────── BÜYÜME ─────────────────────────

    def buyume_puanla(self, seriler: dict, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        """
        YENİ BOYUT: İncelemedeki en önemli eksik. Hasılat ve net kâr
        eğilimi puanlanıyor. Çok dönemli veri yoksa bu boyut hiç
        puanlanmaz (has_data=False) — yokluğu, sıfır puan olarak
        cezalandırılmaz, sadece veri güvenilirliğini düşürür.
        """
        puan, mx, aciklamalar, has_data = 0.0, 0.0, [], False

        hasilat_serisi = seriler.get(FinKey.HASILAT, {})
        kar_serisi = seriler.get(FinKey.NET_KAR, {})

        h_buyume = self._seri_buyume(hasilat_serisi)
        if h_buyume is not None:
            has_data = True
            mx += self.weights.BUYUME_HASILAT
            donem = len(hasilat_serisi)
            if h_buyume >= 50:
                puan += self.weights.BUYUME_HASILAT
                aciklamalar.append(f"Hasılat güçlü büyüyor (yıllık ~%{h_buyume:.0f}, {donem} dönem).")
            elif h_buyume >= 20:
                puan += self.weights.BUYUME_HASILAT * 0.7
                aciklamalar.append(f"Hasılat büyüyor (yıllık ~%{h_buyume:.0f}).")
            elif h_buyume >= 0:
                puan += self.weights.BUYUME_HASILAT * 0.25
                aciklamalar.append(
                    f"Hasılat yatay/enflasyon altı büyüyor (yıllık ~%{h_buyume:.0f})."
                )
            else:
                aciklamalar.append(f"Hasılat daralıyor (yıllık ~%{h_buyume:.0f}).")
                kirmizi_bayraklar.append("🚨 Hasılat son dönemlerde daralma eğiliminde.")
                ceza_sozlugu["hasilat_daralmasi"] = 8

        k_buyume = self._seri_buyume(kar_serisi)
        if k_buyume is not None:
            has_data = True
            mx += self.weights.BUYUME_NET_KAR
            if k_buyume >= 40:
                puan += self.weights.BUYUME_NET_KAR
                aciklamalar.append(f"Net kâr hızlı büyüyor (yıllık ~%{k_buyume:.0f}).")
            elif k_buyume >= 15:
                puan += self.weights.BUYUME_NET_KAR * 0.7
                aciklamalar.append(f"Net kâr büyüyor (yıllık ~%{k_buyume:.0f}).")
            elif k_buyume >= 0:
                puan += self.weights.BUYUME_NET_KAR * 0.25
                aciklamalar.append(f"Net kâr yatay (yıllık ~%{k_buyume:.0f}).")
            else:
                aciklamalar.append(f"Net kâr geriliyor (yıllık ~%{k_buyume:.0f}).")
                kirmizi_bayraklar.append("🚨 Net kâr son dönemlerde azalma eğiliminde.")
                ceza_sozlugu["kar_daralmasi"] = 8

        if not has_data:
            return ScoreResult(
                0, 0,
                "Çok dönemli veri bulunamadığı için büyüme trendi ölçülemedi.",
                False
            )
        return ScoreResult(round(puan, 1), mx, " ".join(aciklamalar), True)

    # ───────────────────────── BORÇ YAPISI ─────────────────────────

    def borc_puanla(self, fin: dict, sektor: str, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        """
        DEĞİŞİKLİK: Borç/Özkaynak eşiği artık sektöre göre değişiyor
        (bir bankanın 1.5'i ile bir yazılım şirketinin 1.5'i aynı şey
        değil). YENİ: Net Borç/FAVÖK ve faiz karşılama oranı eklendi.
        """
        puan, mx, aciklamalar, has_data = 0.0, 0.0, [], False
        prof = self._profil(sektor)

        ozk = fin.get(FinKey.OZKAYNAK)
        borc = fin.get(FinKey.TOPLAM_BORC)
        favok = self._favok_hesapla(fin)
        finansal_borc = fin.get(FinKey.FINANSAL_BORC)
        nakit = fin.get(FinKey.NAKIT)
        finansman_gideri = fin.get(FinKey.FINANSMAN_GIDERI)
        faaliyet_kari = fin.get(FinKey.FAALIYET_KARI)

        if ozk is not None and ozk <= 0:
            has_data = True
            mx += self.weights.BORC_OZKAYNAK
            kirmizi_bayraklar.append("🚨 Özsermaye negatif (teknik iflas göstergesi).")
            ceza_sozlugu["negatif_ozsermaye"] = 30
            aciklamalar.append("Özsermaye negatif.")
        elif borc is not None and ozk:
            has_data = True
            mx += self.weights.BORC_OZKAYNAK
            oran = borc / ozk
            if oran <= prof["borc_iyi"]:
                puan += self.weights.BORC_OZKAYNAK
                aciklamalar.append(
                    f"Sektörüne göre düşük borçluluk (Borç/Özk {oran:.2f}, {sektor} eşiği {prof['borc_iyi']:.1f})."
                )
            elif oran <= prof["borc_kabul"]:
                puan += self.weights.BORC_OZKAYNAK * 0.6
                aciklamalar.append(f"Kabul edilebilir borçluluk (Borç/Özk {oran:.2f}).")
            else:
                aciklamalar.append(
                    f"Sektör eşiğinin üzerinde borç yükü (Borç/Özk {oran:.2f} > {prof['borc_kabul']:.1f})."
                )
                if oran >= prof["borc_kabul"] * 2:
                    kirmizi_bayraklar.append(
                        f"🚨 Kritik borçluluk (Borç/Özkaynak {oran:.1f}, {sektor} için çok yüksek)."
                    )
                    ceza_sozlugu["kritik_borc"] = 15

        # YENİ: Net Borç / FAVÖK — profesyonellerin birincil borç metriği
        if favok and favok > 0 and finansal_borc is not None:
            has_data = True
            mx += self.weights.BORC_NET_FAVOK
            net_borc = finansal_borc - (nakit or 0)
            oran = net_borc / favok
            esik = prof["net_borc_favok_iyi"]
            if oran <= 0:
                puan += self.weights.BORC_NET_FAVOK
                aciklamalar.append("Net nakit pozisyonu (finansal borç nakitten az).")
            elif oran <= esik:
                puan += self.weights.BORC_NET_FAVOK
                aciklamalar.append(f"Sağlıklı borç çevirme kapasitesi (Net Borç/FAVÖK {oran:.1f}).")
            elif oran <= esik * 2:
                puan += self.weights.BORC_NET_FAVOK * 0.5
                aciklamalar.append(f"Orta düzey borç yükü (Net Borç/FAVÖK {oran:.1f}).")
            else:
                aciklamalar.append(f"Ağır borç yükü (Net Borç/FAVÖK {oran:.1f}).")
                kirmizi_bayraklar.append(
                    f"🚨 Net Borç/FAVÖK {oran:.1f}: borç, nakit yaratma kapasitesine göre yüksek."
                )
                ceza_sozlugu["net_borc_favok"] = 10

        # YENİ: Faiz karşılama oranı
        if faaliyet_kari is not None and finansman_gideri and abs(finansman_gideri) > 0:
            has_data = True
            mx += self.weights.BORC_FAIZ_KARSILAMA
            karsilama = faaliyet_kari / abs(finansman_gideri)
            if karsilama >= 5:
                puan += self.weights.BORC_FAIZ_KARSILAMA
                aciklamalar.append(f"Faiz yükü rahat karşılanıyor ({karsilama:.1f}x).")
            elif karsilama >= 2:
                puan += self.weights.BORC_FAIZ_KARSILAMA * 0.6
                aciklamalar.append(f"Faiz karşılama yeterli ({karsilama:.1f}x).")
            elif karsilama >= 1:
                puan += self.weights.BORC_FAIZ_KARSILAMA * 0.2
                aciklamalar.append(f"Faiz karşılama sınırda ({karsilama:.1f}x).")
            else:
                aciklamalar.append(f"Faaliyet kârı faiz giderini karşılamıyor ({karsilama:.1f}x).")
                kirmizi_bayraklar.append(
                    "🚨 Esas faaliyet kârı finansman giderlerini karşılamıyor."
                )
                ceza_sozlugu["faiz_karsilama"] = 12

        if not has_data:
            return ScoreResult(0, 0, "Borç yapısı verisi bulunamadı.", False)
        return ScoreResult(round(puan, 1), mx, " ".join(aciklamalar), True)

    # ───────────────────────── LİKİDİTE ─────────────────────────

    def likidite_puanla(self, fin: dict, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        mx = self.weights.MAX[Category.LIKIDITE]
        donen = fin.get(FinKey.DONEN_VARLIK)
        kv = fin.get(FinKey.KISA_VADELI_YUKUMLULUK)
        if donen is None or not kv or kv <= 0:
            return ScoreResult(0, 0, "Likidite verisi bulunamadı.", False)

        oran = donen / kv
        if oran >= 2.0:
            return ScoreResult(mx, mx, f"Güçlü likidite (Cari oran {oran:.2f}).", True)
        if oran >= 1.2:
            return ScoreResult(mx * 0.7, mx, f"Dengeli likidite (Cari oran {oran:.2f}).", True)
        if oran >= 1.0:
            return ScoreResult(mx * 0.3, mx, f"Sınırda likidite (Cari oran {oran:.2f}).", True)
        kirmizi_bayraklar.append(
            f"🚨 Cari oran 1'in altında ({oran:.2f}): kısa vadeli yükümlülükler dönen varlıkları aşıyor."
        )
        ceza_sozlugu["likidite"] = 8
        return ScoreResult(0, mx, f"Likidite riski (Cari oran {oran:.2f}).", True)

    # ───────────────────────── NAKİT AKIŞI ─────────────────────────

    def nakit_akisi_puanla(self, fin: dict, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        """
        YENİ BOYUT: İncelemedeki Problem 5. Kâğıt üzerinde kâr açıklayan
        ama faaliyetlerinden nakit yakan şirketi yakalar.
        """
        mx = self.weights.MAX[Category.NAKIT_AKISI]
        nakit_akisi = fin.get(FinKey.ISLETME_NAKIT_AKISI)
        net_kar = fin.get(FinKey.NET_KAR)

        if nakit_akisi is None:
            return ScoreResult(0, 0, "İşletme nakit akışı verisi bulunamadı.", False)

        if nakit_akisi < 0:
            kirmizi_bayraklar.append(
                "🚨 İşletme faaliyetlerinden nakit çıkışı var: şirket kâr açıklasa bile nakit yakıyor."
            )
            ceza_sozlugu["negatif_nakit_akisi"] = 14
            return ScoreResult(0, mx, f"Negatif işletme nakit akışı ({nakit_akisi:,.0f} TL).", True)

        if net_kar is not None and net_kar > 0:
            kalite = nakit_akisi / net_kar
            if kalite >= 1.0:
                return ScoreResult(mx, mx,
                                   f"Kâr nakde dönüşüyor (Nakit akışı/Net kâr {kalite:.2f}).", True)
            if kalite >= 0.6:
                return ScoreResult(mx * 0.65, mx,
                                   f"Kârın çoğu nakde dönüşüyor ({kalite:.2f}).", True)
            ceza_sozlugu["dusuk_nakit_kalitesi"] = 5
            return ScoreResult(mx * 0.2, mx,
                               f"Kârın nakde dönüşümü zayıf ({kalite:.2f}) — alacak/stok birikimi olabilir.",
                               True)

        return ScoreResult(mx * 0.6, mx, "Pozitif işletme nakit akışı.", True)

    # ───────────────────────── DEĞERLEME ─────────────────────────

    def degerleme_carpanlari(self, market_cap: Optional[float], fin: dict,
                             sektor: str) -> dict:
        """
        YENİ: F/K, PD/DD ve FD/FAVÖK oranlarını sektör ortalamasıyla
        birlikte, KULLANICIYA GÖSTERİLECEK biçimde döndürür.

        Puanlama zaten bu oranları hesaplıyordu ama sonuç sadece skora
        gömülüyordu. Yatırımcının "bu fiyat pahalı mı ucuz mu?"
        sorusuna doğrudan cevap görmesi gerekir.
        """
        prof = self._profil(sektor)
        bayat, _ = TextUtils.sektor_verisi_bayat_mi()
        sonuc: dict = {
            "sektor": sektor,
            "sektor_verisi_bayat": bayat,
            "carpanlar": [],
            "genel_yorum": "",
        }
        if not market_cap:
            sonuc["genel_yorum"] = (
                "Piyasa değeri hesaplanamadığı için değerleme yapılamadı "
                "(fiyat veya pay sayısı eksik)."
            )
            return sonuc

        sonuc["piyasa_degeri"] = round(market_cap, 0)
        sonuc["piyasa_degeri_notu"] = (
            "Şirketin tamamının değeri (halka arz edilen kısım değil)."
        )
        net_kar = fin.get(FinKey.NET_KAR)
        ozk = fin.get(FinKey.OZKAYNAK)
        favok = self._favok_hesapla(fin)
        fin_borc = fin.get(FinKey.FINANSAL_BORC)
        nakit = fin.get(FinKey.NAKIT)

        def _yorumla(deger: float, ortalama: float, ters: bool = False) -> tuple[str, float]:
            """Sektör ortalamasına göre ucuz/pahalı yorumu ve fark yüzdesi."""
            if ortalama <= 0:
                return ("Karşılaştırma yapılamadı", 0.0)
            fark = ((ortalama - deger) / ortalama) * 100
            if fark >= 30:
                return ("Belirgin iskontolu", fark)
            if fark >= 10:
                return ("Sektör altında (ucuz)", fark)
            if fark >= -10:
                return ("Sektör ortalamasında", fark)
            if fark >= -30:
                return ("Sektör üzerinde (primli)", fark)
            return ("Belirgin primli (pahalı)", fark)

        if net_kar and net_kar > 0:
            fk = market_cap / net_kar
            if 0.5 <= fk <= 300:
                yorum, fark = _yorumla(fk, prof["fk"])
                sonuc["carpanlar"].append({
                    "ad": "F/K", "tam_ad": "Fiyat / Kazanç",
                    "deger": round(fk, 1), "sektor_ortalamasi": prof["fk"],
                    "yorum": yorum, "fark_yuzde": round(fark, 0),
                    "aciklama": (
                        f"Şirketin piyasa değeri, yıllık net kârının "
                        f"{fk:.1f} katı. Sektör ortalaması ~{prof['fk']:.0f}."
                    ),
                })
        elif net_kar is not None and net_kar <= 0:
            sonuc["carpanlar"].append({
                "ad": "F/K", "tam_ad": "Fiyat / Kazanç",
                "deger": None, "sektor_ortalamasi": prof["fk"],
                "yorum": "Hesaplanamaz (şirket zararda)",
                "fark_yuzde": None,
                "aciklama": "Şirket zarar ettiği için F/K oranı anlamlı değil.",
            })

        if ozk and ozk > 0:
            pddd = market_cap / ozk
            if 0.05 <= pddd <= 100:
                yorum, fark = _yorumla(pddd, prof["pddd"])
                sonuc["carpanlar"].append({
                    "ad": "PD/DD", "tam_ad": "Piyasa Değeri / Defter Değeri",
                    "deger": round(pddd, 2), "sektor_ortalamasi": prof["pddd"],
                    "yorum": yorum, "fark_yuzde": round(fark, 0),
                    "aciklama": (
                        f"Şirkete özkaynağının {pddd:.2f} katı değer biçilmiş. "
                        f"Sektör ortalaması ~{prof['pddd']:.1f}."
                    ),
                })

        if favok and favok > 0 and prof["fd_favok"] > 0:
            fd = market_cap + (fin_borc or 0) - (nakit or 0)
            fd_favok = fd / favok
            if 0.5 <= fd_favok <= 100:
                yorum, fark = _yorumla(fd_favok, prof["fd_favok"])
                sonuc["carpanlar"].append({
                    "ad": "FD/FAVÖK", "tam_ad": "Firma Değeri / FAVÖK",
                    "deger": round(fd_favok, 1),
                    "sektor_ortalamasi": prof["fd_favok"],
                    "yorum": yorum, "fark_yuzde": round(fark, 0),
                    "aciklama": (
                        f"Borç dahil firma değeri, faaliyet nakit üretiminin "
                        f"{fd_favok:.1f} katı. Sektör ortalaması ~{prof['fd_favok']:.0f}."
                    ),
                })

        olculen = [c for c in sonuc["carpanlar"] if c.get("fark_yuzde") is not None]
        if not olculen:
            sonuc["genel_yorum"] = (
                "Değerleme oranları hesaplanamadı; izahnamede yeterli "
                "finansal veri bulunamadı."
            )
        else:
            ort_fark = sum(c["fark_yuzde"] for c in olculen) / len(olculen)
            if ort_fark >= 25:
                sonuc["genel_yorum"] = (
                    "Halka arz fiyatı, sektör benzerlerine göre İSKONTOLU görünüyor."
                )
            elif ort_fark >= 8:
                sonuc["genel_yorum"] = (
                    "Halka arz fiyatı, sektör ortalamasının bir miktar ALTINDA."
                )
            elif ort_fark >= -8:
                sonuc["genel_yorum"] = (
                    "Halka arz fiyatı, sektör ortalamasına YAKIN."
                )
            elif ort_fark >= -25:
                sonuc["genel_yorum"] = (
                    "Halka arz fiyatı, sektör ortalamasının ÜZERİNDE (primli)."
                )
            else:
                sonuc["genel_yorum"] = (
                    "Halka arz fiyatı, sektör benzerlerine göre PAHALI görünüyor."
                )
            if bayat:
                sonuc["genel_yorum"] += (
                    " (Sektör ortalamaları güncel olmayabilir.)"
                )
        return sonuc

    def degerleme_puanla(self, market_cap: Optional[float], fin: dict, sektor: str,
                         kirmizi_bayraklar: list) -> ScoreResult:
        """
        DEĞİŞİKLİK: F/K ve PD/DD yanına FD/FAVÖK eklendi.
        Ayrıca sektör çarpanları bayatladığında bu boyutun ağırlığı
        düşürülüyor — güncelliği bilinmeyen bir referansla kesin
        hüküm vermemek için.
        """
        puan, mx, aciklamalar, has_data = 0.0, 0.0, [], False
        prof = self._profil(sektor)
        bayat, _ = TextUtils.sektor_verisi_bayat_mi()
        guven_carpani = 0.6 if bayat else 1.0

        if not market_cap:
            return ScoreResult(0, 0, "Piyasa değeri hesaplanamadı (fiyat/pay sayısı eksik).", False)

        net_kar = fin.get(FinKey.NET_KAR)
        ozk = fin.get(FinKey.OZKAYNAK)
        favok = self._favok_hesapla(fin)
        finansal_borc = fin.get(FinKey.FINANSAL_BORC)
        nakit = fin.get(FinKey.NAKIT)

        if net_kar and net_kar > 0:
            fk = market_cap / net_kar
            if 1.0 <= fk <= 250.0:
                has_data = True
                agirlik = self.weights.DEGERLEME_FK * guven_carpani
                mx += agirlik
                ort = prof["fk"]
                fark = ((ort - fk) / ort) * 100
                if fark > 15:
                    puan += agirlik
                    aciklamalar.append(f"Sektör ortalamasına göre iskontolu F/K ({fk:.1f} vs ~{ort:.0f}).")
                elif -15 <= fark <= 15:
                    puan += agirlik * 0.6
                    aciklamalar.append(f"Sektör ortalamasına yakın F/K ({fk:.1f}).")
                else:
                    aciklamalar.append(f"Sektörüne göre primli F/K ({fk:.1f} vs ~{ort:.0f}).")
                    if fark < -50:
                        kirmizi_bayraklar.append(
                            f"🚨 F/K oranı sektör ortalamasının çok üzerinde ({fk:.1f})."
                        )

        if ozk and ozk > 0:
            pddd = market_cap / ozk
            if 0.2 <= pddd <= 50.0:
                has_data = True
                agirlik = self.weights.DEGERLEME_PDDD * guven_carpani
                mx += agirlik
                ort = prof["pddd"]
                fark = ((ort - pddd) / ort) * 100
                if fark > 10:
                    puan += agirlik
                    aciklamalar.append(f"Cazip PD/DD ({pddd:.2f} vs ~{ort:.1f}).")
                elif -10 <= fark <= 10:
                    puan += agirlik * 0.6
                    aciklamalar.append(f"Makul PD/DD ({pddd:.2f}).")
                else:
                    aciklamalar.append(f"Yüksek PD/DD ({pddd:.2f} vs ~{ort:.1f}).")

        # YENİ: FD/FAVÖK — sermaye yapısından bağımsız değerleme çarpanı
        if favok and favok > 0 and prof["fd_favok"] > 0:
            firma_degeri = market_cap + (finansal_borc or 0) - (nakit or 0)
            fd_favok = firma_degeri / favok
            if 0.5 <= fd_favok <= 60:
                has_data = True
                agirlik = self.weights.DEGERLEME_FD_FAVOK * guven_carpani
                mx += agirlik
                ort = prof["fd_favok"]
                fark = ((ort - fd_favok) / ort) * 100
                if fark > 15:
                    puan += agirlik
                    aciklamalar.append(f"FD/FAVÖK sektör altında ({fd_favok:.1f} vs ~{ort:.0f}).")
                elif -15 <= fark <= 15:
                    puan += agirlik * 0.6
                    aciklamalar.append(f"FD/FAVÖK sektör ortalamasında ({fd_favok:.1f}).")
                else:
                    aciklamalar.append(f"FD/FAVÖK sektör üzerinde ({fd_favok:.1f} vs ~{ort:.0f}).")

        if not has_data:
            return ScoreResult(0, 0, "Değerleme çarpanları hesaplanamadı.", False)

        if bayat:
            aciklamalar.append("(Sektör çarpanları güncel olmadığı için bu boyutun ağırlığı düşürüldü.)")
        return ScoreResult(round(puan, 1), mx, " ".join(aciklamalar), True)

    # ───────────────────────── FON KULLANIMI ─────────────────────────

    def fon_kullanim_puanla(self, metin: str, fin: dict, kirmizi_bayraklar: list) -> ScoreResult:
        """
        DEĞİŞİKLİK: Fon kalemleri artık etkilerine göre farklı
        ağırlıklarla puanlanıyor (kapasite yatırımı > satın alma >
        işletme sermayesi).
        """
        mx = self.weights.MAX[Category.FON_KULLANIM]
        if not metin or TextUtils.normalize(metin) in ("-", "açıklanmadı", ""):
            return ScoreResult(0, 0, "Fon kullanım yeri açıklanmamış.", False)

        satirlar = [s for s in TextUtils.kucult(metin).split("\n") if s.strip()]
        agirlikli_buyume, borc_toplam, herhangi_yuzde = 0.0, 0.0, False
        kalem_notlari: list[str] = []

        for satir in satirlar:
            yuzde = TextUtils.yuzde_bul(satir)
            if yuzde is None:
                continue
            herhangi_yuzde = True
            eslesti = False
            for anahtarlar, katsayi, etiket in self.FON_AGIRLIKLARI:
                if any(k in satir for k in anahtarlar):
                    agirlikli_buyume += yuzde * katsayi
                    kalem_notlari.append(f"%{yuzde:.0f} {etiket}")
                    eslesti = True
                    break
            if not eslesti and any(k in satir for k in self.BORC_ANAHTAR):
                borc_toplam += yuzde
                kalem_notlari.append(f"%{yuzde:.0f} borç ödeme")

        if not herhangi_yuzde:
            return ScoreResult(0, 0, "Fon dağılımı oransal olarak belirtilmemiş.", False)

        borc = fin.get(FinKey.TOPLAM_BORC)
        ozk = fin.get(FinKey.OZKAYNAK)
        asiri_borclu = bool(borc and ozk and ozk > 0 and (borc / ozk) > 2.0)

        if asiri_borclu and borc_toplam > 30:
            return ScoreResult(
                round(mx * 0.8, 1), mx,
                f"Ağır borç yükünü hafifletmek için fonun ~%{borc_toplam:.0f}'i borç ödemeye ayrılmış "
                f"(bu durumda olumlu). {', '.join(kalem_notlari[:4])}",
                True
            )

        if borc_toplam >= 70:
            kirmizi_bayraklar.append(
                f"🚨 Halka arz gelirinin ~%{borc_toplam:.0f}'i borç ödemeye gidiyor, büyümeye değil."
            )

        # ═══ DÜZELTME: CEZA YERİNE AĞIRLIKLI ORTALAMA ═══
        #
        # Eski formül borç ödemesini büyüme katkısından ÇIKARIYORDU ve
        # ceza katsayısı çok dikti. Teknika Plast örneği: %30 büyüme
        # yatırımı + %40 işletme sermayesi + %30 borç ödeme kombinasyonu
        # 0,19/10 puan alıyordu. Yani makul bir dağılım, sıfır sayılıyordu.
        #
        # Oysa borç azaltmak da şirketi güçlendirir: faiz yükünü düşürür,
        # özkaynak kârlılığını artırır. Büyüme yatırımı kadar değerli
        # değildir ama CEZA da değildir.
        #
        # Artık her kalem kendi katsayısıyla ağırlıklı ortalamaya giriyor.
        # Borç ödemesi 0,40 katsayı alıyor (işletme sermayesine yakın,
        # kapasite yatırımının altında). Ceza yalnızca borç ödemesi
        # BASKIN hale geldiğinde (>%50) uygulanıyor — o zaman gerçekten
        # "halka arz parası büyümeye değil borca gidiyor" demektir.
        BORC_KATSAYISI = 0.40
        toplam_agirlikli = agirlikli_buyume + (borc_toplam * BORC_KATSAYISI)
        toplam_oran = sum(
            TextUtils.yuzde_bul(s) or 0 for s in satirlar
            if TextUtils.yuzde_bul(s) is not None
        ) or 100.0

        # Kalemlerin ortalama kalitesi (0-1 arası)
        kalite = toplam_agirlikli / max(toplam_oran, 1.0)
        puan = min(mx, kalite * mx)

        # Borç ödemesi baskınsa ek ceza
        if borc_toplam > 50:
            asim = (borc_toplam - 50) / 50.0
            puan = max(0.0, puan - asim * mx * 0.5)

        return ScoreResult(
            round(puan, 1), mx,
            f"Kalemler: {', '.join(kalem_notlari[:5])}. "
            f"Büyüme/yatırım ~%{min(agirlikli_buyume, 100):.0f} ağırlıklı katkı, "
            f"borç ödeme ~%{min(borc_toplam, 100):.0f}.",
            True
        )

    # ───────────────────── DİĞER BOYUTLAR ─────────────────────

    def arz_yapisi_puanla(self, metin: str, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        mx = self.weights.MAX[Category.ARZ_YAPISI]
        if not metin or TextUtils.normalize(metin) in ("-", "açıklanmadı"):
            return ScoreResult(0, 0, "Arz yapısı belirsiz.", False)

        m_lower = TextUtils.kucult(metin)
        olumsuz = re.sub(
            r"(ortak satış[ıi]?|mevcut pay satış[ıi]?)\s*[^.]{0,15}\b(yok|bulunmuyor|bulunmamaktadır)\b",
            "", m_lower
        )
        ortak = "ortak satış" in olumsuz or "mevcut pay satış" in olumsuz

        if "sermaye artırımı" in m_lower and not ortak:
            return ScoreResult(mx, mx, "Tamamen sermaye artırımı (fon şirkete giriyor).", True)
        if "sermaye artırımı" in m_lower and ortak:
            return ScoreResult(mx * 0.4, mx, "Kısmi ortak satışı mevcut.", True)
        if ortak:
            kirmizi_bayraklar.append(
                "🚨 Tamamen ortak satışı: halka arz geliri şirketin kasasına girmiyor."
            )
            ceza_sozlugu["ortak_satis"] = 15
            return ScoreResult(0, mx, "Tamamen ortak satışı.", True)
        return ScoreResult(0, 0, "Arz yapısı çözümlenemedi.", False)

    def iskonto_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ISKONTO]
        isk = TextUtils.yuzde_bul(metin)
        if isk is None:
            return ScoreResult(0, 0, "İskonto belirtilmemiş.", False)
        mult = 1.0 if isk >= 25 else 0.7 if isk >= 20 else 0.3 if isk >= 15 else 0.0
        return ScoreResult(round(mx * mult, 1), mx, f"Halka arz iskontosu %{isk:.0f}.", True)

    def aciklik_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ACIKLIK]
        a = TextUtils.yuzde_bul(metin)
        if a is None:
            return ScoreResult(0, 0, "Halka açıklık oranı belirtilmemiş.", False)
        if a < 10:
            p, notu = 0.0, "çok dar hacim"
        elif a <= 25:
            p, notu = 0.8, "ideal aralık"
        elif a <= 35:
            p, notu = 1.0, "dengeli"
        elif a <= 45:
            p, notu = 0.3, "yüksek"
        else:
            p, notu = 0.0, "çok yüksek, tahta ağır"
        return ScoreResult(round(mx * p, 1), mx, f"Halka açıklık %{a:.0f} ({notu}).", True)

    def satmama_puanla(self, metin: str, ceza_sozlugu: dict) -> ScoreResult:
        mx = self.weights.MAX[Category.SATMAMA]
        m = TextUtils.kucult(metin)
        if any(k in m for k in ["1 yıl", "2 yıl", "18 ay", "24 ay", "12 ay"]):
            return ScoreResult(mx, mx, "Satmama taahhüdü mevcut.", True)
        if "yok" in m or "bulunmuyor" in m:
            ceza_sozlugu["satmama_yok"] = 2
            return ScoreResult(0, mx, "Satmama taahhüdü bulunmuyor.", True)
        return ScoreResult(0, 0, "Satmama taahhüdü belirsiz.", False)

    def kurumsallik_puanla(self, raw_text: str,
                           kurulus_yili: Optional[int] = None) -> ScoreResult:
        mx = self.weights.MAX[Category.KURUMSALLIK]
        m = TextUtils.kucult(raw_text)
        # DÜZELTME: Kuruluş yılı artık öncelikle sayfadaki .shc-founded
        # alanından geliyor. Metin kalıbı ("2017 yılında ... kurulan")
        # araya uzun cümleler girdiği için çoğu sayfada eşleşmiyordu.
        yillar = [int(y) for y in re.findall(r"(19[5-9]\d|20\d\d)\s*yılında\s*kurul", m)]
        if kurulus_yili:
            yillar.append(kurulus_yili)
        puan, aciklamalar = 0.0, []

        if yillar:
            yas = datetime.now().year - min(yillar)
            if yas >= 20:
                puan += mx * 0.5
                aciklamalar.append(f"Köklü geçmiş (~{yas} yıl).")
            elif yas >= 10:
                puan += mx * 0.3
                aciklamalar.append(f"Yerleşik kurumsal yapı (~{yas} yıl).")

        if any(k in m for k in ["bağımsız denetim", "kurumsal yönetim"]):
            puan += mx * 0.2
            aciklamalar.append("Bağımsız denetim/kurumsal yönetim yapısı.")
        if any(k in m for k in ["iso ", "esg", "sürdürülebilirlik"]):
            puan += mx * 0.3
            aciklamalar.append("Sürdürülebilirlik/sertifikasyon vizyonu.")

        if not aciklamalar:
            return ScoreResult(0, 0, "Kurumsallık verisi bulunamadı.", False)
        return ScoreResult(round(min(mx, puan), 1), mx, " ".join(aciklamalar), True)

    # ═══════════════════════════════════════════════════════════
    # 🔥 YENİ: İLK GÜN SATIŞ BASKISI MODELİ
    # ═══════════════════════════════════════════════════════════

    def ilk_gun_satis_baskisi(
        self,
        veri: dict,
        arz_buyuklugu: Optional[float],
        pay_sayisi: Optional[float],
        fiyat: Optional[float],
        baglam: PiyasaBaglami,
        raw_text: str,
        olasi_lot: Optional[list[dict]] = None,
    ) -> dict:
        """
        YENİ MODEL — bu bölüm doğrudan Albayrak Beton gözleminden doğdu.

        Mekanizma:
          Büyük bir arz + tamamen bireysel eşit dağıtım
            -> kişi başına yüksek miktarda lot düşer
            -> aynı hafta başka arzlar da varsa toplam talep bölünür,
               kişi başı düşen lot daha da artar
            -> ilk gün çok sayıda yatırımcının elinde satılabilir
               büyüklükte pozisyon olur
            -> kâr satışı arzı, tavan alıcısını kırar.

        ÖNEMLİ: Bu bir OLASILIK sinyalidir, kesin bir tahmin değildir.
        Bu yüzden çıktı ayrı bir "baskı skoru" + uyarı metni olarak
        veriliyor; temel kalite skorunu kirletmiyor, yalnızca kısa
        vadeli "tavan potansiyeli"ni aşağı çekiyor.
        """
        gerekceler: list[str] = []
        baski = 0.0

        dagitim_metni = " ".join([
            str(veri.get(InfoKey.DAGITIM_YONTEMI, "")),
            str(veri.get(InfoKey.DAGITIM_TIPI, "")),
            str(veri.get(InfoKey.DAGITIM_TABLOSU, "")),
            str(veri.get(InfoKey.SATIS_YONTEMI, "")),
        ])
        dagitim_metni = TextUtils.kucult(dagitim_metni)
        tahsisat_metni = TextUtils.kucult(str(veri.get(InfoKey.TAHSISAT, "")))
        rt = TextUtils.kucult(raw_text)

        # 1) Eşit dağıtım mı?
        esit_dagitim = any(k in dagitim_metni or k in rt for k in [
            "eşit dağıtım", "eşit olarak dağıt", "eşit dagitim"
        ])
        # 2) Tamamen bireysel mi? (kurumsal/yurt dışı tahsisat yoksa)
        kurumsal_var = any(k in tahsisat_metni or k in rt for k in [
            "yurt içi kurumsal", "yurtiçi kurumsal", "yurt dışı kurumsal",
            "yurtdışı kurumsal", "kurumsal yatırımcı"
        ])
        # DÜZELTME: Kaynak site yüzdeyi etiketten ÖNCE yazıyor:
        #   "28.987.770 Lot (%60) Yurt İçi Bireysel Yatırımcı"
        # Önceki kalıp yüzdeyi etiketten sonra aradığı için hiç eşleşmiyordu.
        bireysel_yuzdesi = None
        aranan = tahsisat_metni + " " + rt
        for kalip in (
            r"%\s*(\d{1,3})\s*\)\s*yurt\s*içi\s*bireysel",
            r"yurt\s*içi\s*bireysel[^%\n]{0,40}%\s*(\d{1,3})",
        ):
            b_match = re.search(kalip, aranan)
            if b_match:
                try:
                    bireysel_yuzdesi = float(b_match.group(1))
                    break
                except ValueError:
                    continue
        tamamen_bireysel = (bireysel_yuzdesi is not None and bireysel_yuzdesi >= 95) or (
            bireysel_yuzdesi is None and not kurumsal_var and "bireysel" in aranan
        )

        # 3) Arz büyüklüğü
        buyuk = bool(arz_buyuklugu and arz_buyuklugu >= SETTINGS.BUYUK_ARZ_ESIGI)
        cok_buyuk = bool(arz_buyuklugu and arz_buyuklugu >= SETTINGS.COK_BUYUK_ARZ_ESIGI)

        # 4) Aynı hafta rakip arz sayısı -> talebin bölünmesi
        rakip = max(0, baglam.ayni_hafta_arz_sayisi - 1)

        # 5) Kişi başı tahmini dağıtım tutarı (asıl sinyal)
        # Rakip arz varsa katılımcı talebi bölünür varsayımı.
        etkin_katilimci = SETTINGS.TAHMINI_KATILIMCI / (1 + 0.25 * rakip)
        kisi_basi_tutar = None
        kisi_basi_lot = None
        tutar_kaynagi = None

        # ÖNCELİK 1 (YENİ): Kaynak sitenin kendi yayınladığı olası dağıtım
        # tablosu. Bu tablo bireysel tahsisat üzerinden hesaplandığı için
        # benim kaba tahminimden çok daha doğru. Beklenen katılımcı
        # sayısına en yakın satır seçiliyor.
        if olasi_lot:
            en_yakin = min(olasi_lot, key=lambda r: abs(r["katilimci"] - etkin_katilimci))
            kisi_basi_tutar = en_yakin["tutar"]
            kisi_basi_lot = float(en_yakin["lot"])
            tutar_kaynagi = f"kaynak tablo ({en_yakin['katilimci']:,} katılımcı senaryosu)".replace(",", ".")

        # ÖNCELİK 2: Tablo yoksa arz büyüklüğünden tahmin et.
        # Sadece BİREYSEL tahsisata düşen kısım dağıtılıyor.
        elif arz_buyuklugu and etkin_katilimci > 0:
            bireysel_pay = (bireysel_yuzdesi / 100.0) if bireysel_yuzdesi else 1.0
            kisi_basi_tutar = (arz_buyuklugu * bireysel_pay) / etkin_katilimci
            if fiyat and fiyat > 0:
                kisi_basi_lot = kisi_basi_tutar / fiyat
            tutar_kaynagi = "tahmin (arz büyüklüğü ÷ beklenen katılımcı)"

        # ── Puanlama ──
        if esit_dagitim:
            baski += 10
            gerekceler.append("Dağıtım eşit yöntemle yapılıyor.")
        if tamamen_bireysel:
            baski += 10
            gerekceler.append("Tahsisat neredeyse tamamen bireysel yatırımcıya ayrılmış.")
        elif bireysel_yuzdesi is not None and bireysel_yuzdesi >= 60:
            baski += 5
            gerekceler.append(
                f"Tahsisatın %{bireysel_yuzdesi:.0f}'i bireysel yatırımcıya ayrılmış "
                f"(kurumsal pay {100 - bireysel_yuzdesi:.0f}%)."
            )
        if buyuk:
            baski += 15
            gerekceler.append(f"Arz büyüklüğü yüksek (~{arz_buyuklugu/1_000_000_000:.1f} milyar TL).")
        if cok_buyuk:
            baski += 10
            gerekceler.append("Arz büyüklüğü çok yüksek: talebin tamamının karşılanması zor.")
        if rakip >= 1:
            baski += 12 * min(rakip, 3)
            gerekceler.append(
                f"Aynı dönemde {rakip} başka halka arz var "
                f"({', '.join(baglam.rakip_sirketler[:3])}): bireysel talep bölünüyor."
            )
        if kisi_basi_tutar is not None:
            if kisi_basi_tutar >= SETTINGS.KISI_BASI_KRITIK_TUTAR:
                baski += 25
                gerekceler.append(
                    f"Tahmini kişi başı dağıtım ~{kisi_basi_tutar:,.0f} TL"
                    + (f" (~{kisi_basi_lot:,.0f} lot)" if kisi_basi_lot else "")
                    + f" [{tutar_kaynagi}]: ilk gün kâr satışı baskısı çok yüksek."
                )
            elif kisi_basi_tutar >= SETTINGS.KISI_BASI_YUKSEK_TUTAR:
                baski += 15
                gerekceler.append(
                    f"Tahmini kişi başı dağıtım ~{kisi_basi_tutar:,.0f} TL"
                    + (f" (~{kisi_basi_lot:,.0f} lot)" if kisi_basi_lot else "")
                    + f" [{tutar_kaynagi}]: ilk gün satış baskısı yüksek."
                )

        # Fiyat istikrarı taahhüdü baskıyı bir miktar dengeler
        ist = TextUtils.kucult(str(veri.get(InfoKey.FIYAT_ISTIKRARI, "")))
        istikrar_var = bool(ist) and ist not in ("-", "açıklanmadı") and \
            "planlanmamaktadır" not in ist and ist != "yok"
        if istikrar_var and baski > 0:
            baski -= 10
            gerekceler.append("Fiyat istikrarı işlemi planlanmış (baskıyı kısmen dengeler).")

        baski = round(max(0.0, min(100.0, baski)), 1)

        if baski >= 65:
            seviye = "Çok Yüksek"
            uyari = (
                "⚠️ İLK GÜN SATIŞ BASKISI ÇOK YÜKSEK: Büyük arz büyüklüğü, eşit/bireysel "
                "dağıtım ve bölünmüş talep bir arada. Kişi başına yüksek miktarda lot düşmesi "
                "beklendiği için ilk gün yoğun kâr satışı görülebilir; tavan serisi "
                "beklentisi bu arzda zayıftır."
            )
        elif baski >= 40:
            seviye = "Yüksek"
            uyari = (
                "⚠️ İLK GÜN SATIŞ BASKISI YÜKSEK: Dağıtım yapısı ve arz büyüklüğü, ilk günde "
                "satış gelmesini kolaylaştırıyor. Tavan serisi beklentisini temkinli kurun."
            )
        elif baski >= 20:
            seviye = "Orta"
            uyari = (
                "ℹ️ İlk gün satış baskısı orta seviyede: dağıtım yapısı kaynaklı bir miktar "
                "arz gelmesi beklenebilir."
            )
        else:
            seviye = "Düşük"
            uyari = ""

        return {
            "skor": baski,
            "seviye": seviye,
            "uyari": uyari,
            "gerekceler": gerekceler,
            "esit_dagitim": esit_dagitim,
            "tamamen_bireysel": tamamen_bireysel,
            "kisi_basi_tutar_kaynagi": tutar_kaynagi,
            "bireysel_tahsisat_yuzdesi": bireysel_yuzdesi,
            "kisi_basi_tahmini_tutar": round(kisi_basi_tutar, 0) if kisi_basi_tutar else None,
            "kisi_basi_tahmini_lot": round(kisi_basi_lot, 0) if kisi_basi_lot else None,
            "ayni_hafta_arz_sayisi": baglam.ayni_hafta_arz_sayisi,
        }

    # ═══════════════════════════════════════════════════════════
    # TOPLAM SKOR
    # ═══════════════════════════════════════════════════════════

    def skoru_topla(self, veri: dict, fin: dict, seriler: dict, durum: ArzDurumu,
                    raw_text: str, baglam: PiyasaBaglami,
                    olasi_lot: Optional[list[dict]] = None,
                    kurulus_yili: Optional[int] = None,
                    sirket_adi: str = "") -> dict:
        kirmizi_bayraklar: list[str] = []
        ceza_sozlugu: dict[str, float] = {}
        uyarilar: list[str] = []

        sektor = self._get_sektor(raw_text, sirket_adi)
        fiyat = TextUtils.sayi_bul(veri.get(InfoKey.FIYAT, ""))
        pay = TextUtils.sayi_bul(veri.get(InfoKey.PAY_SAYISI, ""))

        # ═══ KRİTİK DÜZELTME: PİYASA DEĞERİ ═══
        # Önceki hesap "fiyat x halka arz edilen pay" idi. Bu ARZIN
        # BÜYÜKLÜĞÜ, şirketin piyasa değeri DEĞİL.
        #
        # Quick Sigorta örneği: 3,7 milyar TL'lik arz, şirketin yalnızca
        # %10,03'ü. Şirketin tam piyasa değeri 36,9 milyar TL.
        # Yanlış hesapla PD/DD 0,14 çıkıyor ve "belirgin iskontolu"
        # deniyordu; doğrusu 1,43 yani sektör ortalamasının biraz
        # ÜZERİNDE. Bu hata değerleme puanını şişiriyor, dolayısıyla
        # toplam skoru olduğundan yüksek gösteriyordu.
        arz_degeri = (fiyat * pay) if (fiyat and pay) else None
        aciklik_orani = TextUtils.yuzde_bul(veri.get(InfoKey.ACIKLIK, ""))
        market_cap = None
        if arz_degeri and aciklik_orani and 1.0 <= aciklik_orani <= 100.0:
            market_cap = arz_degeri / (aciklik_orani / 100.0)
        elif arz_degeri:
            # Halka açıklık oranı bilinmiyorsa değerleme YAPILMAZ.
            # Arz büyüklüğünü piyasa değeri sanmak, şirketi olduğundan
            # çok daha ucuz göstermek demektir.
            market_cap = None
        # Arz büyüklüğü ≠ piyasa değeri. Satış baskısı modeli arzın
        # kendi büyüklüğüne bakar, değerleme ise şirketin tamamına.
        arz_buyuklugu = arz_degeri or TextUtils.tutar_coz(
            veri.get(InfoKey.BUYUKLUK, "")
        )

        # DÜZELTME: Hazırlık/erteleme aşamasında fiyat, pay miktarı,
        # tahsisat gibi hiçbir veri yok. Buna rağmen baskı hesaplanınca
        # Türker Vangölü'nde olduğu gibi dayanaksız bir "İlk gün satış
        # baskısı" rozeti çıkıyordu. Bu aşamalarda baskı hesaplanmıyor.
        if durum in (ArzDurumu.HAZIRLANIYOR, ArzDurumu.ERTELENDI):
            baski = {
                "skor": 0.0, "seviye": "Belirsiz", "uyari": "",
                "gerekceler": [], "esit_dagitim": False,
                "tamamen_bireysel": False, "kisi_basi_tutar_kaynagi": None,
                "bireysel_tahsisat_yuzdesi": None,
                "kisi_basi_tahmini_tutar": None,
                "kisi_basi_tahmini_lot": None,
                "ayni_hafta_arz_sayisi": baglam.ayni_hafta_arz_sayisi,
            }
        else:
            baski = self.ilk_gun_satis_baskisi(
                veri, arz_buyuklugu, pay, fiyat, baglam, raw_text, olasi_lot
            )
        if baski["uyari"]:
            uyarilar.append(baski["uyari"])

        bayat, gun = TextUtils.sektor_verisi_bayat_mi()
        if bayat:
            uyarilar.append(
                f"ℹ️ Değerleme karşılaştırmasında kullanılan sektör çarpanları "
                f"{gun} gün önceki verilere dayanıyor; değerleme yorumunu bu kısıtla okuyun."
            )

        # Hazırlık ve ertelenme aşamasındaki arzlarda izahname henüz
        # yayınlanmadığı (veya süreç durduğu) için puanlama yapılmıyor.
        # Kart listede görünüyor ama sahte bir skor gösterilmiyor.
        if durum in (ArzDurumu.HAZIRLANIYOR, ArzDurumu.ERTELENDI):
            if durum == ArzDurumu.ERTELENDI:
                uyarilar.append(
                    "⚠️ Bu halka arz ERTELENDİ. Yeni tarih açıklanana kadar "
                    "değerlendirme yapılamaz."
                )
            else:
                uyarilar.append(
                    "ℹ️ Bu arz henüz hazırlık aşamasında; izahname "
                    "yayınlanmadığı için finansal değerlendirme yapılamıyor."
                )
            return {
                "temel_kalite": 0.0, "tavan_potansiyeli": 0.0, "risk": 0.0,
                "guclu": [], "risk_listesi": [], "kirmizi_bayraklar": [],
                "detaylar": [], "veri_guvenilirligi": 0, "volatilite": "Belirsiz",
                "sektor": sektor, "baski": baski, "uyarilar": uyarilar,
            }

        hesaplamalar = [
            (Category.KARLILIK, self.karlilik_puanla(fin, kirmizi_bayraklar, ceza_sozlugu)),
            (Category.BUYUME, self.buyume_puanla(seriler, kirmizi_bayraklar, ceza_sozlugu)),
            (Category.BORC_YAPISI, self.borc_puanla(fin, sektor, kirmizi_bayraklar, ceza_sozlugu)),
            (Category.LIKIDITE, self.likidite_puanla(fin, kirmizi_bayraklar, ceza_sozlugu)),
            (Category.NAKIT_AKISI, self.nakit_akisi_puanla(fin, kirmizi_bayraklar, ceza_sozlugu)),
            (Category.DEGERLEME, self.degerleme_puanla(market_cap, fin, sektor, kirmizi_bayraklar)),
            (Category.FON_KULLANIM, self.fon_kullanim_puanla(
                veri.get(InfoKey.FON_KULLANIM, ""), fin, kirmizi_bayraklar)),
            (Category.ARZ_YAPISI, self.arz_yapisi_puanla(
                veri.get(InfoKey.HALKA_ARZ_SEKLI, ""), kirmizi_bayraklar, ceza_sozlugu)),
            (Category.ISKONTO, self.iskonto_puanla(veri.get(InfoKey.ISKONTO, ""))),
            (Category.ACIKLIK, self.aciklik_puanla(veri.get(InfoKey.ACIKLIK, ""))),
            (Category.SATMAMA, self.satmama_puanla(veri.get(InfoKey.TAAHHUT, ""), ceza_sozlugu)),
            (Category.KURUMSALLIK, self.kurumsallik_puanla(raw_text, kurulus_yili)),
        ]

        toplam_kazanilan = sum(r.score for _, r in hesaplamalar)
        toplam_max = sum(r.max_possible for _, r in hesaplamalar)
        tum_max = sum(self.weights.MAX.values())

        detaylar = [{
            "kategori": kat.value,
            "puan": res.score,
            "max_puan": self.weights.MAX[kat],
            "olculebilen_max": res.max_possible,
            "aciklama": res.explanation,
            "veri_bulundu": res.has_data,
        } for kat, res in hesaplamalar]

        # DEĞİŞİKLİK: Veri güvenilirliği artık ham puan toplamı değil,
        # ölçülebilen ağırlığın toplam ağırlığa oranı (gerçek bir yüzde).
        veri_guvenilirligi = int(round((toplam_max / tum_max) * 100)) if tum_max else 0

        # YENİ VE ÖNEMLİ: Kaynak site (halkarz.com) çoğu arzda finansal
        # tablo yayınlamıyor; "izahnameye göz atın" notu bırakıyor.
        # Bu durumda kârlılık/büyüme/borç/değerleme boyutlarının hiçbiri
        # hesaplanamıyor ve geriye yalnızca arz yapısı, iskonto, açıklık
        # gibi kalemler kalıyor. Ortaya çıkan sayı bir "finansal kalite"
        # skoru DEĞİLDİR; bunu kullanıcıya açıkça söylemek zorundayız.
        FINANSAL_BOYUTLAR = {
            Category.KARLILIK, Category.BUYUME, Category.BORC_YAPISI,
            Category.LIKIDITE, Category.NAKIT_AKISI, Category.DEGERLEME,
        }
        finansal_veri_var = any(
            res.has_data for kat, res in hesaplamalar if kat in FINANSAL_BOYUTLAR
        )
        if not finansal_veri_var:
            uyarilar.append(
                "⚠️ BU ARZ İÇİN FİNANSAL TABLO VERİSİ YOK: Kaynakta kâr, hasılat, "
                "borç ve özkaynak rakamları yayınlanmadığı için kârlılık, büyüme, "
                "borçluluk ve değerleme hiç hesaplanamadı. Aşağıdaki skor yalnızca "
                "halka arzın YAPISINI (arz şekli, iskonto, halka açıklık, taahhütler) "
                "yansıtır; şirketin finansal sağlığı hakkında bilgi vermez. "
                "Şirketin bilançosu için izahnameyi inceleyin."
            )
        base_score = (toplam_kazanilan / toplam_max * 100) if toplam_max > 0 else 0.0

        bonuslar = 0.0
        guclu: list[str] = []
        rt = TextUtils.kucult(raw_text)

        if any(k in rt for k in ["temettü ödemesi", "kâr payı dağıtıldı", "nakit temettü"]):
            bonuslar += 2
            guclu.append("[bonus] Geçmişte somut temettü ödeme kültürü. (+2.0 Puan)")
        ihracat = re.search(r"ihracat oranı %([2-9][0-9]|100)", rt)
        if ihracat:
            bonuslar += 2
            guclu.append(f"[bonus] Güçlü döviz girdisi (ihracat %{ihracat.group(1)}). (+2.0 Puan)")
        if any(k in rt for k in ["ar-ge merkezi", "patent", "tübitak destekli"]):
            bonuslar += 1
            guclu.append("[bonus] Tescilli Ar-Ge / patent çalışmaları. (+1.0 Puan)")

        ist = TextUtils.kucult(str(veri.get(InfoKey.FIYAT_ISTIKRARI, "")))
        istikrar_yok = "planlanmamaktadır" in ist or ist == "yok"
        if istikrar_yok:
            kirmizi_bayraklar.append("🚨 Fiyat istikrarı işlemi planlanmıyor.")
            ceza_sozlugu["istikrar_yok"] = 3

        # ── DEĞİŞİKLİK: Risk artık doğrusal değil ──
        # İncelemede belirtildiği gibi, kritik sorunlar bir araya
        # geldiğinde risk katlanarak artmalı.
        KRITIK_CEZALAR = {
            "negatif_ozsermaye", "zarar", "faiz_karsilama",
            "negatif_nakit_akisi", "kritik_borc", "faaliyet_zarari",
        }
        toplam_ceza = sum(ceza_sozlugu.values())
        kritik_sayisi = sum(1 for k in ceza_sozlugu if k in KRITIK_CEZALAR)
        kritik_carpani = 1.0 + 0.3 * max(0, kritik_sayisi - 1)

        temel_kalite = round(max(0.0, min(100.0, base_score + bonuslar - toplam_ceza)), 1)

        # ═══ YENİ: SKOR GÜVENİLİRLİĞİ ═══
        # Skor, "ölçülebilen ağırlığa" göre normalize ediliyor. Yani
        # modelin %30'u ölçülebildiyse skor o %30'un içinden hesaplanıyor
        # ve 100 üzerinden sunuluyor. Bu, kullanıcıya tam bir
        # değerlendirme yapılmış izlenimi veriyor — oysa değil.
        #
        # Teknika Plast örneği: kârlılık, borç, likidite, nakit akışı ve
        # değerlemenin HİÇBİRİ ölçülemedi (ağırlığın %67'si). Kalan
        # %33'ten çıkan 32 puan, "şirket kötü" demek değil; "yeterli
        # veri yok" demek. Bu ayrım açıkça bildirilmeli.
        SKOR_GUVENILIR_ESIK = 55
        skor_guvenilir = veri_guvenilirligi >= SKOR_GUVENILIR_ESIK
        if not skor_guvenilir:
            olculemeyen = [
                kat.value for kat, res in hesaplamalar
                if not res.has_data and self.weights.MAX[kat] >= 10
            ]
            uyarilar.append(
                f"⚠️ SKOR EKSİK VERİYLE HESAPLANDI (%{veri_guvenilirligi} kapsam). "
                f"Şu boyutlar hiç ölçülemedi: {', '.join(olculemeyen)}. "
                f"Bu puan şirketin kötü olduğunu DEĞİL, izahname verisinin "
                f"henüz işlenmediğini gösterir. Diğer şirketlerle "
                f"karşılaştırmak için kullanmayın."
            )

        risk = 10.0 + (toplam_ceza * kritik_carpani)
        if veri_guvenilirligi < 70:
            risk += (70 - veri_guvenilirligi) * 0.4
        aciklik_val = TextUtils.yuzde_bul(veri.get(InfoKey.ACIKLIK, "")) or 25.0
        if aciklik_val > 40:
            risk += 12
        # YENİ: ilk gün satış baskısı riske de yansıyor
        risk += baski["skor"] * 0.15
        risk = round(max(0.0, min(100.0, risk)), 1)

        if kritik_sayisi >= 2:
            uyarilar.append(
                f"⚠️ Birden fazla kritik finansal sorun bir arada ({kritik_sayisi} adet); "
                "riskler birbirini büyütüyor."
            )

        # ── Tavan potansiyeli ──
        tavan = 50.0
        if arz_buyuklugu:
            if arz_buyuklugu < 500_000_000:
                tavan += 25
            elif arz_buyuklugu < 1_000_000_000:
                tavan += 12
            elif arz_buyuklugu > SETTINGS.COK_BUYUK_ARZ_ESIGI:
                tavan -= 20
            elif arz_buyuklugu > SETTINGS.BUYUK_ARZ_ESIGI:
                tavan -= 12

        if sektor in ("TEKNOLOJİ", "ENERJİ"):
            tavan += 8
        isk_val = TextUtils.yuzde_bul(veri.get(InfoKey.ISKONTO, "")) or 0.0
        if isk_val >= 25:
            tavan += 8
        if istikrar_yok:
            tavan -= 8
        if ceza_sozlugu.get("ortak_satis"):
            tavan -= 12
        if temel_kalite >= 70:
            tavan += 5
        elif temel_kalite <= 35 and temel_kalite > 0:
            tavan -= 8

        # YENİ VE EN ÖNEMLİ DÜZELTME:
        # İlk gün satış baskısı doğrudan tavan potansiyelini kırıyor.
        # Albayrak Beton tipi durumda (büyük arz + eşit/bireysel dağıtım
        # + bölünmüş talep) yüksek kalite skoru artık otomatik olarak
        # "tavan yapar" beklentisine dönüşmüyor.
        tavan -= baski["skor"] * 0.45
        # Taban 3 puan: baskı çok yüksek olsa bile skoru tam 0'a
        # sabitlemek, farklı kötü senaryoları birbirinden ayırt
        # edilemez hale getiriyordu.
        tavan = round(max(3.0, min(100.0, tavan)), 1)

        if baski["skor"] >= 65:
            volatilite = "Yüksek (İlk gün satış baskısı - aşağı yönlü)"
        elif tavan > 75:
            volatilite = "Yüksek (Agresif yukarı hareket potansiyeli)"
        elif risk > 60:
            volatilite = "Yüksek (Aşağı yönlü dalgalanma riski)"
        elif tavan < 40:
            volatilite = "Düşük (Ağır tahta - sınırlı hareket)"
        else:
            volatilite = "Orta (Piyasa ve sektör koşullarına bağlı)"

        risk_listesi: list[str] = []
        for kat, res in hesaplamalar:
            if not res.has_data or not res.max_possible:
                continue
            oran = res.score / res.max_possible
            if oran >= 0.8:
                guclu.append(f"[{kat.value}] {res.explanation} (+{res.score:.1f} Puan)")
            elif oran <= 0.3:
                risk_listesi.append(
                    f"[{kat.value}] {res.explanation} (-{res.max_possible - res.score:.1f} Puan)"
                )

        # Sıralamayı koruyarak tekilleştir
        kirmizi_bayraklar = list(dict.fromkeys(kirmizi_bayraklar))

        return {
            "temel_kalite": temel_kalite,
            "tavan_potansiyeli": tavan,
            "risk": risk,
            "guclu": guclu,
            "risk_listesi": risk_listesi,
            "kirmizi_bayraklar": kirmizi_bayraklar,
            "detaylar": detaylar,
            "veri_guvenilirligi": veri_guvenilirligi,
            "volatilite": volatilite,
            "sektor": sektor,
            "baski": baski,
            "uyarilar": uyarilar,
            "finansal_veri_var": finansal_veri_var,
            "skor_guvenilir": skor_guvenilir,
            "olculemeyen_boyutlar": [
                kat.value for kat, res in hesaplamalar if not res.has_data
            ],
            "degerleme": self.degerleme_carpanlari(market_cap, fin, sektor),
        }


# ═══════════════════════════════════════════════════════════════════
# 🕸️ 5. VERİ ÇEKİCİ (ASYNC)
# ═══════════════════════════════════════════════════════════════════

class DataExtractor:
    def __init__(self, analyzer: Optional[ScoreAnalyzer] = None):
        self.analyzer = analyzer or ScoreAnalyzer()
        self.session: Optional[AsyncSession] = None
        self._session_lock = asyncio.Lock()
        # Tabloda görülüp seriye alınmayan ara dönemler (ör. 2026/3).
        # Yalnızca tanı amaçlı; büyüme hesabına girmezler.
        self._atlanan_ara_donemler: set[int] = set()

        # DEĞİŞİKLİK (mimari): Sunucu artık PDF indirmiyor, OCR yapmıyor,
        # yapay zekaya istek atmıyor. Tüm bu ağır iş GitHub Actions'ta
        # yapılıp sonucu veri/finansallar/*.json olarak depoya yazılıyor.
        # Burada sadece hazır JSON okunuyor — milisaniyeler sürer.
        # Bu sayede Render'da Docker/tesseract/API anahtarı GEREKMEZ.
        self.finansal_depo = None
        try:
            from finansal_depo import FinansalDepo
            self.finansal_depo = FinansalDepo()
            self.finansal_depo.yukle()
        except ImportError as e:
            logger.info(f"Finansal veri modülü yok, bu özellik devre dışı: {e}")

        self.FIELD_LABELS: dict[InfoKey, list[str]] = {
            InfoKey.BIST_KODU: ["bist kodu"],
            InfoKey.TARIH: ["halka arz tarihi", "talep toplama tarihi"],
            # DEĞİŞİKLİK: Fiyat ve işlem tarihi bazı sayfalarda farklı
            # başlıklarla yazıldığı için "Açıklanmadı" olarak kalıyordu.
            # Eş anlamlı başlıklar eklendi.
            InfoKey.FIYAT: [
                "halka arz fiyatı", "pay fiyatı", "birim pay fiyatı",
                "arz fiyatı", "hisse fiyatı", "satış fiyatı", "fiyat"
            ],
            InfoKey.BUYUKLUK: [
                "halka arz büyüklüğü", "arz büyüklüğü", "halka arz tutarı"
            ],
            InfoKey.ISLEM_TARIHI: [
                "işlem tarihi", "borsada işlem tarihi",
                "borsada işlem görme tarihi", "işlem görme tarihi",
                "borsada işleme başlama tarihi", "ilk işlem tarihi"
            ],
            InfoKey.ACIKLIK: ["halka açıklık", "halka açıklık oranı"],
            InfoKey.ISKONTO: ["halka arz iskontosu", "iskonto oranı"],
            InfoKey.TAAHHUT: ["satmama taahhüdü"],
            InfoKey.HALKA_ARZ_SEKLI: ["halka arz şekli"],
            InfoKey.FON_KULLANIM: ["fonun kullanım yeri", "fon kullanım yeri"],
            InfoKey.SATIS_YONTEMI: ["halka arz satış yöntemi"],
            InfoKey.FIYAT_ISTIKRARI: ["fiyat istikrarı"],
            InfoKey.PAY_SAYISI: [
                "halka arz edilecek pay", "dağıtılacak pay miktarı",
                "toplam pay miktarı", "toplam pay", "pay miktarı",
                "çıkarılmış sermaye", "ödenmiş sermaye"
            ],
            InfoKey.DAGITIM_YONTEMI: ["dağıtım yöntemi"],
            InfoKey.ARACI_KURUM: ["aracı kurum", "konsorsiyum lideri"],
            InfoKey.PAZAR: ["pazar"],
        }
        self.TUM_ETIKETLER = {e for etiketler in self.FIELD_LABELS.values() for e in etiketler}

        self.FIN_LABELS: dict[FinKey, list[str]] = {
            FinKey.NET_KAR: ["net dönem karı", "net dönem kârı", "net kar", "net kâr",
                             "dönem net karı", "dönem net kârı", "dönem karı", "dönem kârı"],
            FinKey.OZKAYNAK: ["özkaynaklar", "toplam özkaynaklar", "öz kaynaklar", "özkaynak"],
            FinKey.DONEN_VARLIK: ["dönen varlıklar", "toplam dönen varlıklar"],
            FinKey.KISA_VADELI_YUKUMLULUK: ["kısa vadeli yükümlülükler", "kısa vadeli borçlar"],
            FinKey.TOPLAM_BORC: ["toplam yükümlülükler", "toplam borçlar"],
            FinKey.HASILAT: ["hasılat", "satış gelirleri", "net satışlar", "satış hasılatı"],
            # YENİ etiketler
            FinKey.FAALIYET_KARI: ["esas faaliyet karı", "esas faaliyet kârı",
                                   "faaliyet karı", "faaliyet kârı", "faaliyet kar/zararı"],
            FinKey.AMORTISMAN: ["amortisman", "amortisman ve tükenme payları",
                                "amortisman gideri"],
            FinKey.FAVOK: ["favök", "favok", "ebitda"],
            FinKey.FINANSAL_BORC: ["finansal borçlar", "finansal yükümlülükler",
                                   "banka kredileri", "toplam finansal borç"],
            FinKey.NAKIT: ["nakit ve nakit benzerleri", "nakit ve benzerleri", "nakit"],
            FinKey.ISLETME_NAKIT_AKISI: [
                "işletme faaliyetlerinden nakit akışları",
                "işletme faaliyetlerinden elde edilen nakit",
                "işletme faaliyetlerinden nakit akışı",
                "faaliyetlerden sağlanan nakit",
            ],
            FinKey.FINANSMAN_GIDERI: ["finansman giderleri", "finansman gideri",
                                      "faiz gideri", "faiz giderleri"],
            FinKey.BRUT_KAR: ["brüt kar", "brüt kâr", "brüt esas faaliyet karı"],
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
            InfoKey.DAGITIM_TIPI: "Tahmini Lot Tablosu",
        })

    # ───────────────────────── HTTP ─────────────────────────

    async def _get_session(self) -> AsyncSession:
        if self.session is None or getattr(self.session, "closed", True):
            async with self._session_lock:
                if self.session is None or getattr(self.session, "closed", True):
                    self.session = AsyncSession(impersonate="chrome")
        return self.session

    async def _fetch_url_with_retry(self, url: str) -> Optional[str]:
        session = await self._get_session()
        for i in range(SETTINGS.MAX_RETRY):
            try:
                res = await session.get(url, timeout=SETTINGS.TIMEOUT)
                if res.status_code == 200:
                    return res.text
                logger.warning(f"Bağlantı hatası ({url}), durum kodu: {res.status_code}")
            except Exception as e:
                logger.warning(f"Timeout/hata ({url}): {e}")
            if i < SETTINGS.MAX_RETRY - 1:
                await asyncio.sleep(1 * (i + 1))
        return None

    async def _fetch_bytes(self, url: str) -> Optional[bytes]:
        """
        PDF gibi ikili içerik indirir. _fetch_url_with_retry metin
        döndürdüğü için PDF'lerde kullanılamaz.
        """
        session = await self._get_session()
        for i in range(SETTINGS.MAX_RETRY):
            try:
                # PDF'ler büyük (~18 MB), zaman aşımı uzun tutuluyor
                res = await session.get(url, timeout=SETTINGS.TIMEOUT * 6)
                if res.status_code == 200:
                    return res.content
                logger.warning(f"PDF indirilemedi ({url}): {res.status_code}")
            except Exception as e:
                logger.warning(f"PDF indirme hatası ({url}): {e}")
            if i < SETTINGS.MAX_RETRY - 1:
                await asyncio.sleep(2 * (i + 1))
        return None

    # ───────────────────────── PARSE ─────────────────────────

    # ── YENİ: etiket bulunamazsa ham metinden yedek çıkarım ──

    @staticmethod
    def _yedek_fiyat_bul(raw_text: str) -> Optional[str]:
        """
        Etiketli tablo okunamazsa fiyatı ham metinden yakalar.
        "18,00 TL", "Halka arz fiyatı 18,00 TL", "1 TL nominal ... 18,00 TL"
        gibi kalıpları arar.
        """
        kaliplar = [
            r"(?:halka arz|pay|birim|satış|hisse)\s*fiyat[ıi]?\s*[:\-]?\s*([\d.,]+)\s*(?:tl|₺)",
            r"([\d.,]+)\s*(?:tl|₺)\s*(?:halka arz )?fiyat",
        ]
        for k in kaliplar:
            m = re.search(k, raw_text, re.IGNORECASE)
            if m:
                return f"{m.group(1)} TL"
        return None

    @staticmethod
    def _yedek_islem_tarihi_bul(raw_text: str) -> Optional[str]:
        """
        "Borsada işlem görmeye 12 Ağustos 2026 tarihinde başlayacaktır"
        gibi cümlelerden işlem tarihini çeker.
        """
        aylar_re = "|".join(AYLAR.keys())
        kaliplar = [
            rf"borsada\s+işlem[^.]{{0,60}}?(\d{{1,2}}\s+(?:{aylar_re})\s+20\d{{2}})",
            rf"(\d{{1,2}}\s+(?:{aylar_re})\s+20\d{{2}})[^.]{{0,40}}?borsada\s+işlem",
            rf"işlem\s+görme\s+tarihi\s*[:\-]?\s*(\d{{1,2}}\s+(?:{aylar_re})\s+20\d{{2}})",
        ]
        for k in kaliplar:
            m = re.search(k, raw_text, re.IGNORECASE)
            if m:
                return m.group(1).title()
        return None

    def _yapisal_finansal_tablo(self, fin: dict, seriler: dict) -> dict:
        """
        YENİ: Finansal tabloyu ham metin yerine yapısal olarak döndürür.
        Uygulama bunu gerçek bir tablo gibi (kalem | dönem | dönem)
        çizebiliyor; eskiden veriler alt alta düz metin olarak
        yazıldığı için okunmuyordu.
        """
        gosterim_adlari = [
            (FinKey.HASILAT, "Hasılat"),
            (FinKey.BRUT_KAR, "Brüt Kâr"),
            (FinKey.FAALIYET_KARI, "Faaliyet Kârı"),
            (FinKey.FAVOK, "FAVÖK"),
            (FinKey.NET_KAR, "Net Kâr"),
            (FinKey.OZKAYNAK, "Özkaynaklar"),
            (FinKey.DONEN_VARLIK, "Dönen Varlıklar"),
            (FinKey.KISA_VADELI_YUKUMLULUK, "Kısa Vadeli Yük."),
            (FinKey.TOPLAM_BORC, "Toplam Yükümlülük"),
            (FinKey.FINANSAL_BORC, "Finansal Borç"),
            (FinKey.NAKIT, "Nakit"),
            (FinKey.ISLETME_NAKIT_AKISI, "İşletme Nakit Akışı"),
        ]

        # Tüm serilerdeki yılları topla
        yillar: set[int] = set()
        for seri in seriler.values():
            yillar.update(seri.keys())
        donemler = [str(y) for y in sorted(yillar)]

        satirlar = []
        for anahtar, ad in gosterim_adlari:
            seri = seriler.get(anahtar, {})
            if donemler and seri:
                degerler = [
                    TextUtils.sayi_formatla(seri.get(int(d))) for d in donemler
                ]
            elif anahtar in fin:
                degerler = ["-"] * max(0, len(donemler) - 1) + [
                    TextUtils.sayi_formatla(fin[anahtar])
                ]
                if not donemler:
                    degerler = [TextUtils.sayi_formatla(fin[anahtar])]
            else:
                continue
            satirlar.append({"kalem": ad, "degerler": degerler})

        if not donemler and satirlar:
            donemler = ["Son Dönem"]

        return {"donemler": donemler, "satirlar": satirlar}


    # ═══════════════════════════════════════════════════════════
    # 🎯 halkarz.com'a ÖZEL AYRIŞTIRICILAR
    # Kaynak sayfanın gerçek HTML yapısına göre yazıldı.
    # Önceki sürüm sayfayı düz metne çevirip etiket tahmin ediyordu;
    # sitenin "Özet Bilgiler" bölümü aslında bir tablo değil
    # <ul class="aex-in"><li><h5>Başlık</h5><p>Değer</p></li></ul>
    # yapısında olduğu için pek çok alan hiç okunamıyordu.
    # ═══════════════════════════════════════════════════════════

    # table.sp-table içindeki satır başlıkları -> alan
    ANA_TABLO_ETIKETLERI: ClassVar[dict[str, InfoKey]] = {
        "halka arz tarihi": InfoKey.TARIH,
        "talep toplama tarihi": InfoKey.TARIH,
        "halka arz fiyatı/aralığı": InfoKey.FIYAT,
        "halka arz fiyatı": InfoKey.FIYAT,
        "dağıtım yöntemi": InfoKey.DAGITIM_YONTEMI,
        "pay": InfoKey.PAY_SAYISI,
        "aracı kurum": InfoKey.ARACI_KURUM,
        "bist kodu": InfoKey.BIST_KODU,
        "pazar": InfoKey.PAZAR,
        "bist ilk işlem tarihi": InfoKey.ISLEM_TARIHI,
        "borsada işlem tarihi": InfoKey.ISLEM_TARIHI,
    }

    # ul.aex-in > li > h5 başlıkları -> alan
    OZET_ETIKETLERI: ClassVar[dict[str, InfoKey]] = {
        "halka arz şekli": InfoKey.HALKA_ARZ_SEKLI,
        "fonun kullanım yeri": InfoKey.FON_KULLANIM,
        "halka arz satış yöntemi": InfoKey.SATIS_YONTEMI,
        "tahsisat grupları": InfoKey.TAHSISAT,
        "finansal tablo": InfoKey.FINANSAL_TABLO,
        "fiyat istikrarı": InfoKey.FIYAT_ISTIKRARI,
        "satmama taahhüdü": InfoKey.TAAHHUT,
        "halka açıklık": InfoKey.ACIKLIK,
        "halka arz iskontosu": InfoKey.ISKONTO,
        "halka arz büyüklüğü": InfoKey.BUYUKLUK,
    }

    # Sitenin "veri henüz yok" ifadeleri
    BOS_IFADELER: ClassVar[set[str]] = {
        "hazırlanıyor", "hazırlanıyor...", "açıklanmadı", "belli değil",
        "-", "", "bekleniyor", "belirtilmemiş",
    }

    @classmethod
    def _bos_mu(cls, deger: Optional[str]) -> bool:
        return TextUtils.normalize(deger).rstrip(".") in cls.BOS_IFADELER

    @staticmethod
    def _etiket_temizle(ham: str) -> str:
        """'Dağıtılacak Pay Miktarı (Olası) *' -> 'dağıtılacak pay miktarı'"""
        n = TextUtils.normalize(ham)
        n = re.sub(r"\s*\([^)]*\)\s*", " ", n)   # parantezli ekleri at
        n = n.replace("*", " ")
        return re.sub(r"\s+", " ", n).strip()

    @staticmethod
    def _dugum_metni(dugum) -> str:
        """
        <p> içeriğini satırlara ayırarak alır; <small> içindeki
        kaynak dipnotlarını ('* İzahname, Sayfa 340.') atar.
        """
        if dugum is None:
            return ""
        for small in dugum.find_all("small"):
            small.decompose()
        ham = dugum.get_text(separator="\n", strip=True)
        satirlar = []
        for s in ham.split("\n"):
            s = s.strip().lstrip("-–•").strip()
            if not s or s.startswith("*"):
                continue
            satirlar.append(s)
        return "\n".join(satirlar)

    def _ana_tablodan_doldur(self, veri: dict, soup) -> None:
        """table.sp-table -> tarih, fiyat, pay, aracı kurum, bist kodu, pazar..."""
        for tr in soup.select("table.sp-table tr"):
            hucreler = tr.find_all("td")
            if len(hucreler) < 2:
                continue
            etiket = TextUtils.normalize(hucreler[0].get_text(separator=" ", strip=True))
            alan = self.ANA_TABLO_ETIKETLERI.get(etiket)
            if alan is None:
                continue
            deger = hucreler[1].get_text(separator=" ", strip=True)
            if self._bos_mu(deger):
                continue
            veri[alan] = deger

    def _ozet_bilgilerden_doldur(self, veri: dict, soup) -> None:
        """ul.aex-in -> fon kullanımı, tahsisat, açıklık, iskonto, büyüklük..."""
        for li in soup.select("ul.aex-in li"):
            h5 = li.find("h5")
            if h5 is None:
                continue
            etiket = self._etiket_temizle(h5.get_text(separator=" ", strip=True))
            deger = self._dugum_metni(li.find("p"))
            if not deger:
                continue

            alan = self.OZET_ETIKETLERI.get(etiket)
            if alan is not None:
                if not self._bos_mu(deger):
                    veri[alan] = deger
                continue

            # "Dağıtılacak Pay Miktarı (Olası)" -> tahmini lot tablosu
            if etiket.startswith("dağıtılacak pay miktarı"):
                veri[InfoKey.DAGITIM_TABLOSU] = deger
                veri[InfoKey.DAGITIM_TIPI] = "Tahmini Lot Tablosu"

    def _kesin_dagitim_doldur(self, veri: dict, soup) -> bool:
        """
        article.haberler-dagitim-olasi -> KESİNLEŞEN lot tablosu.
        DÜZELTME: Bu başlık sayfada her zaman var; içi 'Hazırlanıyor...'
        olsa bile önceki kod ham metinde 'dağıtılan pay miktarı' görünce
        arzı 'İşleme Girmesi Bekleniyor' durumuna geçiriyordu.
        Artık başlığın değil, İÇERİĞİN dolu olmasına bakılıyor.
        """
        art = soup.select_one("article.haberler-dagitim-olasi")
        if art is None:
            return False
        icerik_dugumu = art.find("div")
        deger = self._dugum_metni(icerik_dugumu)
        if not deger or self._bos_mu(deger):
            return False
        veri[InfoKey.DAGITIM_TABLOSU] = deger
        veri[InfoKey.DAGITIM_TIPI] = "Kesinleşen Lot Tablosu"
        return True

    def _sirket_bilgisi_doldur(self, veri: dict, soup) -> Optional[int]:
        """
        Kuruluş yılını .shc-founded alanından okur.
        Önceki kod '(yıl) yılında ... kurulan' kalıbını arıyordu; sitede
        araya uzun cümleler girdiği için hiç eşleşmiyordu.
        """
        span = soup.select_one("span.shc-founded")
        if span is None:
            return None
        m = re.search(r"(19\d{2}|20\d{2})", span.get_text(separator=" ", strip=True))
        return int(m.group(1)) if m else None

    @staticmethod
    def _olasi_lot_tablosu_coz(metin: str) -> list[dict]:
        """
        YENİ: Sitenin kendi hesapladığı dağıtım tablosunu sayısal hale getirir.
          '2.2 Milyon katılım ~ 14 Lot (1072 TL)' -> (2200000, 14, 1072)
        Bu, ilk gün satış baskısı modelini kendi tahminim yerine
        KAYNAĞIN kendi rakamlarına dayandırmayı sağlıyor.
        """
        sonuc: list[dict] = []
        desen = re.compile(
            r"([\d.,]+)\s*(bin|milyon)?\s*katılım[^\d]{0,6}([\d.]+)\s*lot\s*\(\s*([\d.]+)\s*tl\s*\)",
            re.IGNORECASE,
        )
        for m in desen.finditer(metin or ""):
            try:
                taban = float(m.group(1).replace(".", "").replace(",", ".")) \
                    if ("," in m.group(1) or len(m.group(1).split(".")[-1]) == 3) \
                    else float(m.group(1).replace(",", "."))
                # "1.1 Milyon" -> 1.1 ; "150" -> 150
                if m.group(2) and "." in m.group(1) and len(m.group(1).split(".")[-1]) != 3:
                    taban = float(m.group(1))
                birim = (m.group(2) or "").lower()
                katilimci = taban * (1_000 if birim == "bin" else 1_000_000 if birim == "milyon" else 1)
                sonuc.append({
                    "katilimci": int(katilimci),
                    "lot": int(m.group(3).replace(".", "")),
                    "tutar": float(m.group(4).replace(".", "")),
                })
            except (ValueError, AttributeError):
                continue
        return sonuc

    def _tablodan_doldur(self, veri: dict, soup: BeautifulSoup):
        for tr in soup.find_all("tr"):
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
                if TextUtils.etiket_eslesir(baslik_norm, etiketler):
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
                if any(
                    nl.startswith(e) and (len(nl) == len(e) or nl[len(e)] in (":", " "))
                    for e in etiketler
                ) or nl in etiketler:
                    deger = self._satirdan_deger_al(lines, normalized_lines, i)
                    if deger:
                        veri[alan] = deger
                    break

    # DEĞİŞİKLİK: Bölümler birbirine karışıyordu. Önceki kod, başlıktan
    # sonraki 20 satırı alıp içinde "lot"/"kişi"/"%" geçenleri filtreliyordu;
    # araya giren bir sonraki başlığı fark etmediği için "Tahsisat Grupları"
    # satırları "Dağıtılan Pay Miktarı" tablosuna karışıyordu.
    # Artık bir sonraki bilinen bölüm başlığında toplama duruyor.
    BOLUM_BASLIKLARI: ClassVar[set[str]] = {
        "tahsisat grupları", "tahsisat grubu",
        "dağıtılan pay miktarı", "dağıtılacak pay miktarı",
        "halka arz bilgileri", "fonun kullanım yeri", "fon kullanım yeri",
        "finansal tablo", "finansal tablolar", "özet finansal tablo",
        "halka arz şekli", "satmama taahhüdü", "aracı kurum",
        "şirket hakkında", "fiyat istikrarı", "dağıtım yöntemi",
        "talep toplama tarihi", "halka arz tarihi", "izahname",
        "yorumlar", "benzer halka arzlar", "kategoriler", "sermaye yapısı",
        "ortaklık yapısı", "bağımsız denetim", "konsorsiyum",
    }

    def _bolum_satirlari(self, lines: list[str], normalized: list[str],
                         baslik: str, max_satir: int = 18) -> list[str]:
        """
        Verilen başlıktan sonraki satırları, BİR SONRAKİ bölüm başlığına
        kadar toplar. Bölümlerin birbirine karışmasını engelleyen kısım.
        """
        baslik_n = TextUtils.normalize(baslik)
        for i, nl in enumerate(normalized):
            if nl != baslik_n and not nl.startswith(baslik_n):
                continue
            toplanan: list[str] = []
            for j in range(i + 1, min(i + 1 + max_satir, len(lines))):
                if normalized[j] in self.BOLUM_BASLIKLARI:
                    break
                s = lines[j].strip().lstrip("-•").strip()
                if s:
                    toplanan.append(s)
            return toplanan
        return []

    def _dagitim_tahsisat_finansal_doldur(self, veri: dict, raw_text: str):
        lines = [l.strip() for l in raw_text.split("\n")]
        normalized = [TextUtils.normalize(l) for l in lines]

        # ── Lot / dağıtım tablosu ──
        lot_baslik = None
        for aday in ("Dağıtılan Pay Miktarı", "Dağıtılacak Pay Miktarı"):
            if aday in raw_text:
                lot_baslik = aday
                break

        if lot_baslik:
            kesin = "Dağıtılan" in lot_baslik
            # DEĞİŞİKLİK: Başlık adı kullanıcının istediği gibi değiştirildi.
            veri[InfoKey.DAGITIM_TIPI] = (
                "Kesinleşen Lot Tablosu" if kesin else "Tahmini Lot Tablosu"
            )
            ham = self._bolum_satirlari(lines, normalized, lot_baslik)
            lot_satirlari = [
                s for s in ham
                if re.search(r"\d", s)
                and any(k in TextUtils.kucult(s) for k in ("lot", "kişi", "katılım", "adet"))
            ]
            if lot_satirlari:
                veri[InfoKey.DAGITIM_TABLOSU] = "\n".join(
                    "• " + s for s in lot_satirlari[:12]
                )

        # ── Tahsisat grupları ──
        for baslik in ("Tahsisat Grupları", "Tahsisat Grubu"):
            ham = self._bolum_satirlari(lines, normalized, baslik, max_satir=12)
            tahsisat_satirlari = [s for s in ham if "%" in s]
            if tahsisat_satirlari:
                veri[InfoKey.TAHSISAT] = "\n".join(
                    "• " + s for s in tahsisat_satirlari[:8]
                )
                break

        # ── Özet finansal tablo (ham metin yedeği) ──
        for baslik in ("Finansal Tablo", "Özet Finansal Tablo", "Finansal Tablolar"):
            ham = self._bolum_satirlari(lines, normalized, baslik, max_satir=16)
            ham = [s for s in ham if "*" not in s]
            if ham:
                veri[InfoKey.FINANSAL_TABLO] = "\n".join(ham)
                break

    @staticmethod
    def _donem_coz_basligi(metin: str) -> Optional[tuple[int, bool]]:
        """
        Bir tablo kolon başlığından (yıl, tam_yil_mi) çıkarır.

        DÜZELTME: Kaynak sitede kolonlar "2026/3", "2025", "2024"
        şeklinde. "2026/3" ilk ÜÇ AYLIK dönemdir; bunu yıllık verilerle
        karşılaştırmak sahte daralma üretiyordu. Çitlekçi'de hasılat
        2024: 6,2 mlr -> 2025: 9,0 mlr (büyüme) iken, 2026/3'ün
        2,6 mlr'lik çeyrek rakamı yıllık sanılıp "%35 daralma" kırmızı
        bayrağı basılmıştı.
        """
        m = re.search(r"(20\d{2})\s*[/\-]\s*(\d{1,2})", str(metin))
        if m:
            ay = int(m.group(2))
            return int(m.group(1)), ay == 12
        m = re.search(r"(20\d{2})", str(metin))
        if m:
            # Sadece yıl yazıyorsa tam yıl kabul edilir
            return int(m.group(1)), True
        return None

    def _tablo_yil_haritasi(self, tablo) -> dict[int, int]:
        """
        YENİ: Bir finansal tablonun başlık satırından kolon -> yıl
        eşleşmesi çıkarır. Büyüme hesabı için kolonların hangi döneme
        ait olduğunu bilmek şart; yıl tespit edilemezse büyüme hiç
        hesaplanmaz (yanlış yönde büyüme raporlamaktan iyidir).
        """
        harita: dict[int, int] = {}
        satirlar = tablo.find_all("tr")
        for tr in satirlar[:3]:
            hucreler = tr.find_all(["th", "td"])
            gecici: dict[int, int] = {}
            for idx, h in enumerate(hucreler):
                cozum = self._donem_coz_basligi(h.get_text(strip=True))
                if cozum is None:
                    continue
                yil, tam_yil = cozum
                # DÜZELTME: Ara dönem kolonları (2026/3 gibi) seriye
                # ALINMIYOR. Çeyrek rakamını yıllıkla karşılaştırmak
                # sahte daralma üretiyordu.
                if not tam_yil:
                    self._atlanan_ara_donemler.add(yil)
                    continue
                gecici[idx] = yil
            if len(gecici) >= 2:
                harita = gecici
                break
        return harita

    def _finansal_tablo_cikar(self, soup: BeautifulSoup, raw_text: str) -> tuple[dict, dict]:
        """
        DEĞİŞİKLİK: Artık iki şey döndürüyor:
          1) fin      -> en güncel dönemin değerleri
          2) seriler  -> {FinKey: {yil: deger}} çok dönemli seri
        Çok dönemli seri, büyüme puanlaması için gerekli.
        """
        fin: dict[FinKey, float] = {}
        seriler: dict[FinKey, dict[int, float]] = {}

        for tablo in soup.find_all("table"):
            yil_haritasi = self._tablo_yil_haritasi(tablo)
            for tr in tablo.find_all("tr"):
                tds = tr.find_all(["th", "td"])
                if len(tds) < 2:
                    continue
                baslik_norm = TextUtils.normalize(tds[0].get_text(strip=True))
                for alan, etiketler in self.FIN_LABELS.items():
                    if not TextUtils.etiket_eslesir(baslik_norm, etiketler):
                        continue
                    # Kolon kolon ilerle, yıl haritası varsa seriye yaz
                    for idx in range(1, len(tds)):
                        # DÜZELTME: tum_sayilari_bul "9,0 Milyar TL"yi
                        # 9.0 olarak okuyordu. tutar_coz birim ekini
                        # (milyar/milyon/bin) doğru çarpanla uyguluyor.
                        hucre = tds[idx].get_text(strip=True)
                        deger = TextUtils.tutar_coz(hucre)
                        if deger is None:
                            continue
                        yil = yil_haritasi.get(idx)
                        if yil:
                            seriler.setdefault(alan, {})[yil] = deger
                        if alan not in fin:
                            fin[alan] = deger
                    break

        # Metin bazlı yedek okuma (tablo yapısı bozuksa)
        lines = raw_text.split("\n")
        for i, line in enumerate(lines):
            nl = TextUtils.normalize(line)
            for alan, etiketler in self.FIN_LABELS.items():
                if alan in fin:
                    continue
                if any(nl == e or nl.startswith(e) for e in etiketler):
                    deger = TextUtils.tutar_coz(line)
                    if deger is None and i + 1 < len(lines):
                        deger = TextUtils.tutar_coz(lines[i + 1])
                    if deger is not None:
                        fin[alan] = deger

        # Serilerde en güncel yılın değerini "fin" için tercih et
        for alan, seri in seriler.items():
            if seri:
                fin[alan] = seri[max(seri.keys())]

        # Sadece 2+ dönemi olan serileri tut
        seriler = {k: v for k, v in seriler.items() if len(v) >= 2}
        return fin, seriler

    def _durum_belirle(self, tarih_metni: str, islem_tarihi_metni: str,
                       raw_text: str, kart_metni: str,
                       kesin_dagitim: bool = False) -> ArzDurumu:
        """
        REFAKTÖR: Tarih çözümleme mantığı TextUtils.tarih_araligi_coz'a
        taşındı; ay geçişli aralıklar (30 Temmuz - 1 Ağustos) artık
        doğru işleniyor.
        """
        rt = TextUtils.kucult(raw_text)
        kart = TextUtils.kucult(kart_metni)
        bugun = datetime.now().date()

        # YENİ: Ertelenen arzlar. Kaynak sitede "Ertelendi" rozetiyle
        # işaretleniyor; normal arz gibi göstermek yanıltıcı olur.
        if "ertelendi" in kart or "ertelenmiştir" in rt or "iptal edildi" in kart:
            return ArzDurumu.ERTELENDI

        if "işlem görmeye başlamıştır" in rt or "gong!" in kart:
            return ArzDurumu.ISLEM_GORMEYE_BASLADI

        islem_aralik = TextUtils.tarih_araligi_coz(islem_tarihi_metni)
        if islem_aralik and bugun >= islem_aralik[0]:
            return ArzDurumu.ISLEM_GORMEYE_BASLADI

        # DÜZELTME: "Dağıtılan Pay Miktarı" başlığı sayfada HER ZAMAN var
        # (içi "Hazırlanıyor..." olsa bile). Ham metinde bu ifadeyi arayan
        # eski kod, talep toplama aşamasındaki her arzı yanlışlıkla
        # "İşleme Girmesi Bekleniyor" durumuna geçiriyordu.
        if kesin_dagitim:
            return ArzDurumu.ISLEME_BEKLENIYOR

        talep_aralik = TextUtils.tarih_araligi_coz(tarih_metni)
        if not talep_aralik:
            return (
                ArzDurumu.HAZIRLANIYOR
                if ("taslak" in kart or "hazırlanıyor" in kart)
                else ArzDurumu.SPK_ONAYLI
            )

        bas, bit = talep_aralik
        if bugun < bas:
            return ArzDurumu.TALEP_YAKLASIYOR
        if bas <= bugun <= bit:
            return ArzDurumu.TALEP_TOPLANIYOR
        return ArzDurumu.DAGITIM_BEKLENIYOR

    # ─────────────── FAZ 1: sadece indir ve ayrıştır ───────────────

    async def _sirket_parse(self, sirket_adi: str, detay_linki: str,
                            kart_metni: str) -> Optional[dict]:
        """
        YENİ MİMARİ (2 fazlı):
        Faz 1 sadece veriyi indirir ve ayrıştırır — puanlama YAPMAZ.
        Çünkü "aynı hafta kaç arz var" bilgisi ancak tüm şirketler
        ayrıştırıldıktan sonra bilinebilir ve bu bilgi puanlamanın
        girdisidir.
        """
        html = await self._fetch_url_with_retry(detay_linki)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        raw_text = soup.get_text(separator="\n", strip=True)

        veri = dict(self.DEFAULTS)
        # DEĞİŞİKLİK: Önce siteye özel, yapısal ayrıştırıcılar çalışıyor.
        # Genel/metin tabanlı okuyucular yalnızca yedek olarak kaldı.
        self._ana_tablodan_doldur(veri, soup)
        self._ozet_bilgilerden_doldur(veri, soup)
        kesin_dagitim = self._kesin_dagitim_doldur(veri, soup)
        kurulus_yili = self._sirket_bilgisi_doldur(veri, soup)
        self._tablodan_doldur(veri, soup)
        self._satirlardan_doldur(veri, raw_text)
        fin, seriler = self._finansal_tablo_cikar(soup, raw_text)
        olasi_lot = self._olasi_lot_tablosu_coz(veri.get(InfoKey.DAGITIM_TABLOSU, ""))

        if veri.get(InfoKey.TARIH) == self.DEFAULTS.get(InfoKey.TARIH):
            tarih_match = re.search(
                r"(\d{1,2}(?:[-–]\d{1,2})*\s+(?:ocak|şubat|mart|nisan|mayıs|haziran|"
                r"temmuz|ağustos|eylül|ekim|kasım|aralık)\s+\d{4})",
                kart_metni, re.IGNORECASE
            )
            if tarih_match:
                veri[InfoKey.TARIH] = tarih_match.group(1).title()

        buyukluk_metni = str(veri.get(InfoKey.BUYUKLUK, ""))
        if "**" in buyukluk_metni:
            veri[InfoKey.BUYUKLUK] = buyukluk_metni.split("**")[0].strip()
        elif "Grafiği" in buyukluk_metni:
            veri[InfoKey.BUYUKLUK] = buyukluk_metni.split("Grafiği")[0].strip()

        # YENİ: Etiketli okuma başarısız olduysa ham metinden yedek çıkarım.
        # "Hisse fiyatı / işlem tarihi eksik görünüyor" sorununun çözümü.
        if veri.get(InfoKey.FIYAT) == self.DEFAULTS.get(InfoKey.FIYAT):
            yedek = self._yedek_fiyat_bul(raw_text)
            if yedek:
                veri[InfoKey.FIYAT] = yedek
        if veri.get(InfoKey.ISLEM_TARIHI) == self.DEFAULTS.get(InfoKey.ISLEM_TARIHI):
            yedek = self._yedek_islem_tarihi_bul(raw_text)
            if yedek:
                veri[InfoKey.ISLEM_TARIHI] = yedek

        durum = self._durum_belirle(
            veri.get(InfoKey.TARIH, ""), veri.get(InfoKey.ISLEM_TARIHI, ""),
            raw_text, kart_metni, kesin_dagitim
        )

        fiyat = TextUtils.sayi_bul(veri.get(InfoKey.FIYAT, ""))
        pay = TextUtils.sayi_bul(veri.get(InfoKey.PAY_SAYISI, ""))
        # DEĞİŞİKLİK: "4,5 Milyar TL" artık 4.5 değil 4_500_000_000 olarak
        # okunuyor (bkz. TextUtils.tutar_coz).
        buyukluk_ham = TextUtils.tutar_coz(veri.get(InfoKey.BUYUKLUK, ""))

        # YENİ VE ÖNEMLİ: Pay miktarı izahnameden okunamadıysa,
        # arz büyüklüğü / fiyat formülüyle türetiliyor.
        # Lot hesaplayıcının "hiç çalışmaması"nın en yaygın sebebi
        # pay miktarının boş kalmasıydı; bu fallback çoğu durumda
        # hesaplayıcıyı kurtarır.
        pay_kaynagi = "izahname"
        if not pay and buyukluk_ham and fiyat and fiyat > 0:
            pay = buyukluk_ham / fiyat
            pay_kaynagi = "hesaplandı (arz büyüklüğü ÷ fiyat)"
        elif not pay:
            pay_kaynagi = "bulunamadı"

        arz_buyuklugu = (fiyat * pay) if (fiyat and pay) else buyukluk_ham

        # ── İzahname finansallarını birleştir ──
        # Kaynak: GitHub Actions'ın hazırladığı JSON (bkz. finansal_depo.py)
        izahname_durumu = "kapali"
        izahname_url = None
        if self.finansal_depo is not None:
            try:
                from finansal_depo import kayittan_finansal_uret
                kayit = self.finansal_depo.bul(
                    sirket_adi, str(veri.get(InfoKey.BIST_KODU, ""))
                )
                if kayit is None:
                    izahname_durumu = "veri_yok"
                elif not kayit.get("guvenilir"):
                    # Doğrulamayı geçmemiş veri KULLANILMAZ.
                    izahname_durumu = "dogrulanamadi"
                    izahname_url = kayit.get("izahname_url")
                else:
                    pdf_fin, pdf_seriler = kayittan_finansal_uret(kayit, FinKey)
                    # İzahname verisi ÖNCELİKLİ: şirketin denetlenmiş
                    # bilançosundan geliyor, sitenin özetinden değil.
                    fin.update(pdf_fin)
                    seriler.update(pdf_seriler)
                    izahname_durumu = "hazir"
                    izahname_url = kayit.get("izahname_url")
            except Exception as e:
                logger.warning(f"Finansal veri okunamadı ({sirket_adi}): {e}")
                izahname_durumu = "hata"

        # YENİ: Hangi alanların çekilemediğini her yanıtta bildir.
        # Böylece debug anahtarı olmadan da eksik alan görülebiliyor.
        eksik_alanlar = [
            a.value for a in self.FIELD_LABELS
            if veri.get(a) == self.DEFAULTS.get(a)
        ]

        return {
            "sirket_adi": sirket_adi,
            "veri": veri,
            "fin": fin,
            "seriler": seriler,
            "raw_text": raw_text,
            "durum": durum,
            "talep_aralik": TextUtils.tarih_araligi_coz(veri.get(InfoKey.TARIH, "")),
            "arz_buyuklugu": arz_buyuklugu,
            "fiyat": fiyat,
            "pay": pay,
            "pay_kaynagi": pay_kaynagi,
            "eksik_alanlar": eksik_alanlar,
            "olasi_lot_tablosu": olasi_lot,
            "kurulus_yili": kurulus_yili,
            "izahname_url": izahname_url,
            "izahname_durumu": izahname_durumu,
        }

    # ─────────── FAZ 2: piyasa bağlamı hesapla ve puanla ───────────

    @staticmethod
    def _piyasa_baglami_olustur(parsed_list: list[dict]) -> dict[str, PiyasaBaglami]:
        """
        YENİ: Her şirket için, talep toplama tarihi ÇAKIŞAN diğer arzları
        sayar. Bu, "aynı hafta birden fazla şirket halka arz oluyorsa
        bireysel talep bölünür" gözlemini modele sokan bileşendir.
        """
        baglamlar: dict[str, PiyasaBaglami] = {}
        for p in parsed_list:
            ad = p["sirket_adi"]
            aralik = p["talep_aralik"]
            if not aralik:
                baglamlar[ad] = PiyasaBaglami(ayni_hafta_arz_sayisi=1)
                continue
            bas, bit = aralik
            rakipler: list[str] = []
            toplam = p["arz_buyuklugu"] or 0.0
            for d in parsed_list:
                if d["sirket_adi"] == ad or not d["talep_aralik"]:
                    continue
                d_bas, d_bit = d["talep_aralik"]
                # ±3 gün tolerans: aynı hafta içindeki arzlar da talebi böler
                if (d_bas - bit).days <= 3 and (bas - d_bit).days <= 3:
                    rakipler.append(d["sirket_adi"])
                    toplam += d["arz_buyuklugu"] or 0.0
            baglamlar[ad] = PiyasaBaglami(
                ayni_hafta_arz_sayisi=1 + len(rakipler),
                ayni_hafta_toplam_buyukluk=toplam,
                rakip_sirketler=rakipler,
            )
        return baglamlar

    def _sonuc_olustur(self, parsed: dict, baglam: PiyasaBaglami, debug: bool) -> dict:
        veri = parsed["veri"]
        durum = parsed["durum"]

        s = self.analyzer.skoru_topla(
            veri, parsed["fin"], parsed["seriler"], durum, parsed["raw_text"], baglam,
            olasi_lot=parsed.get("olasi_lot_tablosu"),
            kurulus_yili=parsed.get("kurulus_yili"),
            sirket_adi=parsed["sirket_adi"],
        )

        t_kalite = s["temel_kalite"]
        baski = s["baski"]

        # DEĞİŞİKLİK: Harf notu (A+, B, C, D, E) kaldırıldı. Harf notu
        # kullanıcıya "okul karnesi" gibi kesin bir yargı hissi veriyordu;
        # aynı bilgi zaten yıldız + sayısal skorla aktarılıyor.
        if t_kalite >= 85:
            rating = "★★★★★"
        elif t_kalite >= 70:
            rating = "★★★★☆"
        elif t_kalite >= 55:
            rating = "★★★☆☆"
        elif t_kalite >= 40:
            rating = "★★☆☆☆"
        elif t_kalite >= 20:
            rating = "★☆☆☆☆"
        else:
            rating = "☆☆☆☆☆"

        rt = TextUtils.kucult(parsed["raw_text"])
        t1_t2 = "t1-t2 kullanılabilir" in rt or "t1 ve t2 kullanılabilir" in rt
        katilim = ("katılım endeksine uygun değildir" not in rt
                   and "katılım endeksine uygun" in rt)
        islem_menusu = (
            "Hisse Alış/Satış Menüsü"
            if "borsada satış" in TextUtils.kucult(str(veri.get(InfoKey.DAGITIM_YONTEMI, "")))
            else "Halka Arz Menüsü"
        )

        if durum in (ArzDurumu.HAZIRLANIYOR, ArzDurumu.ERTELENDI):
            gorunum = Gorunum.HAZIRLIK
            if durum == ArzDurumu.ERTELENDI:
                degerlendirme = (
                    "Bu halka arz ertelendi. Yeni bir tarih açıklanana kadar "
                    "değerlendirme yapılmayacaktır."
                )
                rating = "Ertelendi"
            else:
                degerlendirme = (
                    "Şirket hazırlık aşamasında. İzahname henüz yayınlanmadığı "
                    "için finansal değerlendirme yapılamıyor; tarih ve fiyat "
                    "açıklandığında burada görünecek."
                )
                rating = "Hazırlanıyor"
        else:
            gorunum = (Gorunum.COK_GUCLU if t_kalite >= 80
                       else Gorunum.DENGELI if t_kalite >= 45
                       else Gorunum.RISKLI)
            parcalar = [
                f"Şirketin temel finansal kalitesi {t_kalite}/100 olarak ölçüldü "
                f"(sektör: {s['sektor']}). Risk seviyesi {s['risk']}/100."
            ]
            # DÜZELTME: Metin ayrı bir eşik (>=40) kullanıyordu, panel ise
            # baskı seviyesini (>=20 "Orta") gösteriyordu. Sonuçta aynı
            # ekranda "satış baskısı düşük" yazarken panelde "Orta · 36"
            # görünüyordu. Artık ikisi de AYNI seviye etiketini kullanıyor.
            seviye = baski.get("seviye", "Belirsiz")
            if baski["skor"] >= 20:
                parcalar.append(
                    f"İlk gün satış baskısı {seviye.lower()} seviyede "
                    f"({baski['skor']:.0f}/100); kısa vadeli yukarı yönlü hareket "
                    f"potansiyeli {s['tavan_potansiyeli']}/100 olarak hesaplandı."
                )
            else:
                parcalar.append(
                    f"İlk gün satış baskısı düşük ({baski['skor']:.0f}/100); "
                    f"kısa vadeli yukarı yönlü hareket potansiyeli "
                    f"{s['tavan_potansiyeli']}/100 olarak hesaplandı."
                )

            # YENİ: Değerleme yorumu da özete giriyor — kullanıcının
            # "fiyat pahalı mı?" sorusu en temel sorulardan biri.
            deg = s.get("degerleme") or {}
            if deg.get("genel_yorum") and deg.get("carpanlar"):
                parcalar.append(deg["genel_yorum"])
            if s["veri_guvenilirligi"] < 60:
                parcalar.append(
                    f"Bu analiz, izahnamede bulunabilen sınırlı veriyle (%{s['veri_guvenilirligi']} "
                    f"veri kapsamı) oluşturulmuştur; eksik kalemler skoru olduğundan farklı "
                    f"gösterebilir."
                )
            parcalar.append("Şirket katılım endeksine UYGUN." if katilim
                            else "Şirket katılım endeksine UYGUN DEĞİL.")
            degerlendirme = " ".join(parcalar)

        result = {
            "sirket": parsed["sirket_adi"],
            "bist_kodu": veri[InfoKey.BIST_KODU],
            "durum": durum,
            "islem_tarihi": veri[InfoKey.ISLEM_TARIHI],
            "skor": t_kalite,
            "temel_kalite_skoru": t_kalite,
            "tavan_potansiyeli_skoru": s["tavan_potansiyeli"],
            "risk_skoru": s["risk"],
            "sektor": s["sektor"],
            "yildiz": rating,
            "genel_gorunum": gorunum,
            "genel_degerlendirme": degerlendirme,
            "guclu_yanlar": s["guclu"],
            "riskler": s["risk_listesi"],
            "kirmizi_bayraklar": s["kirmizi_bayraklar"],
            "uyarilar": s["uyarilar"],
            "puan_detaylari": s["detaylar"],
            # ── YENİ: ilk gün satış baskısı bloğu ──
            "ilk_gun_satis_baskisi": baski["skor"],
            "ilk_gun_baski_seviyesi": baski["seviye"],
            "ilk_gun_baski_uyarisi": baski["uyari"],
            "ilk_gun_baski_gerekceleri": baski["gerekceler"],
            "kisi_basi_tahmini_tutar": baski["kisi_basi_tahmini_tutar"],
            "kisi_basi_tahmini_lot": baski["kisi_basi_tahmini_lot"],
            "ayni_hafta_arz_sayisi": baski["ayni_hafta_arz_sayisi"],
            "rakip_arzlar": baglam.rakip_sirketler,
            # ── mevcut alanlar ──
            "tarih": veri[InfoKey.TARIH],
            "fiyat": veri[InfoKey.FIYAT],
            "buyukluk": veri[InfoKey.BUYUKLUK],
            "aciklik": veri[InfoKey.ACIKLIK],
            "iskonto": veri[InfoKey.ISKONTO],
            "taahhut": veri[InfoKey.TAAHHUT],
            "halka_arz_sekli": veri[InfoKey.HALKA_ARZ_SEKLI],
            "fon_kullanim": veri[InfoKey.FON_KULLANIM],
            "satis_yontemi": veri[InfoKey.SATIS_YONTEMI],
            "fiyat_istikrari": veri[InfoKey.FIYAT_ISTIKRARI],
            "finansal_tablo": veri[InfoKey.FINANSAL_TABLO],
            "tahsisat": veri[InfoKey.TAHSISAT],
            "dagitim_tablosu": veri[InfoKey.DAGITIM_TABLOSU],
            "dagitim_tipi": veri[InfoKey.DAGITIM_TIPI],
            "dagitim_yontemi": veri.get(InfoKey.DAGITIM_YONTEMI, "Açıklanmadı"),
            "pay_miktari": veri.get(InfoKey.PAY_SAYISI, "Açıklanmadı"),
            "araci_kurum": veri.get(InfoKey.ARACI_KURUM, "Açıklanmadı"),
            "pazar": veri.get(InfoKey.PAZAR, "Açıklanmadı"),
            "t1_t2_kullanilabilir": t1_t2,
            "katilim_endeksine_uygun": katilim,
            "islem_menusu": islem_menusu,
            "veri_guvenilirligi": s["veri_guvenilirligi"],
            "finansal_veri_var": s.get("finansal_veri_var", False),
            # Skor yeterli veriyle mi hesaplandı? False ise uygulamada
            # kesin bir puan gibi gösterilmemeli.
            "skor_guvenilir": s.get("skor_guvenilir", False),
            "olculemeyen_boyutlar": s.get("olculemeyen_boyutlar", []),
            # YENİ: F/K, PD/DD, FD/FAVÖK oranları ve "pahalı mı ucuz mu"
            # yorumu. Daha önce sadece skora gömülüydü.
            "degerleme": s.get("degerleme", {}),
            # İzahname PDF'i işlendi mi? "isleniyor" ise finansal veri
            # bir sonraki istekte hazır olacak.
            "izahname_durumu": parsed.get("izahname_durumu", "kapali"),
            "izahname_url": parsed.get("izahname_url"),
            "tahmini_volatilite": s["volatilite"],
            "model_surumu": MODEL_SURUMU,
            # ── YENİ: Lot hesaplayıcı için hazır sayısal değerler ──
            # Uygulama metinden sayı ayıklamaya çalışırken "112.500.000 Lot
            # (ek satış dahil 129.375.000)" gibi ifadelerde iki sayıyı
            # birleştirip saçma sonuç üretiyordu. Artık ayrıştırma
            # sunucuda yapılıyor ve tek doğru sayı gönderiliyor.
            "fiyat_sayi": parsed.get("fiyat"),
            "pay_sayisi_sayi": parsed.get("pay"),
            "arz_buyuklugu_sayi": parsed.get("arz_buyuklugu"),
            # Pay miktarı izahnameden mi okundu, yoksa türetildi mi?
            "pay_sayisi_kaynagi": parsed.get("pay_kaynagi"),
            # İzahnameden çekilemeyen alanlar (tanı amaçlı)
            "eksik_alanlar": parsed.get("eksik_alanlar", []),
            # ── YENİ: Yapısal finansal tablo ──
            "finansal_tablo_yapisal": self._yapisal_finansal_tablo(
                parsed["fin"], parsed["seriler"]
            ),
        }

        if debug:
            result["debug_bilgisi"] = {
                "veri_ham": {k.value: v for k, v in veri.items()},
                "finansal_ham": {k.value: v for k, v in parsed["fin"].items()},
                "finansal_seriler": {
                    k.value: v for k, v in parsed["seriler"].items()
                },
                "default_kalanlar": [
                    a.value for a in self.FIELD_LABELS
                    if veri.get(a) == self.DEFAULTS.get(a)
                ],
                "baski_detay": baski,
                # Sektörün neden o şekilde belirlendiğini gösterir;
                # yanlış sınıflandırmayı ayıklamayı kolaylaştırır.
                "sektor_puan_dokumu": self.analyzer.sektor_puan_dokumu(
                    parsed["raw_text"], parsed["sirket_adi"]
                ),
            }

        return result

    # ───────────────────────── ORKESTRA ─────────────────────────

    async def analiz_et(self, debug: bool = False) -> list[dict]:
        html = await self._fetch_url_with_retry(SETTINGS.BASE_URL)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        gorulen: set[str] = set()
        meta_list: list[tuple[str, str, str]] = []
        atlanan = 0

        for etiket in soup.find_all("h3"):
            link = etiket.find("a") or etiket.find_parent("a")
            if not link or "href" not in link.attrs:
                continue
            sirket_adi = etiket.get_text(strip=True)
            if sirket_adi in gorulen or len(sirket_adi) <= 3:
                continue

            parent_li = etiket.find_parent("li")
            kart_metni = TextUtils.kucult(
                parent_li.get_text(strip=True) if parent_li else sirket_adi
            )

            # DÜZELTME: Bu filtre yalnızca sabit bir kelime listesine
            # bakıyordu. Kaynak site kart etiketlerini değiştirdiğinde
            # veya yeni bir arz farklı bir rozetle yayınlandığında
            # şirket tamamen atlanıyordu — "bugün 4 arz geldi ama
            # görünmüyor" şikayetinin sebeplerinden biri buydu.
            #
            # Artık kelime eşleşmesine ek olarak, kartta GÜNCEL YIL
            # geçiyorsa da şirket alınıyor. Yeni bir arzın kartında
            # neredeyse her zaman tarihi yer alır.
            # DÜZELTME: Önceki sürüm "kartta güncel yıl geçiyorsa al"
            # diyordu ve bu ÇOK GEVŞEKTİ — GDZ Elektrik, Sakarya
            # Elektrik, Tredaş, Aras Elektrik gibi yıllar önceki arzlar
            # listeye sızıyordu.
            #
            # Artık yalnızca kaynak sitenin GÜNCELLİK ROZETİ taşıyan
            # kartları alınıyor. Site yeni arzları "Yeni!", işleme
            # gireni "Gong!", ertelenmiş olanı "Ertelendi" ile
            # işaretliyor; eski arzlarda bu rozetler yok.
            guncel_rozet = any(b in kart_metni for b in [
                "yeni!", "yeni !", "gong!", "gong !", "ertelendi",
                "talep toplan", "hazırlanıyor", "dağıtım bekleniyor",
            ])
            if not guncel_rozet:
                atlanan += 1
                continue

            gorulen.add(sirket_adi)
            meta_list.append((sirket_adi, link["href"], kart_metni))
            if len(meta_list) >= SETTINGS.MAX_SIRKET:
                logger.info(
                    f"MAX_SIRKET sınırına ({SETTINGS.MAX_SIRKET}) ulaşıldı; "
                    f"listede daha fazla şirket olabilir."
                )
                break

        logger.info(
            f"Kaynak sayfada {len(gorulen) + atlanan} aday; "
            f"{len(meta_list)} şirket işlenecek, {atlanan} kart filtrelendi."
        )

        semaphore = asyncio.Semaphore(SETTINGS.ESZAMANLI_ISTEK_LIMITI)

        async def _sinirli_parse(ad: str, href: str, kart: str) -> Optional[dict]:
            async with semaphore:
                sonuc = await self._sirket_parse(ad, href, kart)
                await asyncio.sleep(SETTINGS.ISTEK_ARASI_BEKLEME)
                return sonuc

        tasks = [asyncio.create_task(_sinirli_parse(a, h, k)) for a, h, k in meta_list]
        parse_sonuclari = await asyncio.gather(*tasks, return_exceptions=True)

        parsed_list: list[dict] = []
        for res in parse_sonuclari:
            if isinstance(res, Exception):
                logger.exception(f"Şirket ayrıştırılırken hata: {res}")
                continue
            if res:
                parsed_list.append(res)

        # FAZ 2: piyasa bağlamını hesapla, sonra puanla
        # DEĞİŞİKLİK: Gösterilmeyecek durumlar (Hazırlanıyor / SPK Onaylı)
        # daha puanlamaya girmeden eleniyor. Hem gereksiz iş yapılmıyor
        # hem de aynı hafta rakip arz sayımı, gerçekten talep toplayacak
        # arzlar üzerinden hesaplanıyor.
        # YENİ: Borsada işlem görmeye başlayalı çok olmuş arzlar listeden
        # düşürülüyor. Aksi halde liste aylar önceki arzlarla doluyor ve
        # kullanıcı güncel arzları göremiyor.
        def _eski_mi(p) -> bool:
            """
            İki kademeli eskime kontrolü:

            1) Borsada işlem görmeye başlayan arz, ISLEM_GOREN_GOSTERIM_GUNU
               gün sonra listeden düşer (varsayılan 1 gün — kullanıcı
               "işlem günü girsin, ertesi gün silinsin" istedi).
            2) Talep toplaması ESKİDEN bitmiş ama hâlâ bir ara durumda
               görünen arzlar da düşer. Rozet filtresini aşan eski
               kayıtlara karşı ikinci savunma hattı.
            """
            bugun = datetime.now().date()

            if p["durum"] == ArzDurumu.ISLEM_GORMEYE_BASLADI:
                aralik = TextUtils.tarih_araligi_coz(
                    p["veri"].get(InfoKey.ISLEM_TARIHI, "")
                ) or p.get("talep_aralik")
                if not aralik:
                    return False
                return (bugun - aralik[1]).days > ISLEM_GOREN_GOSTERIM_GUNU

            # Hazırlık/erteleme aşamasında tarih olmaz; bunlar elenmez
            if p["durum"] in (ArzDurumu.HAZIRLANIYOR, ArzDurumu.ERTELENDI,
                              ArzDurumu.SPK_ONAYLI):
                return False

            aralik = p.get("talep_aralik")
            if not aralik:
                return False
            return (bugun - aralik[1]).days > ESKI_ARZ_GUNU

        eskiler = [p for p in parsed_list if _eski_mi(p)]
        if eskiler:
            logger.info(
                f"{len(eskiler)} arz {ISLEM_GOREN_GOSTERIM_GUNU} günden eski, "
                f"listeden düşürüldü: "
                + ", ".join(p["sirket_adi"] for p in eskiler[:8])
            )
        parsed_list = [p for p in parsed_list if not _eski_mi(p)]

        elenen = [p for p in parsed_list if p["durum"].value not in GOSTERILEN_DURUMLAR]
        if elenen:
            logger.info(
                f"Durum filtresiyle gizlenen {len(elenen)} şirket: "
                + ", ".join(f"{p['sirket_adi']} [{p['durum'].value}]"
                            for p in elenen[:10])
            )
        parsed_list = [p for p in parsed_list if p["durum"].value in GOSTERILEN_DURUMLAR]

        baglamlar = self._piyasa_baglami_olustur(parsed_list)

        sirket_listesi: list[dict] = []
        for p in parsed_list:
            try:
                baglam = baglamlar.get(p["sirket_adi"], PiyasaBaglami())
                sirket_listesi.append(self._sonuc_olustur(p, baglam, debug))
            except Exception as e:
                logger.exception(f"{p['sirket_adi']} puanlanırken hata: {e}")

        # YENİ: Duruma göre sırala — talep toplanan en üstte, ertelenen
        # en altta. Kaynak sitenin DOM sırasına güvenmek yerine
        # yatırımcı için anlamlı bir sıra kuruluyor.
        sirket_listesi.sort(
            key=lambda s: (DURUM_SIRASI.get(s.get("durum"), 99),
                           -(s.get("temel_kalite_skoru") or 0))
        )
        return sirket_listesi

    async def close(self):
        if self.session and not getattr(self.session, "closed", True):
            await self.session.close()


# ═══════════════════════════════════════════════════════════════════
# 🚀 6. FASTAPI UYGULAMASI VE CACHE
# ═══════════════════════════════════════════════════════════════════

class CacheManager:
    def __init__(self):
        self._timestamp: float = 0.0
        self._data: list = []
        self._lock = asyncio.Lock()

    async def get(self) -> Optional[list]:
        async with self._lock:
            if time.time() - self._timestamp < SETTINGS.CACHE_TTL and self._data:
                return self._data
        return None

    async def set(self, data: list):
        async with self._lock:
            self._data = data
            self._timestamp = time.time()

    async def invalidate(self):
        async with self._lock:
            self._timestamp = 0.0
            self._data = []


CACHE = CacheManager()


def check_debug_permission(x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key")) -> bool:
    if not SETTINGS.DEBUG_API_KEY:
        return False
    return x_debug_key == SETTINGS.DEBUG_API_KEY


@asynccontextmanager
async def lifespan(app: FastAPI):
    extractor = DataExtractor()
    app.state.extractor = extractor
    bayat, gun = TextUtils.sektor_verisi_bayat_mi()
    logger.info(f"Uygulama başlatıldı. Model: {MODEL_SURUMU}")
    if bayat:
        logger.warning(
            f"Sektör çarpanları {gun} gün önceki verilere ait; güncellenmesi önerilir "
            f"(SEKTOR_VERI_TARIHI ortam değişkeni)."
        )
    yield
    await extractor.close()
    logger.info("Uygulama kapatıldı.")


class UTF8JSONResponse(JSONResponse):
    """
    Tarayıcıda Türkçe karakterler bozuk görünüyordu
    ("İlk gün satış" -> "Ä°lk gÃ¼n satÄ±ÅŸ").

    Sebep: yanıt UTF-8 kodlanıyor ama Content-Type başlığında karakter
    kümesi belirtilmediği için tarayıcı latin-1 varsayıyordu.

    media_type sınıf değişkenini ayarlamak bazı Starlette sürümlerinde
    yeterli olmuyor; init_headers charset'i ayrıca ekliyor. Bu yüzden
    başlık render sırasında doğrudan yazılıyor.
    """
    media_type = "application/json"

    def render(self, content) -> bytes:
        # ensure_ascii=False: Türkçe karakterler \uXXXX kaçışına
        # dönüşmeden, okunabilir biçimde gönderilir.
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            indent=None, separators=(",", ":"),
        ).encode("utf-8")

    def init_headers(self, headers=None) -> None:
        super().init_headers(headers)
        self.raw_headers = [
            (k, b"application/json; charset=utf-8")
            if k.lower() == b"content-type" else (k, v)
            for k, v in self.raw_headers
        ]


app = FastAPI(
    title="Halka Arz Asistanı Pro",
    description="Halka arz analiz ve puanlama API'si",
    version=MODEL_SURUMU,
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if SETTINGS.ALLOWED_ORIGINS == "*"
    else SETTINGS.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _meta_bilgisi() -> dict:
    bayat, gun = TextUtils.sektor_verisi_bayat_mi()
    return {
        "model_surumu": MODEL_SURUMU,
        "sektor_veri_tarihi": SEKTOR_VERI_TARIHI,
        "sektor_verisi_bayat": bayat,
        "sektor_verisi_yasi_gun": gun,
        "model_notu": (
            "İlk gün satış baskısı modeli gözleme dayalıdır ve henüz geriye dönük "
            "test edilmemiştir. Çıktılar tahmin değil, risk uyarısı olarak yorumlanmalıdır."
        ),
    }


@app.get("/api/halkarzlar")
async def get_halka_arzlar(
    debug: bool = Query(False, description="Debug modu (ham verileri döner)"),
    x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key"),
):
    if debug and not check_debug_permission(x_debug_key):
        raise HTTPException(status_code=403, detail="Debug modu için geçerli API Key gerekli.")

    if not debug:
        cached = await CACHE.get()
        if cached:
            return {"halka_arzlar": cached, "uyari": YATIRIM_UYARISI, "meta": _meta_bilgisi()}

    extractor: DataExtractor = app.state.extractor
    try:
        veriler = await extractor.analiz_et(debug=debug)
    except Exception as e:
        logger.exception(f"Analiz sırasında beklenmeyen hata: {e}")
        raise HTTPException(
            status_code=502,
            detail="Veri kaynağına şu anda ulaşılamıyor, lütfen daha sonra tekrar deneyin.",
        )

    if veriler and not debug:
        await CACHE.set(veriler)

    return {"halka_arzlar": veriler, "uyari": YATIRIM_UYARISI, "meta": _meta_bilgisi()}


@app.post("/api/cache/clear")
async def clear_cache(x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key")):
    if not check_debug_permission(x_debug_key):
        raise HTTPException(status_code=403, detail="Yetkisiz işlem.")
    await CACHE.invalidate()
    return {"detail": "Cache temizlendi."}


@app.get("/health")
async def health_check():
    """Sunucunun ayakta olup olmadığını ve hangi sürümün çalıştığını gösterir."""
    saglik = {"status": "ok", "timestamp": time.time(), "meta": _meta_bilgisi()}
    # Finansal veri dosyaları yüklendi mi? Deploy sonrası kontrol için.
    try:
        depo = getattr(app.state.extractor, "finansal_depo", None)
        if depo is not None:
            saglik["finansal_veri"] = depo.durum_ozeti()
        else:
            saglik["finansal_veri"] = {"dosya_sayisi": 0, "modul": "yok"}
    except Exception:
        saglik["finansal_veri"] = {"hata": True}
    return saglik


if __name__ == "__main__":
    uvicorn.run("proje:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 8000)), reload=False)