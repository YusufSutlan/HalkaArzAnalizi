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

def _proje_kokunu_bul() -> Path:
    """
    Depo kökünü güvenilir şekilde bulur.

    DÜZELTME: Önceki sürüm `parent.parent` kullanıyordu ve betiğin
    araclar/ klasöründe olduğunu varsayıyordu. Betik depo köküne
    konulunca JSON dosyaları DEPONUN DIŞINA yazılıyor, git add da
    "pathspec did not match any files" hatası veriyordu.

    Artık .git klasörü yukarı doğru aranıyor; betik nereye konulursa
    konulsun doğru çalışır.
    """
    ortam = os.environ.get("PROJE_KOK")
    if ortam:
        return Path(ortam).resolve()
    burasi = Path(__file__).resolve().parent
    for aday in (burasi, *burasi.parents):
        if (aday / ".git").exists():
            return aday
    # .git yoksa (ör. zip olarak indirilmişse) çalışma dizinini kullan
    return Path.cwd().resolve()


KOK = _proje_kokunu_bul()
sys.path.insert(0, str(KOK))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s: %(message)s")
logger = logging.getLogger("izahname_isle")

CIKTI_DIZINI = KOK / "veri" / "finansallar"
HALKARZ_URL = "https://halkarz.com/"

# Yapay zeka ayarları
LLM_SAGLAYICI = os.environ.get("LLM_SAGLAYICI", "gemini")   # gemini | anthropic
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-flash-latest")
LLM_ANAHTAR = os.environ.get("LLM_API_KEY", "")
# Tek istekte gönderilecek en fazla sayfa. Maliyeti sınırlar.
MAX_SAYFA_GONDER = int(os.environ.get("MAX_SAYFA_GONDER", "5"))
# Görüntü çözünürlüğü. 150 DPI okunabilir ve makul boyutta.
GORUNTU_DPI = int(os.environ.get("GORUNTU_DPI", "150"))
# JPEG kalitesi: 80 taranmış belgede rakamları net tutar, boyutu
# PNG'ye göre ~5-10 kat küçültür.
JPEG_KALITE = int(os.environ.get("JPEG_KALITE", "80"))


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
                              dpi: int = GORUNTU_DPI
                              ) -> tuple[list[bytes], bool]:
    """
    Seçilen sayfaları görüntüye çevirir.

    FORMAT SEÇİMİ: Tüm sayfalar hem PNG hem JPEG olarak kodlanır ve
    TOPLAMI küçük olan format seçilir. Tek tek seçmek yanlış olur —
    istekteki her görüntünün MIME tipi doğru bildirilmek zorunda,
    karışık format göndermek API hatasına yol açar.

    Neden ölçüyoruz: Taranmış belge sayfaları çoğunlukla beyaz zeminde
    siyah metin olduğu için PNG genelde küçük çıkıyor (ölçtüm: bir
    izahname sayfasında PNG 0,30 MB, JPEG 0,46 MB). Ama gri tonlamalı
    veya gürültülü taramalarda tersi oluyor.

    BOYUT KORUMASI: Toplam base64 sonrası 15 MB'ı aşarsa çözünürlük
    otomatik düşürülür. Loglarda 8 sayfanın 11,7 MB tuttuğu görülmüştü;
    base64 ile ~16 MB'a çıkıp istek reddediliyordu.

    Dönüş: (görüntüler, jpeg_mi)
    """
    import warnings
    warnings.filterwarnings("ignore")
    import pdfplumber

    for deneme_dpi in (dpi, 110, 85):
        pngler: list[bytes] = []
        jpegler: list[bytes] = []
        with pdfplumber.open(pdf_yolu) as pdf:
            for i in sayfalar:
                if i >= len(pdf.pages):
                    continue
                try:
                    im = pdf.pages[i].to_image(resolution=deneme_dpi).original
                except Exception as e:
                    logger.debug(f"Sayfa {i+1} çevrilemedi: {e}")
                    continue
                if im.mode != "RGB":
                    im = im.convert("RGB")
                p = io.BytesIO()
                im.save(p, format="PNG", optimize=True)
                pngler.append(p.getvalue())
                j = io.BytesIO()
                im.save(j, format="JPEG", quality=JPEG_KALITE, optimize=True)
                jpegler.append(j.getvalue())

        if not pngler:
            continue

        png_toplam = sum(len(g) for g in pngler)
        jpeg_toplam = sum(len(g) for g in jpegler)
        jpeg_mi = jpeg_toplam < png_toplam
        secilen = jpegler if jpeg_mi else pngler
        toplam = jpeg_toplam if jpeg_mi else png_toplam

        if toplam * 1.37 < 15 * 1024 * 1024 or deneme_dpi == 85:
            if deneme_dpi != dpi:
                logger.info(
                    f"  Boyut nedeniyle çözünürlük {deneme_dpi} DPI'a düşürüldü"
                )
            return secilen, jpeg_mi
    return [], False


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


