#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
İZAHNAME İŞLEME HATTI  (GitHub Actions'ta çalışır)
═══════════════════════════════════════════════════════════════════

NE YAPAR
    1. halkarz.com'daki güncel halka arzları listeler
    2. Her biri için izahname PDF linkini bulur
    3. PDF'i indirir, 300+ sayfanın içinden SADECE finansal tablo
       sayfalarını bulur (2-5 sayfa)
    4. O sayfaları görüntüye çevirip yapay zekaya gönderir
    5. Dönen JSON'u muhasebe kurallarıyla DOĞRULAR
    6. Doğrulamayı geçerse veri/finansallar/{slug}.json olarak kaydeder

NEDEN BÖYLE
    Render sunucusu artık PDF'e hiç dokunmuyor. Ne indirme, ne OCR,
    ne yapay zeka isteği. Sadece hazır JSON dosyasını okuyor.
    Bu sayede sunucu çökmüyor, Docker/tesseract gerekmiyor.

MALİYET
    Tüm PDF değil, yalnızca 2-5 sayfa gönderiliyor. Şirket başına
    bir kerelik işlem. İzahname yayınlandıktan sonra değişmediği için
    aynı şirket bir daha işlenmiyor.

KULLANIM
    python araclar/izahname_isle.py                 # yeni olanları işle
    python araclar/izahname_isle.py --zorla         # hepsini yeniden işle
    python araclar/izahname_isle.py --sirket quick  # tek şirket
    python araclar/izahname_isle.py --pdf dosya.pdf --slug test  # yerel PDF
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Proje kökünü import yoluna ekle (izahname.py'deki yardımcılar için)
KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s: %(message)s")
logger = logging.getLogger("izahname_isle")

CIKTI_DIZINI = KOK / "veri" / "finansallar"
HALKARZ_URL = "https://halkarz.com/"

# Yapay zeka ayarları
LLM_SAGLAYICI = os.environ.get("LLM_SAGLAYICI", "gemini")   # gemini | anthropic
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-3.5-flash")
LLM_ANAHTAR = os.environ.get("LLM_API_KEY", "")
# Tek istekte gönderilecek en fazla sayfa. Maliyeti sınırlar.
MAX_SAYFA_GONDER = int(os.environ.get("MAX_SAYFA_GONDER", "10"))
# Görüntü çözünürlüğü. 150 DPI okunabilir ve makul boyutta.
GORUNTU_DPI = int(os.environ.get("GORUNTU_DPI", "150"))


# ═══════════════════════════════════════════════════════════════════
# 1. FİNANSAL TABLO SAYFALARINI BULMA
# ═══════════════════════════════════════════════════════════════════

def sadelestir(s: Optional[str]) -> str:
    n = (s or "").replace("İ", "i").lower()
    for a, b in (("ı","i"),("ş","s"),("ğ","g"),("ü","u"),("ö","o"),("ç","c")):
        n = n.replace(a, b)
    return re.sub(r"\s+", " ", n).strip()


# Bir sayfanın finansal tablo olduğunu gösteren işaretler
TABLO_ISARETLERI = {
    "bilanco": ["finansal durum tablosu", "bilanco", "toplam varliklar",
                "toplam kaynaklar", "ozkaynaklar", "donen varliklar"],
    "gelir": ["kar veya zarar tablosu", "gelir tablosu", "hasilat",
              "brut kar", "esas faaliyet kari", "donem kari",
              "net faiz geliri", "yazilan prim", "teknik bolum dengesi"],
    "nakit": ["nakit akis tablosu", "isletme faaliyetlerinden",
              "yatirim faaliyetlerinden"],
}

_SAYI_KALIBI = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


def sayfa_puanla(metin: str) -> tuple[float, str]:
    """
    Bir sayfanın finansal tablo olma olasılığını puanlar.
    Dönüş: (puan, tablo_turu)

    Sadece başlığa bakmak yetmez — içindekiler sayfası da "bilanço"
    kelimesini içerir. Bu yüzden kalem yoğunluğu ve sayı sayısı da
    hesaba katılır.
    """
    d = sadelestir(metin)
    if not d:
        return 0.0, ""

    # İçindekiler sayfası eleme: nokta dizileri, az sayı
    sayi_adedi = len(_SAYI_KALIBI.findall(metin))
    if d.count("....") > 3:
        return 0.0, ""

    en_iyi_puan, en_iyi_tur = 0.0, ""
    for tur, isaretler in TABLO_ISARETLERI.items():
        eslesen = sum(1 for i in isaretler if i in d)
        if eslesen == 0:
            continue
        puan = eslesen * 2.0 + min(sayi_adedi / 8.0, 5.0)
        if sayi_adedi < 8:
            puan -= 4.0        # tablo görünümlü ama rakamsız => metin sayfası
        if puan > en_iyi_puan:
            en_iyi_puan, en_iyi_tur = puan, tur
    return en_iyi_puan, en_iyi_tur


def finansal_sayfalari_bul(pdf_yolu: str,
                           max_sayfa: int = MAX_SAYFA_GONDER) -> list[int]:
    """
    300+ sayfalık PDF'ten finansal tablo sayfalarını seçer.
    Taranmış PDF'lerde metin çıkmaz; o durumda konum tahminine düşer.
    """
    import warnings
    warnings.filterwarnings("ignore")
    import pdfplumber

    with pdfplumber.open(pdf_yolu) as pdf:
        toplam = len(pdf.pages)
        metinler = [(s.extract_text() or "") for s in pdf.pages]

    ortalama = sum(len(m) for m in metinler) / max(toplam, 1)

    if ortalama < 200:
        # ── Taranmış PDF: metin yok, konum tahmini ──
        # Finansal tablolar izahnamenin tipik olarak %48-58 aralığında.
        logger.info(
            f"PDF taranmış görünüyor ({ortalama:.0f} kr/sayfa); "
            f"konum tahminiyle sayfa seçiliyor."
        )
        merkez = int(toplam * 0.52)
        yaril = max_sayfa // 2
        return list(range(max(0, merkez - yaril),
                          min(toplam, merkez - yaril + max_sayfa)))

    # ── Metin tabanlı PDF: puanlayarak seç ──
    adaylar: list[tuple[float, int, str]] = []
    for i, m in enumerate(metinler):
        puan, tur = sayfa_puanla(m)
        if puan >= 6.0:
            adaylar.append((puan, i, tur))

    if not adaylar:
        logger.warning("Finansal tablo sayfası bulunamadı, konum tahmini kullanılıyor.")
        merkez = int(toplam * 0.52)
        return list(range(max(0, merkez - 2), min(toplam, merkez + 3)))

    # Her tablo türünden en iyi sayfayı al, sonra kalan kotayı doldur
    secilen: list[int] = []
    for tur in ("bilanco", "gelir", "nakit"):
        tur_adaylari = [a for a in adaylar if a[2] == tur]
        if tur_adaylari:
            secilen.append(max(tur_adaylari)[1])
    for puan, idx, _ in sorted(adaylar, reverse=True):
        if len(secilen) >= max_sayfa:
            break
        if idx not in secilen:
            secilen.append(idx)
    return sorted(secilen[:max_sayfa])


def sayfalari_goruntuye_cevir(pdf_yolu: str, sayfalar: list[int],
                              dpi: int = GORUNTU_DPI) -> list[bytes]:
    """Seçilen sayfaları PNG baytlarına çevirir (yapay zekaya göndermek için)."""
    import warnings
    warnings.filterwarnings("ignore")
    import pdfplumber

    goruntuler: list[bytes] = []
    with pdfplumber.open(pdf_yolu) as pdf:
        for i in sayfalar:
            if i >= len(pdf.pages):
                continue
            im = pdf.pages[i].to_image(resolution=dpi).original
            tampon = io.BytesIO()
            im.save(tampon, format="PNG", optimize=True)
            goruntuler.append(tampon.getvalue())
    return goruntuler


# ═══════════════════════════════════════════════════════════════════
# 2. YAPAY ZEKA İLE OKUMA
# ═══════════════════════════════════════════════════════════════════

ISTENEN_ALANLAR = [
    "Hasilat", "BrutKar", "FaaliyetKari", "NetKar",
    "Ozkaynak", "ToplamVarlik", "ToplamKaynak", "ToplamBorc",
    "DonenVarlik", "DuranVarlik",
    "KisaVadeliYukumluluk", "UzunVadeliYukumluluk",
    "FinansalBorc", "Nakit", "IsletmeNakitAkisi",
    "FinansmanGideri", "Amortisman", "OdenmisSermaye",
]

ISTEM = """Bu görüntüler bir halka arz izahnamesindeki finansal tablolardır.

GÖREV: Tablolardaki rakamları JSON olarak çıkar.

KURALLAR:
1. SADECE JSON döndür. Açıklama, markdown, ``` işareti KULLANMA.
2. Rakamları GÖRDÜĞÜN GİBİ yaz. Hesaplama YAPMA, tahmin ETME.
3. Bir kalemi tabloda göremiyorsan o alanı HİÇ EKLEME. Uydurma.
4. Tablonun başlığında "Bin TL" / "(000)" yazıyorsa "olcek": 1000,
   "Milyon TL" yazıyorsa 1000000, aksi halde 1 yaz.
5. Parantez içindeki değerler NEGATİFTİR: (1.234) -> -1234
6. Dönemleri "YYYY-MM" biçiminde yaz (31.12.2025 -> "2025-12").
7. Rakamları binlik ayraç OLMADAN, düz sayı olarak yaz.

İSTENEN ALANLAR (bulabildiklerini ekle):
%s

ÇIKTI BİÇİMİ:
{
  "olcek": 1,
  "donemler": ["2023-12", "2024-12", "2025-12"],
  "kalemler": {
    "Hasilat": {"2023-12": 2000000000, "2024-12": 3500000000},
    "NetKar": {"2024-12": 650000000}
  }
}
""" % "\n".join(f"- {a}" for a in ISTENEN_ALANLAR)


def llm_cagir(goruntuler: list[bytes]) -> Optional[dict]:
    """
    Görüntüleri yapay zekaya gönderip JSON alır.
    Sağlayıcı LLM_SAGLAYICI ile seçilir.
    """
    if not LLM_ANAHTAR:
        logger.error("LLM_API_KEY tanımlı değil.")
        return None
    if LLM_SAGLAYICI == "gemini":
        return _gemini_cagir(goruntuler)
    if LLM_SAGLAYICI == "anthropic":
        return _anthropic_cagir(goruntuler)
    logger.error(f"Bilinmeyen sağlayıcı: {LLM_SAGLAYICI}")
    return None


def _json_ayikla(ham: str) -> Optional[dict]:
    """
    Model bazen ```json ... ``` sarmalıyla döner. Temizler ve parse eder.
    """
    if not ham:
        return None
    s = ham.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    ilk, son = s.find("{"), s.rfind("}")
    if ilk == -1 or son == -1:
        return None
    try:
        return json.loads(s[ilk:son + 1])
    except json.JSONDecodeError as e:
        logger.warning(f"JSON ayrıştırılamadı: {e}")
        return None


def _gemini_cagir(goruntuler: list[bytes]) -> Optional[dict]:
    import urllib.request

    parcalar: list[dict] = [{"text": ISTEM}]
    for g in goruntuler:
        parcalar.append({
            "inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(g).decode("ascii"),
            }
        })
    govde = json.dumps({
        "contents": [{"parts": parcalar}],
        # Sıcaklık 0: finansal veride yaratıcılık istemiyoruz
        "generationConfig": {"temperature": 0, "maxOutputTokens": 4096},
    }).encode("utf-8")

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{LLM_MODEL}:generateContent?key={LLM_ANAHTAR}")
    istek = urllib.request.Request(
        url, data=govde, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(istek, timeout=180) as yanit:
            veri = json.loads(yanit.read().decode("utf-8"))
        metin = veri["candidates"][0]["content"]["parts"][0]["text"]
        return _json_ayikla(metin)
    except Exception as e:
        logger.error(f"Gemini isteği başarısız: {e}")
        return None


def _anthropic_cagir(goruntuler: list[bytes]) -> Optional[dict]:
    import urllib.request

    icerik: list[dict] = []
    for g in goruntuler:
        icerik.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(g).decode("ascii")},
        })
    icerik.append({"type": "text", "text": ISTEM})

    govde = json.dumps({
        "model": LLM_MODEL,
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [{"role": "user", "content": icerik}],
    }).encode("utf-8")

    istek = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=govde,
        headers={"Content-Type": "application/json",
                 "x-api-key": LLM_ANAHTAR,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(istek, timeout=180) as yanit:
            veri = json.loads(yanit.read().decode("utf-8"))
        metin = "".join(p.get("text", "") for p in veri.get("content", []))
        return _json_ayikla(metin)
    except Exception as e:
        logger.error(f"Anthropic isteği başarısız: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# 3. DOĞRULAMA  (EN KRİTİK KATMAN)
# ═══════════════════════════════════════════════════════════════════

def dogrula(kalemler: dict[str, float]) -> dict[str, bool]:
    """
    Yapay zekanın döndürdüğü rakamları muhasebe kimlikleriyle kontrol eder.

    Bu katman ZORUNLUDUR. Dil modelleri rakam uydurabilir ve uydurulmuş
    bir bilanço ekranda gayet ikna edici görünür. Bilanço denkliği gibi
    kurallar matematiksel olarak tutmak zorunda olduğu için, uydurma
    veri buradan geçemez.
    """
    d: dict[str, bool] = {}
    g = kalemler

    tv, tk = g.get("ToplamVarlik"), g.get("ToplamKaynak")
    if tv and tk:
        d["bilanco_denkligi"] = abs(tv - tk) / max(abs(tv), 1) < 0.01

    dv, dur = g.get("DonenVarlik"), g.get("DuranVarlik")
    if tv and dv and dur:
        d["varlik_toplami"] = abs((dv + dur) - tv) / max(abs(tv), 1) < 0.02

    tb, ozk = g.get("ToplamBorc"), g.get("Ozkaynak")
    if tk and tb and ozk:
        d["kaynak_toplami"] = abs((tb + ozk) - tk) / max(abs(tk), 1) < 0.02

    kv, uv = g.get("KisaVadeliYukumluluk"), g.get("UzunVadeliYukumluluk")
    if tb and kv and uv:
        d["yukumluluk_toplami"] = abs((kv + uv) - tb) / max(abs(tb), 1) < 0.02

    bk, has = g.get("BrutKar"), g.get("Hasilat")
    if bk and has and has > 0:
        d["brut_kar_makul"] = bk <= has * 1.01

    nk, fk = g.get("NetKar"), g.get("FaaliyetKari")
    if nk and fk and fk > 0:
        d["kar_tutarliligi"] = nk <= fk * 5

    if ozk and tv:
        d["ozkaynak_makul"] = abs(ozk) <= abs(tv) * 1.01

    return d


def guvenilir_mi(kalemler: dict, dogrulamalar: dict) -> tuple[bool, str]:
    """
    Veri kullanılabilir mi?
    - Hiçbir doğrulama başarısız olmamalı
    - En az temel kalemler bulunmuş olmalı
    - En az bir doğrulama gerçekten YAPILMIŞ olmalı (hiç kontrol
      edilememiş veri "geçti" sayılmaz)
    """
    basarisiz = [k for k, v in dogrulamalar.items() if v is False]
    if basarisiz:
        return False, "Tutarlılık kontrolü başarısız: " + ", ".join(basarisiz)
    if not dogrulamalar:
        return False, "Hiçbir tutarlılık kontrolü yapılamadı (yetersiz kalem)."
    if "NetKar" not in kalemler or "Ozkaynak" not in kalemler:
        return False, "Temel kalemler (Net Kâr / Özkaynak) bulunamadı."
    return True, ""


# ═══════════════════════════════════════════════════════════════════
# 4. SONUÇ BİÇİMİ
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FinansalSonuc:
    slug: str
    sirket_adi: str = ""
    izahname_url: str = ""
    kaynak: str = "izahname-pdf+llm"
    model: str = ""
    islenme_zamani: str = ""
    olcek: float = 1.0
    donemler: list = field(default_factory=list)
    guncel: dict = field(default_factory=dict)
    seriler: dict = field(default_factory=dict)
    dogrulama: dict = field(default_factory=dict)
    guvenilir: bool = False
    not_: str = ""
    islenen_sayfalar: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "sirket_adi": self.sirket_adi,
            "izahname_url": self.izahname_url, "kaynak": self.kaynak,
            "model": self.model, "islenme_zamani": self.islenme_zamani,
            "olcek": self.olcek, "donemler": self.donemler,
            "guncel": self.guncel, "seriler": self.seriler,
            "dogrulama": self.dogrulama, "guvenilir": self.guvenilir,
            "not": self.not_, "islenen_sayfalar": self.islenen_sayfalar,
        }


def llm_ciktisini_isle(ham: dict, slug: str) -> FinansalSonuc:
    """
    Yapay zekanın JSON'unu standart biçime çevirir, ölçek uygular,
    doğrular.
    """
    sonuc = FinansalSonuc(slug=slug, model=f"{LLM_SAGLAYICI}/{LLM_MODEL}",
                          islenme_zamani=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                       time.gmtime()))
    try:
        olcek = float(ham.get("olcek", 1) or 1)
    except (TypeError, ValueError):
        olcek = 1.0
    sonuc.olcek = olcek

    kalemler = ham.get("kalemler") or {}
    seriler: dict[str, dict[str, float]] = {}
    for alan, donem_deger in kalemler.items():
        if alan not in ISTENEN_ALANLAR or not isinstance(donem_deger, dict):
            continue
        temiz: dict[str, float] = {}
        for donem, deger in donem_deger.items():
            d = str(donem).strip()
            if not re.fullmatch(r"20\d{2}-\d{2}", d):
                continue
            try:
                temiz[d] = float(deger) * olcek
            except (TypeError, ValueError):
                continue
        if temiz:
            seriler[alan] = temiz

    sonuc.seriler = seriler
    tum_donemler = sorted({d for s in seriler.values() for d in s})
    sonuc.donemler = tum_donemler

    if tum_donemler:
        son = tum_donemler[-1]
        sonuc.guncel = {a: s[son] for a, s in seriler.items() if son in s}

    sonuc.dogrulama = dogrula(sonuc.guncel)
    sonuc.guvenilir, sonuc.not_ = guvenilir_mi(sonuc.guncel, sonuc.dogrulama)
    return sonuc


