# main.py
import os
import re
import time
import asyncio
import logging
from datetime import datetime, date
from enum import Enum
from typing import Optional, ClassVar
from dataclasses import dataclass
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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


SETTINGS = AppSettings()


# ═══════════════════════════════════════════════════════════════════
# 🔤 2. ENUM'LAR VE AĞIRLIKLAR
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# 🔤 3. YARDIMCI ARAÇLAR
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
        try:
            return float((eslesme.group(1) or eslesme.group(2)).replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def sayi_bul(metin: Optional[str]) -> Optional[float]:
        if not metin:
            return None
        desenler = re.findall(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", metin)
        for d in desenler:
            return float(d.replace(".", "").replace(",", "."))

        desenler = re.findall(r"-?\d+,\d+", metin)
        for d in desenler:
            return float(d.replace(",", "."))

        desenler = re.findall(r"-?\d+\.\d+", metin)
        for d in desenler:
            return float(d)

        desenler = re.findall(r"-?\d+", metin)
        for d in desenler:
            return float(d)

        return None

    @staticmethod
    def etiket_eslesir(baslik_norm: str, etiketler: list[str]) -> bool:
        if not baslik_norm:
            return False
        for e in etiketler:
            if baslik_norm == e:
                return True
            if re.search(rf"(?:^|\s){re.escape(e)}(?:$|\s)", baslik_norm):
                return True
        return False


# ═══════════════════════════════════════════════════════════════════
# 💰 4. SKORLAMA VE ANALİZ MOTORU
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoreResult:
    score: float
    max_possible: float
    explanation: str
    has_data: bool


class ScoreAnalyzer:
    def __init__(self, weights: ScoreWeights = WEIGHTS):
        self.weights = weights
        self.BUYUME_ANAHTAR = [
            "kapasite artırım", "yeni tesis", "yenilenebilir", "ar-ge",
            "yatırım", "makine", "teçhizat", "büyüme"
        ]
        self.BORC_ANAHTAR = [
            "borç", "kredi", "finansal borç", "borç ödeme", "kredi kapama"
        ]
        self.SECTOR_AVERAGES = {
            "TEKNOLOJİ": {"fk": 18.0, "pddd": 4.5},
            "ENERJİ":    {"fk": 14.0, "pddd": 2.5},
            "SANAYİ":    {"fk": 10.0, "pddd": 1.8},
            "GIDA":      {"fk": 12.0, "pddd": 2.0},
            "GYO":       {"fk": 5.0,  "pddd": 0.8},
            "FİNANS":    {"fk": 6.0,  "pddd": 1.2},
            "GENEL":     {"fk": 12.0, "pddd": 2.0}
        }

    def _get_sektor(self, metin: str) -> str:
        m = metin.lower()
        if any(k in m for k in ["yazılım", "teknoloji", "bilişim", "savunma", "siber", "yapay zeka"]):
            return "TEKNOLOJİ"
        if any(k in m for k in ["enerji", "elektrik", "yenilenebilir", "rüzgar", "güneş"]):
            return "ENERJİ"
        if any(k in m for k in ["çimento", "beton", "demir", "çelik", "inşaat", "sanayi", "üretim", "otomotiv", "makine"]):
            return "SANAYİ"
        if any(k in m for k in ["gıda", "tarım", "hayvancılık", "süt", "et "]):
            return "GIDA"
        if any(k in m for k in ["gayrimenkul", "gyo", "emlak"]):
            return "GYO"
        if any(k in m for k in ["finans", "sigorta", "banka", "faktoring", "yatırım"]):
            return "FİNANS"
        return "GENEL"

    def finansal_puanla(self, fin: dict, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        puan, max_possible, aciklamalar, has_data = 0.0, 0.0, [], False

        net_kar = fin.get(FinKey.NET_KAR)
        hasilat = fin.get(FinKey.HASILAT)
        ozk = fin.get(FinKey.OZKAYNAK)

        if net_kar is not None:
            has_data = True
            max_possible += self.weights.FINANSAL_NET_KAR
            if net_kar < 0:
                aciklamalar.append(f"Şirket Zararda ({net_kar:,.0f} TL).")
                kirmizi_bayraklar.append("🚨 Son açıklanan net dönem zararı mevcut.")
                ceza_sozlugu["zarar"] = 20
            else:
                if ozk and ozk > 0:
                    roe = (net_kar / ozk) * 100
                    if roe >= 30:
                        puan += self.weights.FINANSAL_NET_KAR
                        aciklamalar.append(f"Çok kârlı özkaynak kullanımı (ROE: %{roe:.1f}).")
                    elif roe >= 15:
                        puan += self.weights.FINANSAL_NET_KAR * 0.7
                        aciklamalar.append(f"Pozitif özkaynak kârlılığı (ROE: %{roe:.1f}).")
                    else:
                        puan += self.weights.FINANSAL_NET_KAR * 0.3
                        aciklamalar.append(f"Düşük özkaynak kârlılığı (ROE: %{roe:.1f}).")
                elif hasilat and hasilat > 0:
                    marj = (net_kar / hasilat) * 100
                    if marj >= 25:
                        puan += self.weights.FINANSAL_NET_KAR
                        aciklamalar.append(f"Yüksek kâr marjı (%{marj:.1f}).")
                    elif marj >= 10:
                        puan += self.weights.FINANSAL_NET_KAR * 0.6
                        aciklamalar.append(f"Makul kâr marjı (%{marj:.1f}).")
                    else:
                        puan += self.weights.FINANSAL_NET_KAR * 0.2
                        aciklamalar.append(f"Düşük kâr marjı (%{marj:.1f}).")

        donen = fin.get(FinKey.DONEN_VARLIK)
        kv_yuk = fin.get(FinKey.KISA_VADELI_YUKUMLULUK)
        if donen is not None and kv_yuk and kv_yuk > 0:
            has_data = True
            max_possible += self.weights.FINANSAL_CARI
            oran = donen / kv_yuk
            if oran >= 2.0:
                puan += self.weights.FINANSAL_CARI
                aciklamalar.append(f"Güçlü likidite yapısı (Cari oran: {oran:.2f}).")
            elif oran >= 1.2:
                puan += self.weights.FINANSAL_CARI * 0.7
                aciklamalar.append(f"Dengeli likidite (Cari oran: {oran:.2f}).")
            else:
                aciklamalar.append(f"Likidite riski, ödeme zorluğu (Cari oran: {oran:.2f}).")

        borc = fin.get(FinKey.TOPLAM_BORC)
        if borc is not None and ozk:
            has_data = True
            max_possible += self.weights.FINANSAL_BORC
            if ozk <= 0:
                kirmizi_bayraklar.append("🚨 Özsermaye Negatif (Teknik İflas Durumu!).")
                ceza_sozlugu["iflas"] = 30
            else:
                oran = borc / ozk
                if oran <= 0.5:
                    puan += self.weights.FINANSAL_BORC
                    aciklamalar.append(f"Düşük borçluluk (Borç/Özk: {oran:.2f}).")
                elif oran <= 1.5:
                    puan += self.weights.FINANSAL_BORC * 0.6
                    aciklamalar.append(f"Kabul edilebilir borç (Borç/Özk: {oran:.2f}).")
                else:
                    aciklamalar.append(f"Yüksek borç yükü! (Borç/Özk: {oran:.2f}).")
                    if oran >= 4.0:
                        kirmizi_bayraklar.append(f"🚨 Kritik borçluluk rasyosu (Borç/Özkaynak: {oran:.1f}).")
                        ceza_sozlugu["yuksek_borc"] = 15

        if not has_data:
            return ScoreResult(0, 0, "Finansal veriler eksik/gizlenmiş.", False)

        return ScoreResult(round(puan, 1), max_possible, " ".join(aciklamalar), True)

    def degerleme_puanla(self, fiyat_metni: str, pay_metni: str, fin: dict, raw_text: str, kirmizi_bayraklar: list) -> ScoreResult:
        puan, max_possible, aciklamalar, has_data = 0.0, 0.0, [], False
        fiyat = TextUtils.sayi_bul(fiyat_metni)
        pay = TextUtils.sayi_bul(pay_metni)
        net_kar = fin.get(FinKey.NET_KAR)
        ozk = fin.get(FinKey.OZKAYNAK)
        sektor = self._get_sektor(raw_text)

        market_cap = (fiyat * pay) if (fiyat and pay) else None

        if market_cap and net_kar and net_kar > 0:
            fk = market_cap / net_kar
            if 1.0 <= fk <= 250.0:
                has_data = True
                max_possible += self.weights.DEGERLEME_FK
                ort_fk = self.SECTOR_AVERAGES[sektor]["fk"]
                fark = ((ort_fk - fk) / ort_fk) * 100

                if fark > 15:
                    puan += self.weights.DEGERLEME_FK
                    aciklamalar.append(f"Sektör ortalamasına göre iskontolu F/K ({fk:.1f}).")
                elif -15 <= fark <= 15:
                    puan += self.weights.DEGERLEME_FK * 0.6
                    aciklamalar.append(f"Sektör ortalamasında değerleme (F/K: {fk:.1f}).")
                else:
                    aciklamalar.append(f"Sektörüne göre primli/pahalı (F/K: {fk:.1f}).")
                    if fark < -50:
                        kirmizi_bayraklar.append(f"🚨 F/K oranı sektör ortalamasının çok üzerinde ({fk:.1f}).")

        if market_cap and ozk and ozk > 0:
            pddd = market_cap / ozk
            if 0.2 <= pddd <= 50.0:
                has_data = True
                max_possible += self.weights.DEGERLEME_PDDD
                ort_pddd = self.SECTOR_AVERAGES[sektor]["pddd"]
                fark = ((ort_pddd - pddd) / ort_pddd) * 100

                if fark > 10:
                    puan += self.weights.DEGERLEME_PDDD
                    aciklamalar.append(f"Cazip PD/DD oranı ({pddd:.2f}).")
                elif -10 <= fark <= 10:
                    puan += self.weights.DEGERLEME_PDDD * 0.6
                    aciklamalar.append(f"Makul PD/DD ({pddd:.2f}).")
                else:
                    aciklamalar.append(f"Yüksek PD/DD ({pddd:.2f}).")

        if not has_data:
            return ScoreResult(0, 0, "Değerleme hesaplanamadı (Veri uyuşmazlığı/Eksiklik).", False)

        return ScoreResult(round(puan, 1), max_possible, " ".join(aciklamalar), True)

    def fon_kullanim_puanla(self, metin: str, fin: dict, kirmizi_bayraklar: list) -> ScoreResult:
        mx = self.weights.MAX[Category.FON_KULLANIM]
        if not metin or metin in ("-", "açıklanmadı", ""):
            return ScoreResult(0, 0, "Fon kullanım yeri gizlenmiş/belirsiz.", False)

        satirlar = metin.lower().split("\n")
        buyume_toplam, borc_toplam, herhangi_yuzde = 0.0, 0.0, False

        for satir in satirlar:
            yuzde = TextUtils.yuzde_bul(satir)
            if yuzde is not None:
                herhangi_yuzde = True
            if any(k in satir for k in self.BUYUME_ANAHTAR):
                buyume_toplam += yuzde if yuzde else 0
            if any(k in satir for k in self.BORC_ANAHTAR):
                borc_toplam += yuzde if yuzde else 0

        if not herhangi_yuzde:
            return ScoreResult(0, 0, "Oransal fon dağılımı bulunamadı.", False)

        borc = fin.get(FinKey.TOPLAM_BORC)
        ozk = fin.get(FinKey.OZKAYNAK)
        if borc and ozk and ozk > 0 and (borc / ozk) > 2.0 and borc_toplam > 30:
            return ScoreResult(
                mx * 0.8, mx,
                f"Ağır borç yükünü hafifletmek için fonun %{borc_toplam:.0f}'i borca ayrılmış (Olumlu).",
                True
            )

        if borc_toplam >= 70:
            kirmizi_bayraklar.append(f"🚨 Halka arz gelirinin çok büyük kısmı (%{borc_toplam:.0f}) borç ödemeye gidiyor.")

        buyume_katki = (min(buyume_toplam, 100) / 70.0) * mx
        borc_ceza = (min(borc_toplam, 100) / 40.0) * mx
        puan = max(0.0, min(mx, buyume_katki - borc_ceza))
        return ScoreResult(
            round(puan, 1), mx,
            f"Büyüme/Yatırım ~%{min(buyume_toplam, 100):.0f}, Borç ödemesi ~%{min(borc_toplam, 100):.0f}.",
            True
        )

    def arz_yapisi_puanla(self, metin: str, kirmizi_bayraklar: list, ceza_sozlugu: dict) -> ScoreResult:
        mx = self.weights.MAX[Category.ARZ_YAPISI]
        if not metin or metin in ("-", "açıklanmadı"):
            return ScoreResult(0, 0, "Arz yapısı belirsiz.", False)

        m_lower = metin.lower()
        olumsuz = re.sub(
            r"(ortak satış[ıi]?|mevcut pay satış[ıi]?)\s*[^.]{0,15}\b(yok|bulunmuyor|bulunmamaktadır)\b",
            "", m_lower
        )
        ortak = "ortak satış" in olumsuz or "mevcut pay satış" in olumsuz

        if "sermaye artırımı" in m_lower and not ortak:
            return ScoreResult(mx, mx, "Tamamen sermaye artırımı (Fon kasada kalıyor).", True)
        if "sermaye artırımı" in m_lower and ortak:
            return ScoreResult(mx * 0.4, mx, "Kısmi ortak satışı var.", True)
        if ortak:
            kirmizi_bayraklar.append("🚨 Tamamen ortak satışı! Halka arz geliri şirketin kasasına GİRMİYOR.")
            ceza_sozlugu["ortak_satis"] = 15
            return ScoreResult(0, mx, "Tamamen ortak satışı (Fon kasaya girmiyor!).", True)
        return ScoreResult(0, 0, "Belirsiz arz yapısı.", False)

    def iskonto_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ISKONTO]
        isk = TextUtils.yuzde_bul(metin)
        if isk is None:
            return ScoreResult(0, 0, "İskonto belirsiz.", False)
        mult = 1.0 if isk >= 25 else 0.7 if isk >= 20 else 0.3 if isk >= 15 else 0.0
        return ScoreResult(round(mx * mult, 1), mx, f"İskonto oranı: %{isk:.0f}.", True)

    def aciklik_puanla(self, metin: str) -> ScoreResult:
        mx = self.weights.MAX[Category.ACIKLIK]
        a = TextUtils.yuzde_bul(metin)
        if a is None:
            return ScoreResult(0, 0, "Açıklık belirsiz.", False)
        if a < 10:
            p, not_ = 0.0, "Riskli-Dar Hacim"
        elif a <= 25:
            p, not_ = 0.8, "İdeal"
        elif a <= 35:
            p, not_ = 1.0, "Dengeli"
        elif a <= 45:
            p, not_ = 0.3, "Fazla Yüksek"
        else:
            p, not_ = 0.0, "Riskli-Tahta Ağır"
        return ScoreResult(round(mx * p, 1), mx, f"Halka açıklık oranı %{a:.0f} ({not_}).", True)

    def satmama_puanla(self, metin: str, ceza_sozlugu: dict) -> ScoreResult:
        mx = self.weights.MAX[Category.SATMAMA]
        m = (metin or "").lower()
        if "1 yıl" in m or "2 yıl" in m or "18 ay" in m:
            return ScoreResult(mx, mx, "Kurumsal satmama taahhüdü mevcut.", True)
        if "yok" in m or "bulunmuyor" in m:
            ceza_sozlugu["satmama_yok"] = 2
            return ScoreResult(0, mx, "Satmama taahhüdü bulunmuyor.", True)
        return ScoreResult(0, 0, "Taahhüt belirsiz.", False)

    def kurumsallik_puanla(self, raw_text: str) -> ScoreResult:
        mx = self.weights.MAX[Category.KURUMSALLIK]
        m = raw_text.lower()
        yillar = [int(y) for y in re.findall(r"(19[5-9]\d|20\d\d)\s*yılında\s*kurul", m)]
        puan = 0.0
        aciklamalar = []

        if yillar:
            yas = datetime.now().year - min(yillar)
            if yas >= 20:
                puan += mx * 0.5
                aciklamalar.append(f"Köklü şirket geçmişi (~{yas} yıl).")
            elif yas >= 10:
                puan += mx * 0.3
                aciklamalar.append(f"Kurumsal yapı (~{yas} yıl).")

        if any(k in m for k in ["bağımsız denetim", "kurumsal yönetim"]):
            puan += mx * 0.2
            aciklamalar.append("Bağımsız denetim yapısı mevcut.")
        if any(k in m for k in ["iso ", "esg", "sürdürülebilirlik", "bist100", "bist 100"]):
            puan += mx * 0.3
            aciklamalar.append("Kurumsal yönetim/Sürdürülebilirlik vizyonu.")

        if not yillar and not aciklamalar:
            return ScoreResult(0, 0, "Kurumsallık verisi bulunamadı.", False)
        return ScoreResult(round(min(mx, puan), 1), mx, " ".join(aciklamalar), True)

    def skoru_topla(self, veri: dict, fin: dict, durum: ArzDurumu, raw_text: str):
        kirmizi_bayraklar = []
        ceza_sozlugu = {}

        if durum == ArzDurumu.HAZIRLANIYOR:
            return 0.0, 0.0, 0.0, [], [], kirmizi_bayraklar, [], 0, "Belirsiz"

        hesaplamalar = [
            (Category.FINANSAL, self.finansal_puanla(fin, kirmizi_bayraklar, ceza_sozlugu)),
            (Category.DEGERLEME, self.degerleme_puanla(
                veri.get(InfoKey.FIYAT, ""), veri.get(InfoKey.PAY_SAYISI, ""), fin, raw_text, kirmizi_bayraklar
            )),
            (Category.FON_KULLANIM, self.fon_kullanim_puanla(veri.get(InfoKey.FON_KULLANIM, ""), fin, kirmizi_bayraklar)),
            (Category.ARZ_YAPISI, self.arz_yapisi_puanla(veri.get(InfoKey.HALKA_ARZ_SEKLI, ""), kirmizi_bayraklar, ceza_sozlugu)),
            (Category.ISKONTO, self.iskonto_puanla(veri.get(InfoKey.ISKONTO, ""))),
            (Category.ACIKLIK, self.aciklik_puanla(veri.get(InfoKey.ACIKLIK, ""))),
            (Category.SATMAMA, self.satmama_puanla(veri.get(InfoKey.TAAHHUT, ""), ceza_sozlugu)),
            (Category.KURUMSALLIK, self.kurumsallik_puanla(raw_text)),
        ]

        toplam_kazanilan = 0.0
        toplam_max_possible = 0.0
        puan_detaylari, guclu, risk = [], [], []

        for kategori, res in hesaplamalar:
            toplam_kazanilan += res.score
            toplam_max_possible += res.max_possible
            puan_detaylari.append({
                "kategori": kategori,
                "puan": res.score,
                "max_puan": self.weights.MAX[kategori],
                "aciklama": res.explanation,
                "veri_bulundu": res.has_data
            })

        veri_guvenilirligi = int(toplam_max_possible)
        base_score = (toplam_kazanilan / toplam_max_possible * 100) if toplam_max_possible > 0 else 0

        bonuslar = 0
        rt_lower = raw_text.lower()

        if any(k in rt_lower for k in ["temettü ödemesi", "kâr payı dağıtıldı", "nakit temettü"]):
            bonuslar += 2
            guclu.append("[bonus] Geçmiş yıllara ait somut temettü ödeme kültürü. (+2.0 Puan)")

        ihracat_match = re.search(r'ihracat oranı %([2-9][0-9]|100)', rt_lower)
        if ihracat_match:
            bonuslar += 2
            guclu.append(f"[bonus] Güçlü döviz girdisi (İhracat oranı %{ihracat_match.group(1)}). (+2.0 Puan)")

        if any(k in rt_lower for k in ["ar-ge merkezi", "patent", "tübitak destekli"]):
            bonuslar += 1
            guclu.append("[bonus] Tescilli Ar-Ge / Patent çalışmaları mevcut. (+1.0 Puan)")

        ist = veri.get(InfoKey.FIYAT_ISTIKRARI, "").lower()
        istikrar_yok = "planlanmamaktadır" in ist or ist == "yok"
        if istikrar_yok:
            kirmizi_bayraklar.append("🚨 Fiyat istikrarı planlanmıyor.")
            ceza_sozlugu["istikrar_yok"] = 3

        toplam_ceza = sum(ceza_sozlugu.values())

        temel_kalite_skoru = base_score + bonuslar - toplam_ceza
        temel_kalite_skoru = round(max(0.0, min(100.0, temel_kalite_skoru)), 1)

        risk_skoru = 10.0 + toplam_ceza
        if veri_guvenilirligi < 70:
            risk_skoru += (70 - veri_guvenilirligi) * 0.5
        aciklik_val = TextUtils.yuzde_bul(veri.get(InfoKey.ACIKLIK, "")) or 25.0
        if aciklik_val > 40:
            risk_skoru += 15
        risk_skoru = round(max(0.0, min(100.0, risk_skoru)), 1)

        tavan_potansiyeli = 50.0
        fiyat = TextUtils.sayi_bul(veri.get(InfoKey.FIYAT, ""))
        pay = TextUtils.sayi_bul(veri.get(InfoKey.PAY_SAYISI, ""))

        arz_buyuklugu = (fiyat * pay) if (fiyat and pay) else (TextUtils.sayi_bul(veri.get(InfoKey.BUYUKLUK, "")) or 1_000_000_000)

        if arz_buyuklugu < 500_000_000:
            tavan_potansiyeli += 30
        elif arz_buyuklugu < 1_000_000_000:
            tavan_potansiyeli += 15
        elif arz_buyuklugu > 3_000_000_000:
            tavan_potansiyeli -= 15

        sektor = self._get_sektor(raw_text)
        if sektor in ["TEKNOLOJİ", "ENERJİ"]:
            tavan_potansiyeli += 10

        isk_val = TextUtils.yuzde_bul(veri.get(InfoKey.ISKONTO, "")) or 0.0
        if isk_val >= 25:
            tavan_potansiyeli += 10

        if istikrar_yok:
            tavan_potansiyeli -= 10
        if ceza_sozlugu.get("ortak_satis"):
            tavan_potansiyeli -= 15

        tavan_potansiyeli = round(max(0.0, min(100.0, tavan_potansiyeli)), 1)

        if tavan_potansiyeli > 75:
            volatilite = "Yüksek (Tavan Serisi / Agresif Hareket Potansiyeli)"
        elif risk_skoru > 60:
            volatilite = "Yüksek (Aşağı Yönlü Dalgalanma Riski)"
        elif tavan_potansiyeli < 40:
            volatilite = "Düşük (Ağır Tahta - Sınırlı Hareket)"
        else:
            volatilite = "Orta (Piyasa ve Sektör Koşullarına Bağlı)"

        for kategori, res in hesaplamalar:
            if not res.has_data:
                continue
            oran = res.score / res.max_possible if res.max_possible else 0
            if oran >= 0.8:
                guclu.append(f"[{kategori.value}] {res.explanation} (+{res.score:.1f} Puan)")
            elif oran <= 0.3:
                risk.append(f"[{kategori.value}] {res.explanation} (-{abs(res.max_possible - res.score):.1f} Puan)")

        kirmizi_bayraklar = list(set(kirmizi_bayraklar))

        return temel_kalite_skoru, tavan_potansiyeli, risk_skoru, guclu, risk, kirmizi_bayraklar, puan_detaylari, veri_guvenilirligi, volatilite


# ═══════════════════════════════════════════════════════════════════
# 🕸️ 5. VERİ ÇEKİCİ (GELİŞTİRİLMİŞ ESNEK SCRAPER)
# ═══════════════════════════════════════════════════════════════════

class DataExtractor:
    def __init__(self, analyzer: Optional[ScoreAnalyzer] = None):
        self.analyzer = analyzer or ScoreAnalyzer()
        self.session: Optional[AsyncSession] = None
        self._session_lock = asyncio.Lock()

        self.FIELD_LABELS: dict[InfoKey, list[str]] = {
            InfoKey.BIST_KODU: ["bist kodu"],
            InfoKey.TARIH: ["halka arz tarihi", "talep toplama tarihi", "tarih"],
            InfoKey.FIYAT: ["halka arz fiyatı", "fiyatı", "fiyat"],
            InfoKey.BUYUKLUK: ["halka arz büyüklüğü", "büyüklüğü"],
            InfoKey.ISLEM_TARIHI: ["işlem tarihi", "borsada işlem tarihi", "işlem tarihi"],
            InfoKey.ACIKLIK: ["halka açıklık", "halka açıklık oranı", "açıklık oranı"],
            InfoKey.ISKONTO: ["halka arz iskontosu", "iskonto oranı", "iskonto"],
            InfoKey.TAAHHUT: ["satmama taahhüdü", "taahhüt"],
            InfoKey.HALKA_ARZ_SEKLI: ["halka arz şekli", "arz şekli"],
            InfoKey.FON_KULLANIM: ["fonun kullanım yeri", "fon kullanım yeri", "fonun kullanımı"],
            InfoKey.SATIS_YONTEMI: ["halka arz satış yöntemi", "satış yöntemi"],
            InfoKey.FIYAT_ISTIKRARI: ["fiyat istikrarı"],
            InfoKey.PAY_SAYISI: [
                "pay", "halka arz edilecek pay", "dağıtılacak pay miktarı",
                "toplam pay miktarı", "toplam pay", "çıkarılmış sermaye", "ödenmiş sermaye", "lot"
            ],
            InfoKey.DAGITIM_YONTEMI: ["dağıtım yöntemi", "dağıtım şekli"],
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
                logger.warning(f"Bağlantı hatası ({url}), Durum Kodu: {res.status_code}")
            except Exception as e:
                logger.warning(f"Timeout/Hata ({url}): {e}")
            if i < SETTINGS.MAX_RETRY - 1:
                await asyncio.sleep(1 * (i + 1))
        return None

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
                if any(nl.startswith(e) and (len(nl) == len(e) or nl[len(e)] in (":", " ")) for e in etiketler) or nl in etiketler:
                    deger = self._satirdan_deger_al(lines, normalized_lines, i)
                    if deger:
                        veri[alan] = deger
                    break

    def _dagitim_tahsisat_finansal_doldur(self, veri: dict, raw_text: str):
        lines = raw_text.split("\n")
        lot_baslik = None
        if "Dağıtılan Pay Miktarı" in raw_text:
            lot_baslik = "Dağıtılan Pay Miktarı"
        elif "Dağıtılacak Pay Miktarı" in raw_text:
            lot_baslik = "Dağıtılacak Pay Miktarı"

        if lot_baslik:
            veri[InfoKey.DAGITIM_TIPI] = (
                "Dağıtılan Pay Miktarı (Kesin Sonuç)" if "Dağıtılan" in lot_baslik else "Tahmini Lot Dağıtımı"
            )
            try:
                bolunmus = raw_text.split(lot_baslik)[1]
                lot_lines = [
                    "• " + s.replace("-", "").strip()
                    for s in bolunmus.split("\n")[1:20]
                    if ("katılım" in s.lower() or "lot" in s.lower() or "kişi" in s.lower())
                    and len(s.replace("-", "").strip()) > 4
                ]
                if lot_lines:
                    veri[InfoKey.DAGITIM_TABLOSU] = "\n".join(lot_lines)
            except Exception as e:
                logger.debug(f"Lot Parsing Error: {e}")

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
                if TextUtils.etiket_eslesir(baslik_norm, etiketler):
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

    def _durum_belirle(self, tarih_metni: str, islem_tarihi_metni: str, raw_text: str, kart_metni: str) -> ArzDurumu:
        rt_lower = raw_text.lower()
        bugun = datetime.now().date()
        aylar = {
            "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
            "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12
        }

        if "işlem görmeye başlamıştır" in rt_lower or "gong!" in kart_metni.lower():
            return ArzDurumu.ISLEM_GORMEYE_BASLADI

        is_trh = str(islem_tarihi_metni).lower().strip()
        if is_trh and is_trh not in ("-", "açıklanmadı", "belli değil"):
            ay_str = next((ay for ay in aylar if ay in is_trh), None)
            sayilar = re.findall(r'\d+', is_trh)
            if ay_str and sayilar:
                try:
                    yil = next((int(s) for s in sayilar if len(s) == 4), bugun.year)
                    gun = next((int(s) for s in sayilar if 1 <= int(s) <= 31 and len(s) <= 2), 1)
                    islem_dt = date(yil, aylar[ay_str], gun)
                    if bugun >= islem_dt:
                        return ArzDurumu.ISLEM_GORMEYE_BASLADI
                except Exception as e:
                    logger.debug(f"Islem Tarihi Parse Hatasi: {e}")

        if "dağıtılan pay miktarı" in rt_lower or "kesinleşen" in rt_lower:
            return ArzDurumu.ISLEME_BEKLENIYOR

        t_metin = str(tarih_metni).lower().strip()
        if not t_metin or t_metin in ("-", "açıklanmadı", "belli değil"):
            return (
                ArzDurumu.HAZIRLANIYOR
                if "taslak" in kart_metni or "hazırlanıyor" in kart_metni
                else ArzDurumu.SPK_ONAYLI
            )

        ay_str = next((ay for ay in aylar if ay in t_metin), None)
        sayilar = re.findall(r'\d+', t_metin)

        if ay_str and len(sayilar) >= 2:
            try:
                yil = next((int(s) for s in sayilar if len(s) == 4), bugun.year)
                gunler = [int(s) for s in sayilar if len(s) < 4]
                if not gunler:
                    gunler = [1]
                bas_dt = date(yil, aylar[ay_str], gunler[0])
                bit_dt = date(yil, aylar[ay_str], max(gunler))
                if bugun < bas_dt:
                    return ArzDurumu.TALEP_YAKLASIYOR
                elif bas_dt <= bugun <= bit_dt:
                    return ArzDurumu.TALEP_TOPLANIYOR
                elif bugun > bit_dt:
                    return ArzDurumu.DAGITIM_BEKLENIYOR
            except Exception as e:
                logger.debug(f"Talep Tarihi Parse Hatasi: {e}")

        if "hazırlanıyor" in kart_metni or "taslak" in kart_metni:
            return ArzDurumu.HAZIRLANIYOR
        return ArzDurumu.SPK_ONAYLI

    async def _sirket_isle(self, sirket_adi: str, detay_linki: str, kart_metni: str, debug: bool) -> Optional[dict]:
        html = await self._fetch_url_with_retry(detay_linki)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        raw_text = soup.get_text(separator="\n", strip=True)

        veri = dict(self.DEFAULTS)
        self._tablodan_doldur(veri, soup)
        self._satirlardan_doldur(veri, raw_text)
        self._dagitim_tahsisat_finansal_doldur(veri, raw_text)
        fin = self._finansal_tablo_cikar(soup, raw_text)

        t1_t2 = "t1-t2 kullanılabilir" in raw_text.lower() or "t1 ve t2 kullanılabilir" in raw_text.lower()
        katilim = (
            "katılım endeksine uygun değildir" not in raw_text.lower()
            and "katılım endeksine uygun" in raw_text.lower()
        )
        islem_menusu = (
            "Hisse Alış/Satış Menüsü"
            if "borsada satış" in veri.get(InfoKey.DAGITIM_YONTEMI, "").lower()
            else "Halka Arz Menüsü"
        )

        if veri.get(InfoKey.TARIH) == self.DEFAULTS.get(InfoKey.TARIH):
            tarih_match = re.search(
                r"(\d{1,2}(?:-\d{1,2})*\s+(?:ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)\s+\d{4})",
                kart_metni, re.IGNORECASE
            )
            if tarih_match:
                veri[InfoKey.TARIH] = tarih_match.group(1).title()

        buyukluk_metni = str(veri.get(InfoKey.BUYUKLUK, ""))
        if "**" in buyukluk_metni:
            veri[InfoKey.BUYUKLUK] = buyukluk_metni.split("**")[0].strip()
        elif "Grafiği" in buyukluk_metni:
            veri[InfoKey.BUYUKLUK] = buyukluk_metni.split("Grafiği")[0].strip()

        durum = self._durum_belirle(
            veri.get(InfoKey.TARIH, ""),
            veri.get(InfoKey.ISLEM_TARIHI, ""),
            raw_text,
            kart_metni
        )

        t_kalite, t_potansiyel, t_risk, guclu, risk, kirmizi, detaylar, guven_skoru, volatilite = self.analyzer.skoru_topla(
            veri, fin, durum, raw_text
        )

        if t_kalite >= 85:
            rating = "A+ (★★★★★)"
        elif t_kalite >= 75:
            rating = "A (★★★★☆)"
        elif t_kalite >= 60:
            rating = "B (★★★☆☆)"
        elif t_kalite >= 45:
            rating = "C (★★☆☆☆)"
        elif t_kalite >= 25:
            rating = "D (★☆☆☆☆)"
        else:
            rating = "E (☆☆☆☆☆)"

        if durum == ArzDurumu.HAZIRLANIYOR:
            gorunum = Gorunum.HAZIRLIK
            degerlendirme = "Şirket taslak sürecinde olduğu için finansal değerlemesi yapılamamıştır."
            rating = "Hazırlanıyor"
        else:
            gorunum = Gorunum.COK_GUCLU if t_kalite >= 80 else Gorunum.DENGELI if t_kalite >= 45 else Gorunum.RISKLI
            deg_metin = f"Algoritmamız bu halka arzın temel yatırım kalitesini {t_kalite} puan ile değerlendirdi. Kısa vadede tavan serisi potansiyeli {t_potansiyel}/100, risk seviyesi ise {t_risk}/100 olarak ölçüldü. "
            if guven_skoru < 60:
                deg_metin += f"Ancak bu analiz, izahnamedeki eksik finansal veriler sebebiyle düşük veri güvenilirliği (%{guven_skoru}) ile oluşturulmuştur. "
            deg_metin += "Şirket katılım endeksine UYGUN." if katilim else "Şirket katılım endeksine UYGUN DEĞİL."
            degerlendirme = deg_metin

        result = {
            "sirket": sirket_adi,
            "bist_kodu": veri[InfoKey.BIST_KODU],
            "durum": durum,
            "islem_tarihi": veri[InfoKey.ISLEM_TARIHI],
            "skor": t_kalite,
            "temel_kalite_skoru": t_kalite,
            "tavan_potansiyeli_skoru": t_potansiyel,
            "risk_skoru": t_risk,
            "yildiz": rating,
            "genel_gorunum": gorunum,
            "genel_degerlendirme": degerlendirme,
            "guclu_yanlar": guclu,
            "riskler": risk,
            "kirmizi_bayraklar": kirmizi,
            "puan_detaylari": detaylar,
            "tarih": veri[InfoKey.TARIH],
            "fiyat": veri[InfoKey.FIYAT],
            "buyukluk": veri[InfoKey.BUYUKLUK],
            "aciklik": veri[InfoKey.ACIKLIK],
            "yorum_aciklik": "",
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
            "veri_guvenilirligi": guven_skoru,
            "tahmini_volatilite": volatilite,
        }

        if debug:
            result["debug_bilgisi"] = {
                "veri_ham": {k.value: v for k, v in veri.items()},
                "finansal_ham": {k.value: v for k, v in fin.items()},
                "default_kalanlar": [a.value for a in self.FIELD_LABELS if veri.get(a) == self.DEFAULTS.get(a)],
            }

        return result

    async def analiz_et(self, debug: bool = False) -> list[dict]:
        sirket_listesi: list[dict] = []
        gorulen_sirketler = set()

        html = await self._fetch_url_with_retry(SETTINGS.BASE_URL)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        meta_list = []

        for etiket in soup.find_all("h3"):
            link = etiket.find("a") or etiket.find_parent("a")
            if not link or "href" not in link.attrs:
                continue
            sirket_adi = etiket.get_text(strip=True)
            if sirket_adi in gorulen_sirketler or len(sirket_adi) <= 3:
                continue

            parent_li = etiket.find_parent("li")
            if parent_li:
                kart_metni = parent_li.get_text(strip=True).lower()
            else:
                kart_metni = sirket_adi.lower()

            # YALNIZCA YENİ VE GONG ( İLK İŞLEM GÜNÜ ) OLANLARI FİLTRELE
            if not any(b in kart_metni for b in ["yeni!", "gong!"]):
                continue

            gorulen_sirketler.add(sirket_adi)
            meta_list.append((sirket_adi, link["href"], kart_metni))

            if len(meta_list) >= SETTINGS.MAX_SIRKET:
                break

        semaphore = asyncio.Semaphore(SETTINGS.ESZAMANLI_ISTEK_LIMITI)

        async def _sinirli_isle(sirket_adi: str, href: str, kart_metni: str) -> Optional[dict]:
            async with semaphore:
                sonuc = await self._sirket_isle(sirket_adi, href, kart_metni, debug)
                await asyncio.sleep(SETTINGS.ISTEK_ARASI_BEKLEME)
                return sonuc

        tasks = [
            asyncio.create_task(_sinirli_isle(sirket_adi, href, kart_metni))
            for sirket_adi, href, kart_metni in meta_list
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.exception(f"Şirket işlenirken hata: {res}")
                continue
            if res:
                sirket_listesi.append(res)

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
    logger.info("Uygulama başlatıldı.")
    yield
    await extractor.close()
    logger.info("Uygulama kapatıldı.")


app = FastAPI(
    title="Halka Arz Asistanı Pro",
    description="Gelişmiş halka arz analiz ve puanlama API'si",
    version="2.0.1",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if SETTINGS.ALLOWED_ORIGINS == "*" else SETTINGS.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/halkarzlar")
async def get_halka_arzlar(
    debug: bool = Query(False, description="Debug modu (ham verileri döner)"),
    x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key")
):
    if debug and not check_debug_permission(x_debug_key):
        raise HTTPException(status_code=403, detail="Debug modu için geçerli API Key gerekli.")

    if not debug:
        cached = await CACHE.get()
        if cached:
            return {"halka_arzlar": cached, "uyari": YATIRIM_UYARISI}

    extractor: DataExtractor = app.state.extractor

    try:
        veriler = await extractor.analiz_et(debug=debug)
    except Exception as e:
        logger.exception(f"Analiz sırasında beklenmeyen hata: {e}")
        raise HTTPException(
            status_code=502,
            detail="Veri kaynağına şu anda ulaşılamıyor, lütfen daha sonra tekrar deneyin."
        )

    if veriler and not debug:
        await CACHE.set(veriler)

    return {"halka_arzlar": veriler, "uyari": YATIRIM_UYARISI}


@app.post("/api/cache/clear")
async def clear_cache(x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key")):
    if not check_debug_permission(x_debug_key):
        raise HTTPException(status_code=403, detail="Yetkisiz işlem.")
    await CACHE.invalidate()
    return {"detail": "Cache temizlendi."}


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": time.time()}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)