def llm_cagir(goruntuler: list[bytes], istem: Optional[str] = None,
              jpeg: bool = False) -> Optional[dict]:
    """
    Görüntüleri yapay zekaya gönderip JSON alır.
    istem verilmezse varsayılan finansal tablo istemi kullanılır.
    """
    if not LLM_ANAHTAR:
        logger.error("LLM_API_KEY tanımlı değil.")
        return None
    if LLM_SAGLAYICI == "gemini":
        return _gemini_cagir(goruntuler, istem=istem, jpeg=jpeg)
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
    ilk = s.find("{")
    if ilk == -1:
        return None
    son = s.rfind("}")
    if son > ilk:
        try:
            return json.loads(s[ilk:son + 1])
        except json.JSONDecodeError:
            pass

    # ── Yarıda kesilmiş JSON onarımı ──
    # Model token sınırına takılırsa JSON ortasından kesilir. Bu durumda
    # her şeyi kaybetmek yerine, tamamlanmış kısmı kurtarmayı deneriz:
    # son geçerli virgülden sonrasını atıp açık parantezleri kapatırız.
    govde = s[ilk:]
    for kesim in range(len(govde), ilk, -1):
        parca = govde[:kesim].rstrip().rstrip(",")
        acik_kume = parca.count("{") - parca.count("}")
        acik_dizi = parca.count("[") - parca.count("]")
        if acik_kume < 0 or acik_dizi < 0:
            continue
        aday = parca + "]" * acik_dizi + "}" * acik_kume
        try:
            sonuc = json.loads(aday)
            logger.warning(
                "JSON yarıda kesilmişti; tamamlanan kısım kurtarıldı."
            )
            return sonuc
        except json.JSONDecodeError:
            continue
    return None


def _http_hata_detayi(e) -> str:
    """
    urllib'in HTTPError'u varsayılan olarak sadece "HTTP Error 404" der.
    Asıl sebep yanıt gövdesinde yazar; onu okumak sorunu anında çözer.
    """
    try:
        govde = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(govde)
            hata = j.get("error", {})
            mesaj = hata.get("message", "")
            # "Request contains an invalid argument" tek başına işe
            # yaramaz; hangi alanın sorunlu olduğu details içindedir.
            detaylar = hata.get("details") or []
            ek = []
            for d in detaylar:
                for anahtar in ("fieldViolations", "violations"):
                    for v in (d.get(anahtar) or []):
                        alan = v.get("field", "")
                        aciklama = v.get("description", "")
                        ek.append(f"{alan}: {aciklama}".strip(": "))
                if d.get("reason"):
                    ek.append(str(d["reason"]))
            if ek:
                return f"{mesaj} | {' ; '.join(ek[:4])}"
            if mesaj:
                return mesaj
        except Exception:
            pass
        return govde[:600]
    except Exception:
        return str(e)


def gemini_modelleri_listele() -> list[str]:
    """
    API anahtarının ERİŞEBİLDİĞİ modelleri sorar.

    404 hatasının sebebi neredeyse her zaman budur: model adı doğru
    görünse bile o anahtar/proje için mevcut olmayabilir. Model adı
    tahmin etmek yerine kaynağa sormak gerekir.
    """
    import urllib.request
    url = (f"https://generativelanguage.googleapis.com/v1beta/models"
           f"?key={LLM_ANAHTAR}&pageSize=200")
    try:
        with urllib.request.urlopen(url, timeout=60) as y:
            veri = json.loads(y.read().decode("utf-8"))
    except Exception as e:
        detay = _http_hata_detayi(e) if hasattr(e, "read") else str(e)
        logger.error(f"Model listesi alınamadı: {detay}")
        return []

    uygun: list[str] = []
    for m in veri.get("models", []):
        ad = m.get("name", "").replace("models/", "")
        yontemler = m.get("supportedGenerationMethods", [])
        if "generateContent" in yontemler:
            uygun.append(ad)
    return uygun


def gemini_model_sec() -> Optional[str]:
    """
    Ayarlanan model çalışmıyorsa, erişilebilir modeller arasından
    görüntü okuyabilecek bir tane seçer.
    """
    modeller = gemini_modelleri_listele()
    if not modeller:
        return None
    if LLM_MODEL in modeller:
        return LLM_MODEL
    # Tercih sırası: flash (ucuz ve hızlı) -> pro -> kalan her şey.
    # Görüntü desteği olmayan (embedding vb.) modeller elenir.
    def puan(ad: str) -> tuple:
        a = ad.lower()
        if "embedding" in a or "aqa" in a or "imagen" in a or "veo" in a:
            return (-1, ad)
        p = 0
        if "flash" in a: p += 3
        if "pro" in a: p += 2
        if "latest" in a: p += 1
        if "lite" in a: p -= 1
        return (p, ad)
    siralanmis = sorted((m for m in modeller if puan(m)[0] >= 0),
                        key=puan, reverse=True)
    return siralanmis[0] if siralanmis else None


