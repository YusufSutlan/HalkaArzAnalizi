"""
═══════════════════════════════════════════════════════════════════
ORTAK HTTP İSTEMCİSİ
═══════════════════════════════════════════════════════════════════

Sunucu (proje.py), izahname hattı (Izahnameisle.py), geriye dönük test
aracı ve EVDS istemcisi dış kaynaklara bu modül üzerinden gider.

İlkeler:
  - Tarayıcı taklidi YOK. Her istek kendini açıkça tanıtır (USER_AGENT);
    site sahibi gerekirse bize ulaşabilir.
  - Nazik tarama: aynı sunucuya ardışık istekler arasında en az
    ASGARI_ARALIK saniye beklenir; 429/503'te Retry-After'a uyulur.
  - Sertifika doğrulaması asla kapatılmaz. Güven kökleri certifi'dir;
    ara sertifikasını göndermeyen sunucular için doğrulanmış ara
    sertifikalar veri/ek_ara_sertifikalar.pem dosyasından eklenir.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import os
import ssl
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import certifi
import httpx

logger = logging.getLogger(__name__)
# httpx her isteği INFO seviyesinde logluyor; sunucu loglarını kirletmesin.
logging.getLogger("httpx").setLevel(logging.WARNING)

USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT",
    "HalkaArzAnaliz/1.0 (+https://stltechnology.com.tr; stlteknoloji.2026@gmail.com)",
)
# Aynı sunucuya iki istek arasında en az bu kadar saniye.
ASGARI_ARALIK = float(os.environ.get("HTTP_ASGARI_ARALIK", "0.5"))
# 429/503 yanıtında Retry-After yoksa ya da çok uzunsa kullanılacak sınır.
AZAMI_BEKLEME = 30.0
EK_SERTIFIKALAR = Path(__file__).resolve().parent / "veri" / "ek_ara_sertifikalar.pem"
_TEKRAR_KODLARI = {429, 500, 502, 503, 504}


@lru_cache(maxsize=1)
def ssl_baglami() -> ssl.SSLContext:
    """certifi kökleri + doğrulanmış ek ara sertifikalar. Doğrulama açık."""
    baglam = ssl.create_default_context(cafile=certifi.where())
    if EK_SERTIFIKALAR.exists():
        baglam.load_verify_locations(cafile=str(EK_SERTIFIKALAR))
    return baglam


def _bekleme_suresi(yanit: Optional[httpx.Response], deneme: int) -> float:
    """Retry-After başlığına uyar; yoksa artan bekleme."""
    if yanit is not None:
        deger = yanit.headers.get("Retry-After")
        if deger:
            try:
                return min(AZAMI_BEKLEME, max(0.0, float(deger)))
            except ValueError:
                try:
                    zaman = email.utils.parsedate_to_datetime(deger).timestamp()
                    return min(AZAMI_BEKLEME, max(0.0, zaman - time.time()))
                except (TypeError, ValueError):
                    pass
    return min(AZAMI_BEKLEME, 2.0 * (deneme + 1))


def _basliklar(ek: Optional[dict] = None) -> dict:
    b = {"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.5"}
    if ek:
        b.update(ek)
    return b


# ───────────────────────── eşzamanlı (sync) ─────────────────────────

class _SunucuSirasi:
    """Sunucu başına asgari aralık (iş parçacığı güvenli)."""

    def __init__(self, aralik: float):
        self.aralik = aralik
        self._son: dict[str, float] = {}
        self._kilit = threading.Lock()

    def bekle(self, url: str) -> None:
        sunucu = urlsplit(url).netloc
        with self._kilit:
            simdi = time.monotonic()
            sira = max(simdi, self._son.get(sunucu, 0.0) + self.aralik)
            self._son[sunucu] = sira
        if sira > simdi:
            time.sleep(sira - simdi)


_SIRA = _SunucuSirasi(ASGARI_ARALIK)
_istemci: Optional[httpx.Client] = None
_istemci_kilit = threading.Lock()


def _sync_istemci() -> httpx.Client:
    global _istemci
    with _istemci_kilit:
        if _istemci is None:
            _istemci = httpx.Client(
                headers=_basliklar(), verify=ssl_baglami(),
                follow_redirects=True, timeout=30.0,
            )
        return _istemci


def getir(url: str, *, zaman_asimi: float = 30.0, deneme_sayisi: int = 3,
          basliklar: Optional[dict] = None) -> httpx.Response:
    """
    GET isteği. 429/5xx ve ağ hatalarında Retry-After'a uyarak tekrar dener.
    Son yanıtı döndürür (başarısızsa da); hiç yanıt alınamazsa son hatayı
    yükseltir. Durum kodunu kontrol etmek çağıranın işidir.
    """
    son_hata: Optional[Exception] = None
    yanit: Optional[httpx.Response] = None
    for deneme in range(deneme_sayisi):
        _SIRA.bekle(url)
        try:
            yanit = _sync_istemci().get(url, timeout=zaman_asimi, headers=basliklar)
            if yanit.status_code not in _TEKRAR_KODLARI:
                return yanit
        except httpx.HTTPError as e:
            son_hata, yanit = e, None
        if deneme < deneme_sayisi - 1:
            time.sleep(_bekleme_suresi(yanit, deneme))
    if yanit is not None:
        return yanit
    raise son_hata or httpx.HTTPError("istek başarısız")


# ───────────────────────── asenkron (sunucu) ─────────────────────────

class AsyncIstemci:
    """
    Sunucu için asenkron istemci.

    - Sunucu başına asgari aralık (asyncio kilidiyle sıralı).
    - Sayfa önbelleği: aynı adres `onbellek_suresi` saniye içinde tekrar
      istenirse ağa gidilmez. Kaynak site ETag/Last-Modified göndermediği
      için koşullu istek yapılamıyor; süreli önbellek bu yüzden var.
    """

    def __init__(self, aralik: float = ASGARI_ARALIK, onbellek_suresi: float = 0.0,
                 onbellek_kapasitesi: int = 300):
        self.aralik = aralik
        self.onbellek_suresi = onbellek_suresi
        self.onbellek_kapasitesi = onbellek_kapasitesi
        self._istemci: Optional[httpx.AsyncClient] = None
        self._son: dict[str, float] = {}
        self._kilitler: dict[str, asyncio.Lock] = {}
        self._onbellek: dict[str, tuple[float, str]] = {}

    def _client(self) -> httpx.AsyncClient:
        if self._istemci is None or self._istemci.is_closed:
            self._istemci = httpx.AsyncClient(
                headers=_basliklar(), verify=ssl_baglami(),
                follow_redirects=True, timeout=30.0,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._istemci

    async def _sira_bekle(self, url: str) -> None:
        sunucu = urlsplit(url).netloc
        kilit = self._kilitler.setdefault(sunucu, asyncio.Lock())
        async with kilit:
            bekle = self._son.get(sunucu, 0.0) + self.aralik - time.monotonic()
            if bekle > 0:
                await asyncio.sleep(bekle)
            self._son[sunucu] = time.monotonic()

    async def getir(self, url: str, *, zaman_asimi: float = 15.0,
                    deneme_sayisi: int = 3) -> Optional[httpx.Response]:
        yanit: Optional[httpx.Response] = None
        for deneme in range(deneme_sayisi):
            await self._sira_bekle(url)
            try:
                yanit = await self._client().get(url, timeout=zaman_asimi)
                if yanit.status_code not in _TEKRAR_KODLARI:
                    return yanit
                logger.warning(f"HTTP {yanit.status_code}: {url}")
            except httpx.HTTPError as e:
                logger.warning(f"Bağlantı hatası ({url}): {type(e).__name__}: {e}")
                yanit = None
            if deneme < deneme_sayisi - 1:
                await asyncio.sleep(_bekleme_suresi(yanit, deneme))
        return yanit

    async def metin(self, url: str, *, zaman_asimi: float = 15.0,
                    onbellekten: bool = True) -> Optional[str]:
        """Sayfa metni (200 değilse None). Önbellekte tazeyse ağa gidilmez."""
        if onbellekten and self.onbellek_suresi > 0:
            kayit = self._onbellek.get(url)
            if kayit and time.monotonic() - kayit[0] < self.onbellek_suresi:
                return kayit[1]
        yanit = await self.getir(url, zaman_asimi=zaman_asimi)
        if yanit is None or yanit.status_code != 200:
            if yanit is not None:
                logger.warning(f"Sayfa alınamadı ({url}), durum kodu: {yanit.status_code}")
            return None
        if self.onbellek_suresi > 0:
            if len(self._onbellek) >= self.onbellek_kapasitesi:
                en_eski = min(self._onbellek, key=lambda k: self._onbellek[k][0])
                self._onbellek.pop(en_eski, None)
            self._onbellek[url] = (time.monotonic(), yanit.text)
        return yanit.text

    async def kapat(self) -> None:
        if self._istemci is not None and not self._istemci.is_closed:
            await self._istemci.aclose()
