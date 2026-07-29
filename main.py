# main.py
"""
Halka Arz Asistanı Pro — V3 Quant Engine Entry Point
====================================================
"""
import time
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import SETTINGS, YATIRIM_UYARISI
from scraper import DataExtractorV3

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CACHE YÖNETİMİ
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


# ═══════════════════════════════════════════════════════════════════
# FASTAPI UYGULAMASI
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    extractor = DataExtractorV3()
    app.state.extractor = extractor
    logger.info("V3 Kantitatif Analiz Motoru başlatıldı.")
    yield
    await extractor.close()
    logger.info("V3 Kantitatif Analiz Motoru durduruldu.")


app = FastAPI(
    title="Halka Arz Asistanı Pro - V3 Quant Engine",
    description="Gelişmiş kantitatif değerleme, trend analizi ve modüler yapı.",
    version="3.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if SETTINGS.ALLOWED_ORIGINS == "*" else SETTINGS.ALLOWED_ORIGINS.split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/halkarzlar")
async def get_halka_arzlar(
    debug: bool = Query(False, description="Debug modu (ham verileri döner)"),
    x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key")
):
    """Flutter uygulamasının doğrudan bağlandığı ana endpoint."""
    if debug and not check_debug_permission(x_debug_key):
        raise HTTPException(status_code=403, detail="Debug modu için geçerli API Key gerekli.")

    if not debug:
        cached = await CACHE.get()
        if cached:
            logger.info("Veriler önbellekten (cache) getirildi.")
            # FLUTTER UYUMLU ANAHTAR: "halka_arzlar"
            return {"halka_arzlar": cached, "uyari": YATIRIM_UYARISI}

    extractor: DataExtractorV3 = app.state.extractor
    try:
        veriler = await extractor.analiz_et()
        if veriler and not debug:
            await CACHE.set(veriler)
        return {"halka_arzlar": veriler, "uyari": YATIRIM_UYARISI}
    except Exception as e:
        logger.error(f"Analiz servisi hatası: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Veri kaynağına şu anda ulaşılamıyor: {str(e)}"
        )


@app.post("/api/cache/clear")
async def clear_cache(x_debug_key: Optional[str] = Header(None, alias="X-Debug-Key")):
    if not check_debug_permission(x_debug_key):
        raise HTTPException(status_code=403, detail="Yetkisiz işlem.")
    await CACHE.invalidate()
    return {"detail": "Cache temizlendi."}


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": time.time()}


# Projeyi çalıştırmak için giriş noktası
if __name__ == "__main__":
    logger.info(f"Sunucu {SETTINGS.PORT} portunda başlatılıyor...")
    uvicorn.run("main:app", host="0.0.0.0", port=SETTINGS.PORT, reload=True)