def _gemini_cagir(goruntuler: list[bytes], model: Optional[str] = None,
                  istem: Optional[str] = None, jpeg: bool = False) -> Optional[dict]:
    import urllib.request

    parcalar: list[dict] = [{"text": istem or ISTEM}]
    mim = "image/jpeg" if jpeg else "image/png"
    for g in goruntuler:
        parcalar.append({
            "inline_data": {
                "mime_type": mim,
                "data": base64.b64encode(g).decode("ascii"),
            }
        })
    # KADEMELİ CONFIG (400 "invalid argument" çözümü)
    #
    # Gemini modelleri farklı generationConfig alanlarını destekliyor.
    # thinkingConfig yalnızca bazı sürümlerde geçerli; desteklemeyen
    # model 400 "Request contains an invalid argument" döndürüyor ve
    # İSTEK HİÇ ÇALIŞMIYOR.
    #
    # Hangi alanın sorunlu olduğunu tahmin edip hepsini silmek yerine,
    # en zengin ayardan en sadeye doğru sırayla deneniyor. Böylece
    # destekleyen modellerde JSON modunun avantajı korunuyor,
    # desteklemeyende otomatik olarak sadeye düşülüyor.
    # DÜZELTME 1: "JSON ayrıştırılamadı: Expecting ',' delimiter" hatası
    # yanıtın YARIDA KESİLMESİNDEN kaynaklanıyordu (253 karakterde bitmiş).
    # İki sebebi var:
    #   a) maxOutputTokens düşüktü
    #   b) yeni Gemini modelleri "düşünme" (thinking) yapıyor ve bu
    #      tokenler çıktı bütçesinden yeniyor; geriye JSON için yer
    #      kalmayınca yanıt ortasından kesiliyor
    #
    # DÜZELTME 2: responseMimeType ile modelden GEÇERLİ JSON dönmesi
    # garanti altına alınıyor. Böylece ``` sarmalı, önsöz, yarım küme
    # parantezi gibi ayrıştırma sorunları tamamen ortadan kalkıyor.
    # SIRALAMA NOTU: Canlı loglarda thinkingConfig'in reddedildiği,
    # responseMimeType'ın kabul edildiği görüldü. Bu yüzden çalışan
    # kademe başa alındı — aksi halde HER istekte bir çağrı boşa gidiyor
    # (2 şirket = 6 gereksiz istek). thinkingConfig'i destekleyen bir
    # modele geçilirse diye en sona yedek olarak bırakıldı.
    kademeler: list[dict] = [
        # 1) JSON modu (canlıda çalıştığı doğrulandı)
        {"temperature": 0, "maxOutputTokens": 16384,
         "responseMimeType": "application/json"},
        # 2) Sade: her modelde çalışır
        {"temperature": 0, "maxOutputTokens": 8192},
        # 3) Düşünme kapalı (destekleyen modeller için)
        {"temperature": 0, "maxOutputTokens": 16384,
         "responseMimeType": "application/json",
         "thinkingConfig": {"thinkingBudget": 0}},
    ]

    # Boyut koruması: base64 kodlama veriyi ~%33 şişirir. Çok büyük
    # istek de 400 döndürebilir.
    ham_boyut = sum(len(g) for g in goruntuler)
    if ham_boyut * 1.37 > 18 * 1024 * 1024:
        logger.warning(
            f"İstek çok büyük ({ham_boyut/1e6:.1f} MB ham). "
            f"Daha az sayfa gönderin (MAX_SAYFA_GONDER / TARANMIS_PENCERE)."
        )

    kullanilan = model or LLM_MODEL
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{kullanilan}:generateContent?key={LLM_ANAHTAR}")

    veri = None
    son_hata = ""
    son_kod = None
    for i, ayar in enumerate(kademeler, start=1):
        govde = json.dumps({
            "contents": [{"parts": parcalar}],
            "generationConfig": ayar,
        }).encode("utf-8")
        istek = urllib.request.Request(
            url, data=govde, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(istek, timeout=300) as yanit:
                veri = json.loads(yanit.read().decode("utf-8"))
            if i > 1:
                logger.info(f"  (config kademesi {i} ile başarılı)")
            break
        except Exception as e:
            son_kod = getattr(e, "code", None)
            son_hata = _http_hata_detayi(e) if hasattr(e, "read") else str(e)
            if son_kod == 400 and i < len(kademeler):
                # Config bu modelde desteklenmiyor; bir alt kademeye in
                logger.info(
                    f"  Config kademesi {i} reddedildi ({son_hata[:90]}), "
                    f"kademe {i+1} deneniyor."
                )
                continue
            break

    if veri is None:
        detay = son_hata
        kod = son_kod
        # 404 = model bu anahtar için yok. Otomatik olarak erişilebilir
        # bir model seç ve BİR KEZ yeniden dene.
        if kod == 404 and model is None:
            logger.warning(f"Model '{kullanilan}' bulunamadı. Detay: {detay}")
            logger.info("Anahtarınızın erişebildiği modeller sorgulanıyor...")
            yedek = gemini_model_sec()
            if yedek and yedek != kullanilan:
                logger.info(f"Yedek model deneniyor: {yedek}")
                return _gemini_cagir(goruntuler, model=yedek,
                                     istem=istem, jpeg=jpeg)
            logger.error(
                "Erişilebilir bir model bulunamadı. "
                "'python araclar/izahname_isle.py --modelleri-listele' "
                "komutuyla listeyi görebilirsiniz."
            )
            return None

        logger.error(f"Gemini isteği başarısız ({kod}): {detay}")
        return None

    try:
        adaylar = veri.get("candidates") or []
        if not adaylar:
            # Güvenlik filtresi veya boş yanıt
            geri = veri.get("promptFeedback", {})
            logger.error(f"Model yanıt vermedi. promptFeedback: {geri}")
            return None
        aday = adaylar[0]
        bitis = aday.get("finishReason")
        parcalar = aday.get("content", {}).get("parts", [])
        metin = "".join(p.get("text", "") for p in parcalar)

        if bitis == "MAX_TOKENS":
            logger.warning(
                "Yanıt token sınırına takıldı (MAX_TOKENS). Daha az sayfa "
                "göndermeyi deneyin (MAX_SAYFA_GONDER)."
            )
        if not metin:
            kullanim = veri.get("usageMetadata", {})
            logger.error(f"Yanıt boş. Bitiş sebebi: {bitis}, kullanım: {kullanim}")
            return None

        cozulen = _json_ayikla(metin)
        if cozulen is None:
            logger.warning(
                f"JSON çözülemedi (bitiş: {bitis}, {len(metin)} karakter). "
                f"Son 120 karakter: {metin[-120:]!r}"
            )
        return cozulen
    except Exception as e:
        logger.error(f"Gemini yanıtı çözümlenemedi: {e}")
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


# Puanlamada gerçekten işe yarayan kalemler. Bunlardan yeterince
# varsa veri kullanılabilir sayılır.
DEGERLI_KALEMLER = {
    "NetKar", "Ozkaynak", "Hasilat", "FaaliyetKari", "BrutKar",
    "ToplamVarlik", "ToplamBorc", "DonenVarlik", "KisaVadeliYukumluluk",
    "IsletmeNakitAkisi",
}


def guvenilir_mi(kalemler: dict, dogrulamalar: dict) -> tuple[bool, str]:
    """
    Veri kullanılabilir mi?

    DEĞİŞİKLİK: Önceki kural "NetKar VE Ozkaynak olmak zorunda" idi ve
    fazla katıydı. İsvea'da 11, Intercity'de 10 kalem bulunmuştu ama bu
    ikisi eksik olduğu için HEPSİ çöpe gidiyordu. Oysa proje.py zaten
    kısmi veriyle çalışacak şekilde tasarlandı: her boyut kendi
    verisi yoksa o boyutu hiç puanlamıyor, veri güvenilirliği yüzdesi
    de buna göre düşüyor.

    Yeni kural:
      - Hiçbir tutarlılık kontrolü BAŞARISIZ olmamalı (bu değişmedi;
        uydurma veriyi engelleyen asıl koruma budur)
      - En az 1 kontrol gerçekten yapılmış olmalı
      - Puanlamada işe yarayan en az 4 kalem bulunmuş olmalı
    """
    basarisiz = [k for k, v in dogrulamalar.items() if v is False]
    if basarisiz:
        return False, "Tutarlılık kontrolü başarısız: " + ", ".join(basarisiz)
    if not dogrulamalar:
        return False, "Hiçbir tutarlılık kontrolü yapılamadı (yetersiz kalem)."

    degerli = [k for k in kalemler if k in DEGERLI_KALEMLER]
    if len(degerli) < 4:
        return False, (
            f"Yetersiz kalem ({len(degerli)} adet): puanlama için "
            f"en az 4 anlamlı finansal kalem gerekli."
        )

    # Bilgilendirme: hangi ana kalemler eksik?
    eksikler = [k for k in ("NetKar", "Ozkaynak", "Hasilat") if k not in kalemler]
    if eksikler:
        return True, f"Kullanılabilir ancak şu kalemler eksik: {', '.join(eksikler)}"
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
    """
    PDF indirir ve GERÇEKTEN PDF olduğunu doğrular.

    DÜZELTME: Bazı sunucular (Cloudflare koruması, 404 sayfası, oturum
    yönlendirmesi) PDF yerine küçük bir HTML sayfası döndürüyor. Önceki
    sürüm yalnızca boyuta bakıyordu; 10-50 KB'lik bir HTML hata sayfası
    bu kontrolü geçip .pdf olarak kaydediliyor, pdfplumber açmaya
    çalışınca "No /Root object! - Is this really a PDF?" hatasıyla
    TÜM ÇALIŞMA çöküyordu.

    Artık dosyanın imzası kontrol ediliyor: geçerli her PDF "%PDF-"
    bayt dizisiyle başlar.
    """
    from curl_cffi import requests as cr
    try:
        r = cr.get(url, impersonate="chrome", timeout=180)
    except Exception as e:
        logger.error(f"PDF indirme hatası: {e}")
        return False

    if r.status_code != 200:
        logger.error(f"PDF indirilemedi (HTTP {r.status_code}): {url}")
        return False

    icerik = r.content or b""
    if len(icerik) < 10_000:
        logger.error(
            f"Dosya çok küçük ({len(icerik):,} bayt), PDF değil: {url}"
        )
        return False

    # Kimlik kontrolü: her geçerli PDF "%PDF-" ile başlar.
    # Bazı sunucular başa boşluk/BOM koyabildiği için ilk 1 KB taranıyor.
    if not icerik.lstrip()[:5].startswith(b"%PDF-") and b"%PDF-" not in icerik[:1024]:
        onizleme = icerik[:120].decode("utf-8", "replace").replace("\n", " ")
        logger.error(
            f"İndirilen dosya PDF değil ({len(icerik):,} bayt). "
            f"Muhtemelen HTML hata sayfası. Başlangıç: {onizleme!r}"
        )
        return False

    hedef.write_bytes(icerik)
    logger.info(f"PDF indirildi: {len(icerik)/1e6:.2f} MB ({len(icerik):,} bayt)")
    return True


# Taranmış PDF'lerde sayfa puanlaması çalışmadığı için daha geniş
# pencere kullanılır; kapsama alanı doğruluktan daha kritik.
TARANMIS_PENCERE = int(os.environ.get("TARANMIS_PENCERE", "8"))
# Keşif turu: taranmış PDF'lerde önce modele "hangi sayfada finansal
# tablo var?" diye sorulur. Kapatmak için KESIF_AKTIF=0.
KESIF_AKTIF = os.environ.get("KESIF_AKTIF", "1") not in ("0", "false", "False")


def aday_pencereler(pdf_yolu: str, pencere: int = MAX_SAYFA_GONDER) -> list[list[int]]:
    """
    Denenecek sayfa aralıklarını sırayla üretir.

    DÜZELTME: Taranmış PDF'lerde metin olmadığı için sayfa puanlaması
    çalışmıyor ve tek bir konum tahmini (%52) yapılıyordu. Bu tahmin
    tutmayınca hiç veri çıkmıyordu ("0 kalem").

    Artık birden fazla aday pencere üretiliyor ve ilk başarılı olanda
    duruluyor. Finansal tablolar izahnamelerde tipik olarak son
    üçte birde, ekler bölümünden hemen önce yer alır.
    """
    import warnings
    warnings.filterwarnings("ignore")
    import pdfplumber

    with pdfplumber.open(pdf_yolu) as pdf:
        toplam = len(pdf.pages)
        metinler = [(s.extract_text() or "") for s in pdf.pages]

    ortalama = sum(len(m) for m in metinler) / max(toplam, 1)

    # ── Metin tabanlı PDF ──
    # DÜZELTME: Eskiden tek pencere üretiliyordu. Kardemir'de puanlama
    # dağınık sayfalar seçti (39, 168, 174, 179, 180) ve bilanço
    # kalemleri bulundu ama gelir tablosu kaçtı. Artık en iyi sayfaların
    # ETRAFI da ayrı pencereler olarak deneniyor — finansal tablolar
    # birbirini izleyen sayfalarda yer alır.
    if ortalama >= 200:
        secilen = finansal_sayfalari_bul(pdf_yolu, pencere)
        if not secilen:
            return []
        pencereler = [secilen]
        # En yüksek puanlı sayfanın etrafındaki bitişik blok
        odak = secilen[len(secilen) // 2]
        komsu = [k for k in range(max(0, odak - 2), min(toplam, odak + 6))]
        if komsu and komsu != secilen:
            pencereler.append(komsu)
        return pencereler

    # ── Taranmış PDF: birden çok oran denenir ──
    pencere = max(pencere, TARANMIS_PENCERE)
    logger.info(f"PDF taranmış ({ortalama:.0f} kr/sayfa), {toplam} sayfa.")

    # Önce modele sor: hangi sayfada finansal tablo var?
    # Kör tahminden çok daha isabetli.
    if KESIF_AKTIF:
        bulunan = kesif_turu(pdf_yolu, toplam)
        if bulunan:
            # Bulunan sayfaların etrafını da al (tablolar birden fazla
            # sayfaya yayılabiliyor)
            genis: set[int] = set()
            for b in bulunan:
                for k in range(b - 1, b + 3):
                    if 0 <= k < toplam:
                        genis.add(k)
            sirali = sorted(genis)[:max(pencere, len(bulunan) + 4)]
            # DÜZELTME: Yedek pencereler belgenin ortasına göre değil,
            # KEŞFİN BULDUĞU sayfaya göre üretilmeli. Intercity'de keşif
            # 122. sayfayı buldu ama yedekler 88-95 ve 80-87'ye gitti —
            # yani doğru bölgeden UZAKLAŞTI. Bilançonun aktif ve pasif
            # tarafları ardışık sayfalarda olduğu için, kaçan kalemler
            # neredeyse her zaman bulunan sayfanın hemen yanındadır.
            merkez = bulunan[0]
            yedekler = [
                [k for k in range(merkez + 2, min(toplam, merkez + 2 + pencere))],
                [k for k in range(max(0, merkez - pencere - 1), max(1, merkez - 1))],
            ]
            return [sirali] + [y for y in yedekler if y]

    return _kor_pencereler(toplam, pencere)


def _kor_pencereler(toplam: int, pencere: int) -> list[list[int]]:
    """Keşif turu sonuç vermezse kullanılan konum tahmini pencereleri."""
    # Pencereler BİTİŞİK olmalı. Seyrek örnekleme yapılırsa aradaki
    # sayfalar hiç görülmez: Quick Sigorta'nın bilançosu 185. sayfadaydı
    # ve seyrek pencereler onu tamamen atlıyordu.
    #
    # Merkez %50'den başlanır (finansal tablolar izahnamenin ortasının
    # biraz sonrasında, ekler bölümünden önce yer alır), sonra sırayla
    # aşağı ve yukarı bitişik bloklar denenir.
    merkez = int(toplam * 0.50)
    baslangiclar: list[int] = []
    for adim in range(0, 6):
        # 0, -1, +1, -2, +2, -3 ... sırasıyla merkezden uzaklaş
        yon = 0 if adim == 0 else (-((adim + 1) // 2) if adim % 2 else (adim // 2))
        bas = merkez + yon * pencere
        bas = max(0, min(bas, max(0, toplam - pencere)))
        if bas not in baslangiclar:
            baslangiclar.append(bas)

    pencereler = [
        list(range(b, min(toplam, b + pencere))) for b in baslangiclar
    ]
    return [p for p in pencereler if p]


KESIF_ISTEMI = """Bu görüntüler bir halka arz izahnamesinin ardışık sayfalarıdır.
Sana verilen sayfa numaraları sırayla: %s

GÖREV: Hangi sayfalarda FİNANSAL TABLO var, onu bul.

Finansal tablo = Bilanço (Finansal Durum Tablosu), Gelir Tablosu
(Kar veya Zarar Tablosu) veya Nakit Akış Tablosu.
Bunlar rakam dolu, satırları "Hasılat / Dönem Karı / Toplam Varlıklar /
Özkaynaklar" gibi kalemlerden oluşan tablolardır.

DİKKAT:
- İçindekiler sayfası finansal tablo DEĞİLDİR.
- Düz metin, risk faktörleri, şirket tanıtımı finansal tablo DEĞİLDİR.
- Sadece GERÇEK tabloları işaretle.

Sadece JSON döndür:
{"finansal_sayfalar": [185, 186, 187], "aciklama": "185 bilanço, 186 gelir tablosu"}

Hiç finansal tablo yoksa: {"finansal_sayfalar": [], "aciklama": "yok"}
"""


def kesif_turu(pdf_yolu: str, toplam: int) -> list[int]:
    """
    KEŞİF TURU — taranmış PDF'lerde doğru sayfayı bulmanın anahtarı.

    NEDEN: Taranmış PDF'te metin olmadığı için sayfa puanlaması
    çalışmıyordu; kör konum tahmini (%50) çoğu izahnamede tutmuyor ve
    "0 kalem" ile sonuçlanıyordu.

    NASIL: İzahnamenin orta-son bölümü DÜŞÜK çözünürlükte (küçük ve
    ucuz görüntüler) modele gösterilip "hangi sayfada finansal tablo
    var?" diye soruluyor. Model sayfaları söyleyince o sayfalar TAM
    çözünürlükte tekrar gönderilip okunuyor.

    Maliyet: keşif görüntüleri çok küçük (60 DPI), ek yük düşük.
    """
    import warnings
    warnings.filterwarnings("ignore")
    import pdfplumber

    # Taranacak bölge: izahnamelerin %35-%80 aralığı. Finansal tablolar
    # neredeyse her zaman bu bantta yer alır.
    bas = int(toplam * 0.35)
    bit = int(toplam * 0.80)
    adim = max(1, (bit - bas) // 24)          # en fazla ~24 sayfa örnekle
    ornek = list(range(bas, bit, adim))[:24]
    if not ornek:
        return []

    logger.info(
        f"  Keşif turu: {ornek[0]+1}-{ornek[-1]+1} arası {len(ornek)} sayfa "
        f"düşük çözünürlükte taranıyor..."
    )
    goruntuler: list[bytes] = []
    with pdfplumber.open(pdf_yolu) as pdf:
        for i in ornek:
            try:
                im = pdf.pages[i].to_image(resolution=60).original
                if im.mode != "RGB":
                    im = im.convert("RGB")
                tampon = io.BytesIO()
                im.save(tampon, format="JPEG", quality=55, optimize=True)
                goruntuler.append(tampon.getvalue())
            except Exception:
                continue
    if not goruntuler:
        return []

    mb = sum(len(g) for g in goruntuler) / 1e6
    logger.info(f"  {len(goruntuler)} keşif görüntüsü ({mb:.1f} MB)")

    istem = KESIF_ISTEMI % ", ".join(str(i + 1) for i in ornek)
    ham = llm_cagir(goruntuler, istem=istem, jpeg=True)
    if not ham:
        logger.info("  Keşif turu sonuç vermedi.")
        return []

    sayfalar = ham.get("finansal_sayfalar") or []
    aciklama = ham.get("aciklama", "")
    temiz: list[int] = []
    for s in sayfalar:
        try:
            n = int(s) - 1                      # 1-tabanlıdan 0-tabanlıya
        except (TypeError, ValueError):
            continue
        if 0 <= n < toplam:
            temiz.append(n)
    if temiz:
        logger.info(
            f"  Keşif sonucu: sayfa {[t+1 for t in temiz]} — {aciklama}"
        )
    return sorted(set(temiz))


def _sonuclari_birlestir(a: Optional[FinansalSonuc],
                         b: FinansalSonuc) -> FinansalSonuc:
    """
    İki denemenin sonuçlarını birleştirir ve yeniden doğrular.

    Finansal tablolar izahnamede dağınık: bilanço bir sayfada, gelir
    tablosu birkaç sayfa sonra. Her denemeyi ayrı ayrı değerlendirmek
    yerine üst üste koymak, tabloların tamamını yakalamayı sağlıyor.

    Çakışma durumunda DAHA ÇOK DÖNEMİ olan seri tercih edilir.
    """
    if a is None:
        return b

    birlesik = FinansalSonuc(
        slug=b.slug, sirket_adi=b.sirket_adi or a.sirket_adi,
        izahname_url=b.izahname_url or a.izahname_url,
        model=b.model or a.model, islenme_zamani=b.islenme_zamani,
        olcek=b.olcek if b.seriler else a.olcek,
    )
    birlesik.islenen_sayfalar = sorted(set(a.islenen_sayfalar) |
                                       set(b.islenen_sayfalar))

    seriler: dict[str, dict[str, float]] = {}
    for kaynak in (a.seriler, b.seriler):
        for alan, seri in (kaynak or {}).items():
            mevcut = seriler.get(alan)
            if mevcut is None or len(seri) > len(mevcut):
                seriler[alan] = dict(seri)
    birlesik.seriler = seriler

    tum = sorted({d for s in seriler.values() for d in s})
    birlesik.donemler = tum
    if tum:
        son = tum[-1]
        birlesik.guncel = {al: s[son] for al, s in seriler.items() if son in s}

    birlesik.dogrulama = dogrula(birlesik.guncel)
    birlesik.guvenilir, birlesik.not_ = guvenilir_mi(
        birlesik.guncel, birlesik.dogrulama)
    return birlesik


def pdf_isle(pdf_yolu: str, slug: str, sirket_adi: str = "",
             izahname_url: str = "", max_deneme: int = 3) -> FinansalSonuc:
    """
    Tek bir PDF'i işler. Taranmış PDF'lerde farklı sayfa aralıkları
    denenir; ilk GÜVENİLİR sonuçta durulur.
    """
    pencereler = aday_pencereler(pdf_yolu)
    if not pencereler:
        s = FinansalSonuc(slug=slug, sirket_adi=sirket_adi,
                          izahname_url=izahname_url)
        s.not_ = "PDF'te aday finansal tablo sayfası bulunamadı."
        return s

    en_iyi: Optional[FinansalSonuc] = None
    for deneme, sayfalar in enumerate(pencereler[:max_deneme], start=1):
        logger.info(
            f"  Deneme {deneme}/{min(len(pencereler), max_deneme)} — "
            f"sayfalar (1-tabanlı): {[s+1 for s in sayfalar]}"
        )
        goruntuler, jpeg_mi = sayfalari_goruntuye_cevir(pdf_yolu, sayfalar)
        if not goruntuler:
            continue
        toplam_mb = sum(len(g) for g in goruntuler) / 1e6
        logger.info(
            f"  {len(goruntuler)} görüntü ({toplam_mb:.1f} MB, "
            f"{'JPEG' if jpeg_mi else 'PNG'}) gönderiliyor"
        )

        ham = llm_cagir(goruntuler, jpeg=jpeg_mi)
        if ham is None:
            logger.warning("  Yapay zeka yanıtı alınamadı, sonraki aralık.")
            continue

        sonuc = llm_ciktisini_isle(ham, slug)
        sonuc.sirket_adi = sirket_adi
        sonuc.izahname_url = izahname_url
        sonuc.islenen_sayfalar = [s + 1 for s in sayfalar]

        if sonuc.guvenilir:
            logger.info(f"  ✓ Güvenilir sonuç ({len(sonuc.guncel)} kalem)")
            return sonuc

        logger.info(f"  Yetersiz ({len(sonuc.guncel)} kalem): {sonuc.not_}")

        # DENEMELERİ BİRLEŞTİR — en önemli iyileştirme.
        # Finansal tablolar tek sayfada değil: bilanço bir sayfada,
        # gelir tablosu diğerinde, nakit akışı bir başkasında.
        # Kardemir'de 14 kalem bulunmuş ama Net Kâr/Özkaynak farklı
        # sayfada kaldığı için hepsi çöpe gidiyordu. Artık her deneme
        # bir öncekinin üstüne ekleniyor.
        en_iyi = _sonuclari_birlestir(en_iyi, sonuc)
        if en_iyi.guvenilir:
            logger.info(
                f"  ✓ Denemeler birleştirilince güvenilir oldu "
                f"({len(en_iyi.guncel)} kalem)"
            )
            return en_iyi

    if en_iyi is not None:
        return en_iyi
    s = FinansalSonuc(slug=slug, sirket_adi=sirket_adi,
                      izahname_url=izahname_url)
    s.not_ = "Hiçbir sayfa aralığında yapay zeka yanıtı alınamadı."
    return s


def kaydet(sonuc: FinansalSonuc) -> Path:
    CIKTI_DIZINI.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Yazılıyor: {CIKTI_DIZINI}")
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
    ap.add_argument("--modelleri-listele", action="store_true",
                    help="API anahtarının erişebildiği modelleri göster")
    args = ap.parse_args()

    # ── Tanı modu: hangi modeller kullanılabilir? ──
    if args.modelleri_listele:
        if not LLM_ANAHTAR:
            print("HATA: LLM_API_KEY tanımlı değil.")
            return 1
        modeller = gemini_modelleri_listele()
        if not modeller:
            print("Hiç model listelenemedi. API anahtarı geçersiz olabilir.")
            return 1
        print(f"\nAnahtarınızın erişebildiği {len(modeller)} model:\n")
        for m in modeller:
            isaret = "  <-- şu an ayarlı" if m == LLM_MODEL else ""
            print(f"  {m}{isaret}")
        secilen = gemini_model_sec()
        print(f"\nOtomatik seçilecek model: {secilen}")
        print("\nBunu sabitlemek icin GitHub -> Settings -> "
              "Secrets and variables -> Actions -> Variables kismina:")
        print(f"  LLM_MODEL = {secilen}\n")
        return 0

    CIKTI_DIZINI.mkdir(parents=True, exist_ok=True)
    logger.info(f"Proje kökü : {KOK}")
    logger.info(f"Çıktı dizini: {CIKTI_DIZINI}")

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
    hatalar = 0
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
            hatalar += 1
            continue
        try:
            # DÜZELTME: Bozuk bir PDF (veya beklenmedik bir hata) tüm
            # çalışmayı çökertiyordu. Artık her şirket kendi içinde
            # yalıtılmış; biri patlarsa diğerleri işlenmeye devam eder.
            sonuc = pdf_isle(str(gecici), slug, s["ad"], url)
            yol = kaydet(sonuc)
            durum = "✓ GÜVENİLİR" if sonuc.guvenilir else "✗ GÜVENİLMEZ"
            logger.info(f"{durum} ({len(sonuc.guncel)} kalem) -> {yol.name}")
            if not sonuc.guvenilir:
                logger.warning(f"  Sebep: {sonuc.not_}")
            islenen += 1
        except Exception as e:
            hatalar += 1
            logger.error(f"  {s['ad']} işlenemedi, atlanıyor: {type(e).__name__}: {e}")
        finally:
            gecici.unlink(missing_ok=True)

    logger.info(f"Bitti. {islenen} şirket işlendi, {hatalar} şirket atlandı.")
    # Bazı şirketler atlanmış olsa bile çalışma BAŞARILI sayılır;
    # aksi halde tek bozuk PDF yüzünden commit adımı hiç çalışmaz ve
    # başarıyla üretilmiş JSON'lar kaybolur.
    return 0


if __name__ == "__main__":
    sys.exit(main())