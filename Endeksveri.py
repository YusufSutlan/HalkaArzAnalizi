"""
═══════════════════════════════════════════════════════════════════
BIST 100 ENDEKS VERİSİ  (TCMB EVDS)
═══════════════════════════════════════════════════════════════════

Kısa vade modelinin "piyasa momentumu" etkeni (BIST 100'ün son 60 işlem
günündeki getirisi) buradan gelir. Hem sunucu (proje.py) hem geriye dönük
test aracı (araclar/geriye_donuk_test.py) bu modülü kullanır.

Kaynak: Türkiye Cumhuriyet Merkez Bankası Elektronik Veri Dağıtım Sistemi
(EVDS), seri TP.MK.F.BILESIK (BIST 100 endeksi, günlük kapanış).
Resmi ve ücretsiz bir servistir; kullanıcıya özel API anahtarı gerektirir.

API ANAHTARI KODA VE DEPOYA YAZILMAZ. `EVDS_API_KEY` ortam değişkeninden
okunur:
  - Render:          Dashboard > servis > Environment > EVDS_API_KEY
  - GitHub Actions:  Settings > Secrets and variables > Actions > EVDS_API_KEY
  - Yerel:           export EVDS_API_KEY=...

Anahtar yoksa veya servis yanıt vermezse momentum `None` olur; model bu
etkeni nötr sayar ve çıktıda "bilinmiyor" olarak gösterir.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

EVDS_ADRES = os.environ.get("EVDS_URL", "https://evds3.tcmb.gov.tr/igmevdsms-dis/")
EVDS_SERI = os.environ.get("EVDS_BIST100_SERI", "TP.MK.F.BILESIK")
# Servis istek başına ~1000 satır döndürüyor; uzun aralıklar parçalanır.
_PARCA_GUN = 365
MOMENTUM_GUN = 60


class EndeksHatasi(Exception):
    """Endeks verisi alınamadı (anahtar yok, yetki yok, servis hatası)."""


def anahtar_var_mi() -> bool:
    return bool(os.environ.get("EVDS_API_KEY", "").strip())


def _istek(bas: dt.date, bit: dt.date, zaman_asimi: float) -> list[tuple[dt.date, float]]:
    anahtar = os.environ.get("EVDS_API_KEY", "").strip()
    if not anahtar:
        raise EndeksHatasi("EVDS_API_KEY ortam değişkeni tanımlı değil")
    url = (f"{EVDS_ADRES}series={EVDS_SERI}"
           f"&startDate={bas:%d-%m-%Y}&endDate={bit:%d-%m-%Y}&type=json")
    istek = urllib.request.Request(url, headers={
        "key": anahtar,                         # anahtar başlıkta gider, URL'de değil
        "User-Agent": "halka-arz-analiz/4 (+https://stltechnology.com.tr)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:
            govde = yanit.read()
            tur = yanit.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise EndeksHatasi(f"EVDS yetki hatası (HTTP {e.code}); anahtar geçersiz olabilir") from None
        raise EndeksHatasi(f"EVDS HTTP {e.code}") from None
    except Exception as e:
        raise EndeksHatasi(f"EVDS bağlantı hatası: {type(e).__name__}") from None
    if "json" not in tur:
        raise EndeksHatasi("EVDS beklenmeyen yanıt (JSON değil); servis adresi değişmiş olabilir")
    alan = EVDS_SERI.replace(".", "_")
    sonuc = []
    for satir in json.loads(govde).get("items") or []:
        deger = satir.get(alan)
        if deger in (None, "", "null"):
            continue                            # tatil günleri boş gelir
        try:
            sonuc.append((dt.datetime.strptime(satir["Tarih"], "%d-%m-%Y").date(), float(deger)))
        except (KeyError, ValueError, TypeError):
            continue
    return sonuc


def kapanislar(bas: dt.date, bit: Optional[dt.date] = None,
               zaman_asimi: float = 30.0) -> list[tuple[dt.date, float]]:
    """[bas, bit] aralığındaki günlük kapanışlar (tarih sıralı, tekrarsız)."""
    bit = bit or dt.date.today()
    hepsi: dict[dt.date, float] = {}
    parca_bas = bas
    while parca_bas <= bit:
        parca_bit = min(bit, parca_bas + dt.timedelta(days=_PARCA_GUN))
        for gun, deger in _istek(parca_bas, parca_bit, zaman_asimi):
            hepsi[gun] = deger
        parca_bas = parca_bit + dt.timedelta(days=1)
    return sorted(hepsi.items())


def momentum(seri: list[tuple[dt.date, float]], gun: Optional[dt.date] = None,
             n: int = MOMENTUM_GUN) -> Optional[float]:
    """
    `gun`den ÖNCEKİ son kapanışın, ondan n işlem günü önceki kapanışa göre
    getirisi. `gun` verilmezse serideki son kapanış kullanılır.
    """
    if gun is not None:
        seri = [s for s in seri if s[0] < gun]
    if len(seri) <= n or seri[-1 - n][1] <= 0:
        return None
    deger = seri[-1][1] / seri[-1 - n][1] - 1
    return deger if -0.6 < deger < 2.0 else None      # bariz veri hatası


def guncel_momentum(zaman_asimi: float = 15.0) -> tuple[Optional[float], Optional[dt.date], str]:
    """
    (momentum, son kapanış tarihi, durum) döndürür. Hata fırlatmaz.
    60 işlem günü için ~150 takvim günü yeterli.
    """
    try:
        seri = kapanislar(dt.date.today() - dt.timedelta(days=150), zaman_asimi=zaman_asimi)
    except EndeksHatasi as e:
        return (None, None, str(e))
    m = momentum(seri)
    if m is None:
        return (None, seri[-1][0] if seri else None, "yetersiz veri")
    return (m, seri[-1][0], "ok")
