"""
═══════════════════════════════════════════════════════════════════
İZAHNAME ENTEGRASYON KATMANI
═══════════════════════════════════════════════════════════════════

Bu modül proje.py ile izahname.py arasındaki köprüdür.

NEDEN AYRI BİR KATMAN
    İzahname işlemek PAHALI: PDF'ler ~18 MB, 360-430 sayfa ve
    taranmış oldukları için OCR gerekiyor (~4 sn/sayfa).
    Naif bir entegrasyon (istek geldiğinde PDF indir + OCR yap)
    API'yi dakikalarca bloke eder ve Render'da zaman aşımına uğrar.

ÇÖZÜM: ÜÇ KADEMELİ
    1. Disk önbelleği  : İzahname bir kez yayınlanır, DEĞİŞMEZ.
                         Bir kez işlenip diske yazılır, bir daha
                         asla işlenmez.
    2. Hedefli sayfa   : 360 sayfanın tamamı değil, finansal tablo
                         bölümü taranır. halkarz.com zaten sayfa
                         numarasını veriyor ("* İzahname, Sayfa 181").
    3. Arka plan       : API isteği ASLA beklemez. Finansal veri
                         henüz yoksa skor onsuz üretilir, PDF arka
                         planda işlenir, sonraki istekte hazır olur.

VERİ AKIŞI
    halkarz.com detay sayfası
      └─ "Ekler" bölümü -> SPK Onaylı İzahname PDF linki
           └─ (önbellekte var mı?) -> evet: anında dön
                                   -> hayır: arka plana al, şimdilik None
                                             └─ indir -> hedefli OCR
                                                      -> kalem çıkarımı
                                                      -> doğrulama
                                                      -> diske yaz
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)

ONBELLEK_DIZINI = os.environ.get("IZAHNAME_ONBELLEK", "/tmp/izahname_onbellek")
# Aynı anda işlenecek en fazla PDF. OCR bellek yiyor; Render'ın
# ücretsiz planında 1'den fazlası riskli.
ESZAMANLI_PDF = int(os.environ.get("ESZAMANLI_PDF", "1"))
# PDF indirme üst sınırı (bayt). 18 MB tipik; 60 MB üstü şüpheli.
MAX_PDF_BOYUT = int(os.environ.get("MAX_PDF_BOYUT", str(60 * 1024 * 1024)))
# İzahname işleme özelliği açık mı? Render'da kaynak yetmezse kapatılır.
IZAHNAME_AKTIF = os.environ.get("IZAHNAME_AKTIF", "1") not in ("0", "false", "False")
# OCR dili. Dockerfile ile "tesseract-ocr-tur" kurulduğunda "tur+eng"
# kullanılır; Türkçe paket yoksa otomatik olarak "eng"e düşer.
# Türkçe paket ÖNEMLİ: onsuz "Dönem Net Kârı" -> "Dénem Net Kart" gibi
# okunuyor ve kalem adları tanınmıyor.
OCR_DILI = os.environ.get("OCR_DILI", "tur+eng")


# ═══════════════════════════════════════════════════════════════════
# 1. İZAHNAME LİNKİ BULMA
# ═══════════════════════════════════════════════════════════════════

# halkarz.com "Ekler" bölümündeki link metinleri
IZAHNAME_LINK_METINLERI = [
    "spk onaylı izahname", "izahname", "onaylı izahname",
]
# Bunlar izahname DEĞİL, karıştırılmamalı
HARIC_LINK_METINLERI = [
    "izahname ekleri", "esas sözleşme", "iç yönerge",
    "tasarruf sahiplerine satış duyurusu", "fiyat tespit raporu",
    "fon kullanım yeri raporu",
]


def _kucult(s: Optional[str]) -> str:
    return (s or "").replace("İ", "i").lower()


def izahname_linki_bul(soup) -> Optional[str]:
    """
    halkarz.com detay sayfasının "Ekler" bölümünden SPK onaylı
    izahname PDF linkini çıkarır.

    Sayfadaki yapı:
        <div class="acc-body"><ul>
          <li><a href="...izahname.pdf">SPK Onaylı İzahname</a></li>
          <li><a href="...">Tasarruf Sahiplerine Satış Duyurusu</a></li>
          ...
    """
    adaylar: list[tuple[int, str]] = []
    for a in soup.find_all("a"):
        href = a.attrs.get("href") if hasattr(a, "attrs") else None
        if not href:
            continue
        metin = _kucult(a.get_text(separator=" ", strip=True))
        h = _kucult(href)
        if any(x in metin for x in HARIC_LINK_METINLERI):
            continue
        puan = 0
        if any(x in metin for x in IZAHNAME_LINK_METINLERI):
            puan += 5
        if "izahname" in h:
            puan += 3
        if h.endswith(".pdf"):
            puan += 2
        if "spk onaylı" in metin:
            puan += 2
        if puan >= 5:
            adaylar.append((puan, href))
    if not adaylar:
        return None
    adaylar.sort(key=lambda x: -x[0])
    return adaylar[0][1]


def finansal_tablo_sayfasi_bul(veri_metni: str) -> Optional[int]:
    """
    halkarz.com dipnotlarda sayfa numarası veriyor:
        "* İzahname, Sayfa 181."
    Bu, 360 sayfalık PDF'in tamamını taramak yerine hedefli
    OCR yapmayı sağlar — işlem süresini ~24 dakikadan ~1 dakikaya indirir.
    """
    m = re.search(r"izahname[,\s]*sayfa\s*(\d{1,4})", _kucult(veri_metni))
    return int(m.group(1)) if m else None


# ═══════════════════════════════════════════════════════════════════
# 2. DİSK ÖNBELLEĞİ
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OnbellekKaydi:
    url: str
    durum: str                      # "basarili" | "basarisiz" | "isleniyor"
    zaman: float = field(default_factory=time.time)
    guncel: dict = field(default_factory=dict)      # {alan: deger}
    yillik: dict = field(default_factory=dict)      # {alan: {yil: deger}}
    dogrulama: dict = field(default_factory=dict)
    uyarilar: list = field(default_factory=list)
    kaynak: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "url": self.url, "durum": self.durum, "zaman": self.zaman,
            "guncel": self.guncel, "yillik": self.yillik,
            "dogrulama": self.dogrulama, "uyarilar": self.uyarilar,
            "kaynak": self.kaynak,
        }, ensure_ascii=False)

    @staticmethod
    def from_json(ham: str) -> "OnbellekKaydi":
        d = json.loads(ham)
        return OnbellekKaydi(
            url=d.get("url", ""), durum=d.get("durum", "basarisiz"),
            zaman=d.get("zaman", 0.0), guncel=d.get("guncel", {}),
            # JSON anahtarları string olur; yılları int'e geri çevir
            yillik={k: {int(y): v for y, v in seri.items()}
                    for k, seri in d.get("yillik", {}).items()},
            dogrulama=d.get("dogrulama", {}), uyarilar=d.get("uyarilar", []),
            kaynak=d.get("kaynak", ""),
        )


class IzahnameOnbellegi:
    """
    İzahname çıkarım sonuçlarını diske yazar.

    İzahname yayınlandıktan sonra DEĞİŞMEZ, bu yüzden süre sınırı yok.
    Başarısız denemeler de kaydedilir; aynı bozuk PDF her istekte
    yeniden indirilip OCR'lanmasın diye.
    """

    def __init__(self, dizin: str = ONBELLEK_DIZINI):
        self.dizin = dizin
        try:
            os.makedirs(dizin, exist_ok=True)
        except OSError as e:
            logger.warning(f"Önbellek dizini oluşturulamadı ({dizin}): {e}")
        self._bellek: dict[str, OnbellekKaydi] = {}
        self._kilit = asyncio.Lock()

    @staticmethod
    def _anahtar(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]

    def _yol(self, url: str) -> str:
        return os.path.join(self.dizin, f"{self._anahtar(url)}.json")

    def oku(self, url: str) -> Optional[OnbellekKaydi]:
        if url in self._bellek:
            return self._bellek[url]
        yol = self._yol(url)
        if not os.path.exists(yol):
            return None
        try:
            with open(yol, encoding="utf-8") as f:
                kayit = OnbellekKaydi.from_json(f.read())
            self._bellek[url] = kayit
            return kayit
        except Exception as e:
            logger.warning(f"Önbellek okunamadı ({yol}): {e}")
            return None

    def yaz(self, kayit: OnbellekKaydi) -> None:
        self._bellek[kayit.url] = kayit
        try:
            # Atomik yazım: yarım kalmış dosya okunmasın
            gecici = self._yol(kayit.url) + ".tmp"
            with open(gecici, "w", encoding="utf-8") as f:
                f.write(kayit.to_json())
            os.replace(gecici, self._yol(kayit.url))
        except Exception as e:
            logger.warning(f"Önbellek yazılamadı: {e}")


# ═══════════════════════════════════════════════════════════════════
# 3. İŞLEME SERVİSİ
# ═══════════════════════════════════════════════════════════════════

class IzahnameServisi:
    """
    İzahname PDF'lerini arka planda işler ve sonuçları önbellekten sunar.

    API isteği ASLA PDF işlemeyi beklemez. `finansal_getir` çağrısı:
      - Önbellekte varsa   -> veriyi döner
      - Yoksa              -> arka plan görevi başlatır, None döner
    """

    def __init__(self, fetch_bytes_fn: Callable,
                 onbellek: Optional[IzahnameOnbellegi] = None,
                 llm_fn: Optional[Callable] = None):
        # fetch_bytes_fn(url) -> bytes | None
        self._fetch = fetch_bytes_fn
        self.onbellek = onbellek or IzahnameOnbellegi()
        self._llm = llm_fn
        self._semafor = asyncio.Semaphore(ESZAMANLI_PDF)
        self._isleniyor: set[str] = set()
        self._gorevler: set = set()

    # ── Dışarıya açık API ──

    def finansal_getir(self, izahname_url: Optional[str],
                       ipucu_sayfa: Optional[int] = None) -> Optional[OnbellekKaydi]:
        """
        Önbellekten finansal veriyi döner. Yoksa arka plan işlemeyi
        başlatır ve None döner — çağıran BEKLEMEZ.
        """
        if not IZAHNAME_AKTIF or not izahname_url:
            return None
        kayit = self.onbellek.oku(izahname_url)
        if kayit is not None:
            return kayit if kayit.durum == "basarili" else None
        self._arka_plana_al(izahname_url, ipucu_sayfa)
        return None

    def _arka_plana_al(self, url: str, ipucu_sayfa: Optional[int]) -> None:
        if url in self._isleniyor:
            return
        self._isleniyor.add(url)
        try:
            gorev = asyncio.create_task(self._isle(url, ipucu_sayfa))
            # Görev referansı tutulmazsa çöp toplayıcı iptal edebilir
            self._gorevler.add(gorev)
            gorev.add_done_callback(self._gorevler.discard)
        except RuntimeError:
            # Çalışan event loop yoksa (test ortamı) sessizce geç
            self._isleniyor.discard(url)

    # ── Arka plan işleme ──

    async def _isle(self, url: str, ipucu_sayfa: Optional[int]) -> None:
        async with self._semafor:
            baslangic = time.time()
            try:
                logger.info(f"İzahname işleniyor: {url}")
                ham = await self._fetch(url)
                if not ham:
                    raise RuntimeError("PDF indirilemedi")
                if len(ham) > MAX_PDF_BOYUT:
                    raise RuntimeError(f"PDF çok büyük ({len(ham)} bayt)")

                sonuc = await asyncio.to_thread(
                    self._cikar_senkron, ham, ipucu_sayfa
                )
                if sonuc is None:
                    raise RuntimeError("Finansal veri çıkarılamadı")

                kayit = OnbellekKaydi(
                    url=url, durum="basarili",
                    guncel={k: v for k, v in sonuc.guncel().items()},
                    yillik=sonuc.yillik(),
                    dogrulama=sonuc.dogrulama,
                    uyarilar=sonuc.uyarilar,
                    kaynak=sonuc.kaynak,
                )
                self.onbellek.yaz(kayit)
                logger.info(
                    f"İzahname işlendi ({time.time()-baslangic:.0f} sn): "
                    f"{len(kayit.guncel)} kalem — {url}"
                )
            except Exception as e:
                logger.warning(f"İzahname işlenemedi ({url}): {e}")
                self.onbellek.yaz(OnbellekKaydi(
                    url=url, durum="basarisiz", uyarilar=[str(e)]
                ))
            finally:
                self._isleniyor.discard(url)

    def _cikar_senkron(self, pdf_baytlari: bytes, ipucu_sayfa: Optional[int]):
        """
        Bloke eden kısım — ayrı bir thread'de çalışır ki event loop
        donmasın. PDF açma, OCR ve ayrıştırma burada.
        """
        import io
        import tempfile
        from izahname import IzahnameCikarici

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tf:
            tf.write(pdf_baytlari)
            tf.flush()
            sayfa_metinleri = self._sayfa_metinleri_al(tf.name, ipucu_sayfa)
            if not sayfa_metinleri:
                return None
            cikarici = IzahnameCikarici(llm_fn=self._llm)
            return cikarici.cikar(tf.name, sayfa_metinleri=sayfa_metinleri)

    @staticmethod
    def _sayfa_metinleri_al(pdf_yolu: str,
                            ipucu_sayfa: Optional[int]) -> Optional[list[str]]:
        """
        Önce hızlı metin çıkarımı dener. PDF taranmışsa (metin yoksa)
        yalnızca HEDEFLİ bir aralığı OCR'lar.

        Hedefli tarama kritik: 360 sayfanın tamamını OCR'lamak ~24 dakika
        sürer ve sunucuda kabul edilemez. halkarz.com'un verdiği sayfa
        ipucu ile bu ~1 dakikaya iner.
        """
        import warnings
        warnings.filterwarnings("ignore")
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber kurulu değil; izahname işlenemiyor.")
            return None

        with pdfplumber.open(pdf_yolu) as pdf:
            toplam = len(pdf.pages)
            metinler = [(s.extract_text() or "") for s in pdf.pages]

            ortalama = sum(len(m) for m in metinler) / max(toplam, 1)
            if ortalama >= 300:
                return metinler          # metin tabanlı, OCR gerekmiyor

            # ── Taranmış PDF: hedefli OCR ──
            logger.info(f"PDF taranmış görünüyor ({ortalama:.0f} kr/sayfa); OCR yapılacak.")
            try:
                import pytesseract
            except ImportError:
                logger.warning("pytesseract kurulu değil; taranmış PDF okunamıyor.")
                return None

            if ipucu_sayfa and 1 <= ipucu_sayfa <= toplam:
                # İpucu sayfa numarası, basılı sayfa numarasıdır ve PDF
                # indeksinden birkaç sayfa kayabilir. Geniş bir pencere alınır.
                bas = max(0, ipucu_sayfa - 8)
                bit = min(toplam, ipucu_sayfa + 12)
            else:
                # İpucu yoksa: finansal tablolar tipik olarak izahnamenin
                # son üçte birindedir.
                bas = int(toplam * 0.45)
                bit = min(toplam, bas + 40)

            # Türkçe dil paketi kurulu mu? Kurulu değilse İngilizce'ye düş,
            # yoksa pytesseract her sayfada hata verir.
            dil = OCR_DILI
            try:
                mevcut = pytesseract.get_languages(config="")
                istenen = [d for d in OCR_DILI.split("+") if d in mevcut]
                dil = "+".join(istenen) if istenen else "eng"
                if dil != OCR_DILI:
                    logger.warning(
                        f"OCR dili '{OCR_DILI}' tam kurulu değil, '{dil}' kullanılıyor. "
                        f"Türkçe için Dockerfile'a 'tesseract-ocr-tur' ekleyin."
                    )
            except Exception:
                dil = "eng"

            logger.info(f"OCR aralığı: {bas+1}-{bit} ({bit-bas} sayfa), dil: {dil}")
            for i in range(bas, bit):
                try:
                    im = pdf.pages[i].to_image(resolution=150).original
                    metinler[i] = pytesseract.image_to_string(im, lang=dil)
                except Exception as e:
                    logger.debug(f"OCR hatası sayfa {i+1}: {e}")
            return metinler


# ═══════════════════════════════════════════════════════════════════
# 4. proje.py'ye AKTARMA
# ═══════════════════════════════════════════════════════════════════

# izahname.py alan adı -> proje.py FinKey değeri
ALAN_ESLEME: dict[str, str] = {
    "Hasilat": "Hasilat",
    "BrutKar": "BrutKar",
    "FaaliyetKari": "FaaliyetKari",
    "NetKar": "NetKar",
    "Ozkaynak": "Ozkaynak",
    "DonenVarlik": "DonenVarlik",
    "KisaVadeliYukumluluk": "KisaVadeliYukumluluk",
    "ToplamBorc": "ToplamBorc",
    "FinansalBorc": "FinansalBorc",
    "Nakit": "Nakit",
    "IsletmeNakitAkisi": "IsletmeNakitAkisi",
    "FinansmanGideri": "FinansmanGideri",
    "Amortisman": "Amortisman",
    "YazilanPrim": "Hasilat",   # sigorta şirketinde hasılat vekili
}


def kayittan_finansal_uret(kayit: OnbellekKaydi, FinKey) -> tuple[dict, dict]:
    """
    Önbellek kaydını proje.py'nin beklediği (fin, seriler) biçimine çevirir.

    fin     : {FinKey: deger}           -> güncel dönem
    seriler : {FinKey: {yil: deger}}    -> büyüme hesabı için
    """
    fk_haritasi = {k.value: k for k in FinKey}
    fin: dict = {}
    seriler: dict = {}

    for alan, deger in (kayit.guncel or {}).items():
        hedef = ALAN_ESLEME.get(alan)
        if hedef and hedef in fk_haritasi and deger is not None:
            fin.setdefault(fk_haritasi[hedef], float(deger))

    for alan, seri in (kayit.yillik or {}).items():
        hedef = ALAN_ESLEME.get(alan)
        if not hedef or hedef not in fk_haritasi or len(seri) < 2:
            continue
        seriler.setdefault(fk_haritasi[hedef],
                           {int(y): float(v) for y, v in seri.items()})
    return fin, seriler