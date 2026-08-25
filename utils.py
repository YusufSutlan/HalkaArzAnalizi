# utils.py
import re
from typing import Optional, List


class TextUtils:
    @staticmethod
    def normalize(s: Optional[str]) -> str:
        s = (s or "").replace("İ", "i").replace("I", "ı")
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
        liste = TextUtils.sayilari_bul(metin)
        return liste[-1] if liste else None

    @staticmethod
    def sayilari_bul(metin: Optional[str]) -> List[float]:
        if not metin:
            return []
        temiz = re.sub(r'(?<=\d)\.(?=\d{3})', '', metin)
        temiz = re.sub(r'(?<=\d)\s(?=\d{3})', '', temiz)
        desenler = re.findall(r"-?\d+(?:[.,]\d+)?", temiz)
        sonuclar = []
        for d in desenler:
            try:
                sonuclar.append(float(d.replace(",", ".")))
            except Exception:
                pass
        return sonuclar

    @staticmethod
    def yildiz_uret(skor: float, max_skor: float) -> str:
        yuzde = (skor / max_skor * 100) if max_skor else 0
        if yuzde >= 85:
            return "★★★★★"
        elif yuzde >= 70:
            return "★★★★☆"
        elif yuzde >= 55:
            return "★★★☆☆"
        elif yuzde >= 40:
            return "★★☆☆☆"
        elif yuzde >= 20:
            return "★☆☆☆☆"
        else:
            return "☆☆☆☆☆"

    @staticmethod
    def etiket_eslesir(baslik_norm: str, etiketler: list[str]) -> bool:
        if not baslik_norm:
            return False
        for e in etiketler:
            if baslik_norm == e or e in baslik_norm:
                return True
            if re.search(rf"(?:^|\s){re.escape(e)}(?:$|\s)", baslik_norm):
                return True
        return False