# ═══════════════════════════════════════════════════════════════════
# 5. ANA AKIŞ
# ═══════════════════════════════════════════════════════════════════

def slug_uret(ad: str) -> str:
    n = (ad or "").replace("İ", "i").lower()
    for a, b in (("ı","i"),("ş","s"),("ğ","g"),("ü","u"),("ö","o"),("ç","c")):
        n = n.replace(a, b)
    n = re.sub(r"[^\w]+", "-", n).strip("-")
    return n[:60] or "bilinmeyen"


def halkarz_listesi_al() -> list[dict]:
    """halkarz.com ana sayfasından şirket adı + detay linki çıkarır."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests as cr

    r = cr.get(HALKARZ_URL, impersonate="chrome", timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    sirketler: list[dict] = []
    gorulen: set[str] = set()
    for h3 in soup.find_all("h3"):
        a = h3.find("a") or h3.find_parent("a")
        if not a or not a.get("href"):
            continue
        ad = h3.get_text(strip=True)
        if not ad or ad in gorulen or len(ad) <= 3:
            continue
        gorulen.add(ad)
        sirketler.append({"ad": ad, "url": a["href"]})
    return sirketler


def izahname_linki_al(detay_url: str) -> Optional[str]:
    """Detay sayfasının 'Ekler' bölümünden SPK onaylı izahname linki."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests as cr
    sys.path.insert(0, str(KOK))
    from izahname_servis import izahname_linki_bul

    r = cr.get(detay_url, impersonate="chrome", timeout=30)
    return izahname_linki_bul(BeautifulSoup(r.text, "html.parser"))


