"""
═══════════════════════════════════════════════════════════════════
FİNANSAL VERİ OKUYUCU  (sunucu tarafı — HAFİF)
═══════════════════════════════════════════════════════════════════

Bu modül, GitHub Actions'ın hazırladığı JSON dosyalarını okur.

ÖNEMLİ: Sunucu artık PDF indirmiyor, OCR yapmıyor, yapay zekaya
istek atmıyor. Tüm o ağır iş GitHub Actions'ta bir kez yapılıyor ve
sonuç depoya commit ediliyor. Burada sadece dosya okuma var —
milisaniyeler sürer.

Bu yüzden Render'da Docker'a, tesseract'a veya API anahtarına
GEREK YOKTUR.

Veri akışı:
    GitHub Actions (günde 1 kez)
      └─ PDF indir -> sayfa bul -> yapay zeka -> doğrula
           └─ veri/finansallar/{slug}.json  (depoya commit)
                └─ Render deploy ile sunucuya iner
                     └─ BU MODÜL okur (anında)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VERI_DIZINI = Path(os.environ.get(
    "FINANSAL_VERI_DIZINI",
    str(Path(__file__).resolve().parent / "veri" / "finansallar")
))

# JSON alan adı -> proje.py FinKey değeri
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
}


def slug_uret(ad: str) -> str:
    """araclar/izahname_isle.py ile AYNI mantık — dosya adları eşleşmeli."""
    n = (ad or "").replace("İ", "i").lower()
    for a, b in (("ı","i"),("ş","s"),("ğ","g"),("ü","u"),("ö","o"),("ç","c")):
        n = n.replace(a, b)
    n = re.sub(r"[^\w]+", "-", n).strip("-")
    return n[:60] or "bilinmeyen"


class FinansalDepo:
    """
    veri/finansallar/ klasöründeki JSON'ları okur ve bellekte tutar.

    Dosyalar deploy ile geldiği için çalışma sırasında değişmez;
    bir kez okunup önbelleğe alınır.
    """

    def __init__(self, dizin: Optional[Path] = None):
        self.dizin = Path(dizin) if dizin else VERI_DIZINI
        self._kayitlar: dict[str, dict] = {}
        self._yuklendi = False
        self._yukleme_zamani = 0.0

    def yukle(self, zorla: bool = False) -> int:
        if self._yuklendi and not zorla:
            return len(self._kayitlar)
        self._kayitlar = {}
        if not self.dizin.exists():
            logger.info(f"Finansal veri klasörü yok: {self.dizin}")
            self._yuklendi = True
            return 0
        for dosya in self.dizin.glob("*.json"):
            try:
                veri = json.loads(dosya.read_text(encoding="utf-8"))
                slug = veri.get("slug") or dosya.stem
                self._kayitlar[slug] = veri
            except Exception as e:
                logger.warning(f"Finansal dosya okunamadı ({dosya.name}): {e}")
        self._yuklendi = True
        self._yukleme_zamani = time.time()
        guvenilir = sum(1 for v in self._kayitlar.values() if v.get("guvenilir"))
        logger.info(
            f"Finansal veri yüklendi: {len(self._kayitlar)} dosya "
            f"({guvenilir} güvenilir)"
        )
        return len(self._kayitlar)

    def bul(self, sirket_adi: str, bist_kodu: str = "") -> Optional[dict]:
        """
        Şirket adından JSON kaydını bulur.
        Önce tam slug, sonra kısmi eşleşme denenir.
        """
        if not self._yuklendi:
            self.yukle()
        if not self._kayitlar:
            return None

        slug = slug_uret(sirket_adi)
        kayit = self._kayitlar.get(slug)
        if kayit:
            return kayit

        # Kısmi eşleşme: "quick-sigorta-a-s" ile "quick-sigorta" eşleşsin.
        # Birden fazla aday varsa eşleştirme YAPILMAZ — yanlış şirketin
        # bilançosunu göstermek, hiç göstermemekten çok daha kötü.
        adaylar = [
            v for k, v in self._kayitlar.items()
            if k.startswith(slug[:20]) or slug.startswith(k[:20])
        ]
        if len(adaylar) == 1:
            return adaylar[0]

        if bist_kodu and bist_kodu != "Belli Değil":
            kod = slug_uret(bist_kodu)
            for k, v in self._kayitlar.items():
                if k.startswith(kod):
                    return v

        # Son çare: dosyadaki "sirket_adi" alanına göre eşleştir.
        # Dosya adı (slug) ile içindeki şirket adı farklı olabilir
        # (ör. pipeline farklı bir sürümle üretmişse). Bu, veriyi
        # boşuna kaybetmemek için eklendi.
        for v in self._kayitlar.values():
            if v.get("sirket_adi") and slug_uret(v["sirket_adi"]) == slug:
                return v
        return None

    def durum_ozeti(self) -> dict:
        if not self._yuklendi:
            self.yukle()
        return {
            "dosya_sayisi": len(self._kayitlar),
            "guvenilir_sayisi": sum(
                1 for v in self._kayitlar.values() if v.get("guvenilir")),
            "dizin": str(self.dizin),
            "dizin_var": self.dizin.exists(),
        }


def kayittan_finansal_uret(kayit: dict, FinKey) -> tuple[dict, dict]:
    """
    JSON kaydını proje.py'nin beklediği biçime çevirir.

    fin     : {FinKey: deger}          -> en güncel dönem
    seriler : {FinKey: {yil: deger}}   -> yıllık büyüme için

    GÜVENLİK: Doğrulamayı geçmemiş kayıt KULLANILMAZ. Yanlış rakamla
    hesaplanmış bir skor, veri yokluğundan çok daha tehlikelidir.
    """
    if not kayit or not kayit.get("guvenilir"):
        return {}, {}

    fk_haritasi = {k.value: k for k in FinKey}
    fin: dict = {}
    seriler: dict = {}

    for alan, deger in (kayit.get("guncel") or {}).items():
        hedef = ALAN_ESLEME.get(alan)
        if hedef and hedef in fk_haritasi and deger is not None:
            try:
                fin[fk_haritasi[hedef]] = float(deger)
            except (TypeError, ValueError):
                continue

    for alan, seri in (kayit.get("seriler") or {}).items():
        hedef = ALAN_ESLEME.get(alan)
        if not hedef or hedef not in fk_haritasi:
            continue
        # Yalnızca YIL SONU (12. ay) dönemleri. Çeyrek verisini yıllıkla
        # kıyaslamak sahte büyüme/daralma üretir.
        yillik: dict[int, float] = {}
        for donem, deger in (seri or {}).items():
            m = re.fullmatch(r"(20\d{2})-(\d{2})", str(donem))
            if not m or m.group(2) != "12":
                continue
            try:
                yillik[int(m.group(1))] = float(deger)
            except (TypeError, ValueError):
                continue
        if len(yillik) >= 2:
            seriler[fk_haritasi[hedef]] = yillik

    return fin, seriler