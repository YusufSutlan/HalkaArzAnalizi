"""
═══════════════════════════════════════════════════════════════════
GEÇMİŞ ARZ VERİSİ  (kısa vade modelinin dayanağı)
═══════════════════════════════════════════════════════════════════

`veri/gecmis_arzlar.json` dosyasını okur. Dosya, `araclar/geriye_donuk_test.py`
tarafından üretilir: 2023'ten bugüne tamamlanan halka arzların gerçek
sonuçları (katılımcı sayısı, kişi başı dağıtılan tutar, tavan serisi,
ilk gün ve 20 günlük getiri).

Bu modül üç soruya cevap verir:

1. "Bu arza kaç kişi katılır?"      -> son 5 arzın gerçek katılımcı medyanı
2. "Kişi başı X TL çok mu, az mı?"  -> son 10 arzın gerçek medyanına oranı
3. "Benzer arzlar geçmişte ne yaptı?" -> aynı skor bandındaki arzların
   tavan sayısı dağılımı

NEDEN SABİT EŞİK DEĞİL: Eski model katılımcıyı 2,5 milyon, "yüksek" kişi
başı tutarı 1.200 TL varsayıyordu. 2023'te medyan katılım 1,9 milyon ve
kişi başı 390 TL idi; 2026'da 730 bin ve 1.700 TL. Sabit eşikler bir yıl
içinde anlamsızlaşıyor. Göreli ölçüler (son arzların medyanına oran) hem
enflasyona hem katılım dalgalanmasına karşı kendiliğinden ayarlanır.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

GECMIS_DOSYASI = Path(os.environ.get(
    "GECMIS_ARZ_DOSYASI",
    str(Path(__file__).resolve().parent / "veri" / "gecmis_arzlar.json")
))

# Dosya yoksa/boşsa kullanılan değerler (Eylül 2026 itibarıyla son 10 arz).
VARSAYILAN_KATILIMCI = 650_000
VARSAYILAN_KISI_BASI_TL = 2_200.0
VARSAYILAN_ARZ = 2_200_000_000.0
# Geçmiş arzların emsal çarpan medyanları (dosya yoksa).
VARSAYILAN_EMSAL = {"fk": 32.0, "pddd": 2.2, "n": 0}

# Bu kadar günden eski veri "bayat" sayılır ve çıktıda belirtilir.
BAYATLAMA_GUNU = int(os.environ.get("GECMIS_VERI_BAYATLAMA_GUNU", "150"))


def _tarih(s) -> Optional[date]:
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


class GecmisArzlar:
    def __init__(self, dosya: Optional[Path] = None, kayitlar: Optional[list] = None,
                 ozet: Optional[dict] = None):
        self.dosya = Path(dosya) if dosya else GECMIS_DOSYASI
        self.arzlar: list[dict] = []
        self.ozet: dict = {}
        self.guncelleme: Optional[date] = None
        if kayitlar is not None:
            # Test ve geriye dönük test aracı için doğrudan veriyle kurulum.
            self._yerlestir(kayitlar, ozet or {})
        else:
            self.yukle()

    def _yerlestir(self, kayitlar: list, ozet: dict) -> None:
        temiz = [k for k in kayitlar if _tarih(k.get("talep_bit"))]
        temiz.sort(key=lambda k: k["talep_bit"])
        self.arzlar = temiz
        self.ozet = ozet
        self.guncelleme = _tarih(ozet.get("guncelleme")) or (
            _tarih(temiz[-1]["talep_bit"]) if temiz else None)

    def yukle(self) -> int:
        try:
            ham = json.loads(self.dosya.read_text(encoding="utf-8"))
            self._yerlestir(ham.get("arzlar") or [],
                            {k: v for k, v in ham.items() if k != "arzlar"})
            logger.info(f"Geçmiş arz verisi yüklendi: {len(self.arzlar)} kayıt")
        except FileNotFoundError:
            logger.warning(f"Geçmiş arz dosyası yok: {self.dosya} (varsayılanlar kullanılacak)")
        except Exception as e:
            logger.warning(f"Geçmiş arz dosyası okunamadı: {e}")
        return len(self.arzlar)

    # ───────────────────────── yardımcılar ─────────────────────────

    def _onceki(self, tarih: Optional[date], alan: str, adet: int) -> list[float]:
        """`tarih`ten ÖNCE biten arzların son `adet` tanesinin `alan` değerleri."""
        sinir = (tarih or date.max).isoformat()
        deg = [k[alan] for k in self.arzlar
               if k["talep_bit"] < sinir and k.get(alan)]
        return deg[-adet:]

    def bayat_mi(self) -> tuple[bool, int]:
        if not self.guncelleme:
            return (True, 9999)
        gun = (date.today() - self.guncelleme).days
        return (gun > BAYATLAMA_GUNU, gun)

    # ───────────────────────── referanslar ─────────────────────────

    def beklenen_katilimci(self, tarih: Optional[date] = None) -> tuple[int, str]:
        """Son 5 arzın gerçek bireysel katılımcı medyanı."""
        d = self._onceki(tarih, "katilimci", 5)
        if len(d) >= 3:
            return (int(statistics.median(d)), f"son {len(d)} arzın medyanı")
        return (VARSAYILAN_KATILIMCI, "varsayılan")

    def referans_kisi_basi_tl(self, tarih: Optional[date] = None) -> tuple[float, int]:
        """Son 10 arzda kişi başına gerçekten dağıtılan tutarın medyanı."""
        d = self._onceki(tarih, "kisi_basi_tl", 10)
        if len(d) >= 5:
            return (float(statistics.median(d)), len(d))
        return (VARSAYILAN_KISI_BASI_TL, 0)

    def referans_arz(self, tarih: Optional[date] = None) -> tuple[float, int]:
        """Son 10 arzın büyüklük medyanı."""
        d = self._onceki(tarih, "arz", 10)
        if len(d) >= 5:
            return (float(statistics.median(d)), len(d))
        return (VARSAYILAN_ARZ, 0)

    def emsal_carpanlar(self) -> dict:
        """Geçmiş halka arzların F/K ve PD/DD medyanları."""
        e = self.ozet.get("emsal") or {}
        if e.get("fk") and e.get("pddd"):
            return e
        return dict(VARSAYILAN_EMSAL)

    def bist100_medyan(self) -> dict:
        return self.ozet.get("bist100_medyan") or {}

    # ───────────────────────── benzer arzlar ─────────────────────────

    def benzer_arzlar(self, skor: float, tarih: Optional[date] = None,
                      pencere: float = 12.0, en_az: int = 12) -> Optional[dict]:
        """
        Kısa vade skoru `skor`a yakın geçmiş arzların GERÇEK sonuç dağılımı.

        Bu bir tahmin değil, geçmişin özetidir: "skoru 60-75 olan 18 arzın
        medyanı 6 tavan, dörtte biri 4'ten az, %11'i hiç tavan yapmadı."
        Yeterli örnek yoksa pencere genişletilir; yine yoksa None döner.
        """
        sinir = (tarih or date.max).isoformat()
        havuz = [k for k in self.arzlar
                 if k["talep_bit"] < sinir
                 and k.get("kisa_vade_skoru") is not None
                 and k.get("tavan") is not None]
        if len(havuz) < en_az:
            return None
        g: list[dict] = []
        for p in (pencere, pencere * 1.5, pencere * 2.5):
            g = [k for k in havuz if abs(k["kisa_vade_skoru"] - skor) <= p]
            if len(g) >= en_az:
                break
        if len(g) < en_az:
            return None
        tavanlar = sorted(k["tavan"] for k in g)
        n = len(tavanlar)
        ilk_gun = [k["ilk_gun"] for k in g if k.get("ilk_gun") is not None]
        g20 = [k["getiri_20g"] for k in g if k.get("getiri_20g") is not None]
        return {
            "ornek_sayisi": n,
            "skor_araligi": [round(min(k["kisa_vade_skoru"] for k in g)),
                             round(max(k["kisa_vade_skoru"] for k in g))],
            "donem": min(k["talep_bit"] for k in g)[:7] + " – "
                     + max(k["talep_bit"] for k in g)[:7],
            "tavan_medyan": statistics.median(tavanlar),
            "tavan_alt_ceyrek": tavanlar[n // 4],
            "tavan_ust_ceyrek": tavanlar[(3 * n) // 4],
            "tavansiz_yuzde": round(100 * sum(t == 0 for t in tavanlar) / n),
            "ilk_gun_ekside_yuzde": (
                round(100 * sum(x < 0 for x in ilk_gun) / len(ilk_gun)) if ilk_gun else None),
            "getiri_20g_medyan_yuzde": (
                round(100 * statistics.median(g20)) if g20 else None),
            "getiri_20g_ekside_yuzde": (
                round(100 * sum(x < 0 for x in g20) / len(g20)) if g20 else None),
        }

    def durum_ozeti(self) -> dict:
        bayat, gun = self.bayat_mi()
        return {
            "kayit_sayisi": len(self.arzlar),
            "guncelleme": self.guncelleme.isoformat() if self.guncelleme else None,
            "bayat": bayat, "yas_gun": gun,
            "dosya_var": self.dosya.exists(),
        }