def pdf_indir(url: str, hedef: Path) -> bool:
    from curl_cffi import requests as cr
    try:
        r = cr.get(url, impersonate="chrome", timeout=180)
        if r.status_code != 200 or len(r.content) < 10_000:
            logger.error(f"PDF indirilemedi ({r.status_code}): {url}")
            return False
        hedef.write_bytes(r.content)
        logger.info(f"PDF indirildi: {len(r.content)/1e6:.1f} MB")
        return True
    except Exception as e:
        logger.error(f"PDF indirme hatası: {e}")
        return False


def pdf_isle(pdf_yolu: str, slug: str, sirket_adi: str = "",
             izahname_url: str = "") -> FinansalSonuc:
    """Tek bir PDF'i baştan sona işler."""
    sayfalar = finansal_sayfalari_bul(pdf_yolu)
    logger.info(f"Seçilen sayfalar (1-tabanlı): {[s+1 for s in sayfalar]}")

    goruntuler = sayfalari_goruntuye_cevir(pdf_yolu, sayfalar)
    toplam_mb = sum(len(g) for g in goruntuler) / 1e6
    logger.info(f"{len(goruntuler)} görüntü hazırlandı ({toplam_mb:.1f} MB)")

    ham = llm_cagir(goruntuler)
    if ham is None:
        s = FinansalSonuc(slug=slug, sirket_adi=sirket_adi,
                          izahname_url=izahname_url)
        s.not_ = "Yapay zeka yanıtı alınamadı."
        return s

    sonuc = llm_ciktisini_isle(ham, slug)
    sonuc.sirket_adi = sirket_adi
    sonuc.izahname_url = izahname_url
    sonuc.islenen_sayfalar = [s + 1 for s in sayfalar]
    return sonuc


def kaydet(sonuc: FinansalSonuc) -> Path:
    CIKTI_DIZINI.mkdir(parents=True, exist_ok=True)
    yol = CIKTI_DIZINI / f"{sonuc.slug}.json"
    yol.write_text(json.dumps(sonuc.to_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return yol


def main() -> int:
    ap = argparse.ArgumentParser(description="İzahname finansal tablo çıkarıcı")
    ap.add_argument("--zorla", action="store_true",
                    help="Daha önce işlenmiş şirketleri yeniden işle")
    ap.add_argument("--sirket", help="Sadece adı bunu içeren şirketi işle")
    ap.add_argument("--pdf", help="Yerel PDF dosyası (test için)")
    ap.add_argument("--slug", help="--pdf ile kullanılacak dosya adı")
    ap.add_argument("--limit", type=int, default=5,
                    help="Bir çalıştırmada en fazla kaç şirket işlensin")
    args = ap.parse_args()

    CIKTI_DIZINI.mkdir(parents=True, exist_ok=True)

    # ── Yerel PDF modu (test) ──
    if args.pdf:
        slug = args.slug or slug_uret(Path(args.pdf).stem)
        sonuc = pdf_isle(args.pdf, slug)
        yol = kaydet(sonuc)
        logger.info(f"{'✓ GÜVENİLİR' if sonuc.guvenilir else '✗ GÜVENİLMEZ'} -> {yol}")
        if not sonuc.guvenilir:
            logger.warning(sonuc.not_)
        return 0

    # ── Normal mod: halkarz.com'u tara ──
    sirketler = halkarz_listesi_al()
    logger.info(f"{len(sirketler)} şirket bulundu.")

    islenen = 0
    for s in sirketler:
        if islenen >= args.limit:
            logger.info(f"Limit ({args.limit}) doldu, duruluyor.")
            break
        if args.sirket and sadelestir(args.sirket) not in sadelestir(s["ad"]):
            continue

        slug = slug_uret(s["ad"])
        hedef = CIKTI_DIZINI / f"{slug}.json"
        if hedef.exists() and not args.zorla:
            mevcut = json.loads(hedef.read_text(encoding="utf-8"))
            if mevcut.get("guvenilir"):
                logger.info(f"Atlandı (zaten var): {s['ad']}")
                continue

        logger.info(f"── {s['ad']} ──")
        url = izahname_linki_al(s["url"])
        if not url:
            logger.info("İzahname linki bulunamadı, atlanıyor.")
            continue

        gecici = Path(f"/tmp/{slug}.pdf")
        if not pdf_indir(url, gecici):
            continue
        try:
            sonuc = pdf_isle(str(gecici), slug, s["ad"], url)
            yol = kaydet(sonuc)
            durum = "✓ GÜVENİLİR" if sonuc.guvenilir else "✗ GÜVENİLMEZ"
            logger.info(f"{durum} ({len(sonuc.guncel)} kalem) -> {yol.name}")
            if not sonuc.guvenilir:
                logger.warning(f"  Sebep: {sonuc.not_}")
            islenen += 1
        finally:
            gecici.unlink(missing_ok=True)

    logger.info(f"Bitti. {islenen} şirket işlendi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())