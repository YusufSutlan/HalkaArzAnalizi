# scoring.py
import re
import math
from typing import List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from config import InfoKey, FinKey
from utils import TextUtils


@dataclass
class ScoreResultV3:
    score: float
    max_possible: float
    explanations: List[str]

    @property
    def stars(self) -> str:
        return TextUtils.yildiz_uret(self.score, self.max_possible)

    @property
    def label(self) -> str:
        return f"{self.score:.1f}/{self.max_possible} {self.stars}"


class ScoreAnalyzerV3:
    def __init__(self):
        self.SEKTORLER = {
            "TEKNOLOJİ": {"fk": 18.0, "pddd": 4.5, "fd_favok": 12.0},
            "ENERJİ":    {"fk": 14.0, "pddd": 2.5, "fd_favok": 9.0},
            "SANAYİ":    {"fk": 10.0, "pddd": 1.8, "fd_favok": 7.0},
            "GIDA":      {"fk": 12.0, "pddd": 2.0, "fd_favok": 8.0},
            "GYO":       {"fk": 5.0,  "pddd": 0.8, "fd_favok": 5.0},
            "FİNANS":    {"fk": 6.0,  "pddd": 1.2, "fd_favok": 0.0},
            "GENEL":     {"fk": 12.0, "pddd": 2.0, "fd_favok": 8.0}
        }

    def _get_sektor(self, metin: str) -> str:
        m = metin.lower()
        if any(k in m for k in ["yazılım", "teknoloji", "bilişim", "savunma"]):
            return "TEKNOLOJİ"
        if any(k in m for k in ["enerji", "elektrik", "yenilenebilir"]):
            return "ENERJİ"
        if any(k in m for k in ["çimento", "demir", "sanayi", "üretim", "makine"]):
            return "SANAYİ"
        if any(k in m for k in ["gıda", "tarım", "hayvancılık"]):
            return "GIDA"
        if any(k in m for k in ["gayrimenkul", "gyo"]):
            return "GYO"
        if any(k in m for k in ["finans", "sigorta", "banka", "faktoring"]):
            return "FİNANS"
        return "GENEL"

    def _trend_hesapla(self, veriler: List[float]) -> float:
        if len(veriler) < 2:
            return 0.0
        büyümeler = []
        for i in range(1, len(veriler)):
            if veriler[i - 1] > 0:
                buyume = ((veriler[i] - veriler[i - 1]) / veriler[i - 1]) * 100
                büyümeler.append(buyume)
        return sum(büyümeler) / len(büyümeler) if büyümeler else 0.0

    def finansal_analiz(self, fin: Dict[FinKey, List[float]]) -> ScoreResultV3:
        puan, max_puan, aciklamalar = 0.0, 50.0, []
        karlar = fin.get(FinKey.NET_KAR, [])
        hasilatlar = fin.get(FinKey.HASILAT, [])
        ozk = fin.get(FinKey.OZKAYNAK, [])
        favok = fin.get(FinKey.FAVOK, [])
        borc = fin.get(FinKey.TOPLAM_BORC, [])
        nakit_akisi = fin.get(FinKey.NAKIT_AKISI, [])

        toplam_varlik = None
        if ozk and borc and ozk[-1] is not None and borc[-1] is not None:
            toplam_varlik = ozk[-1] + borc[-1]

        if karlar and karlar[-1] > 0:
            trend_kar = self._trend_hesapla(karlar)
            if trend_kar > 40:
                puan += 5.0
                aciklamalar.append(f"Güçlü yıllık kâr büyümesi (Ort: %{trend_kar:.1f}).")
            elif trend_kar > 10:
                puan += 3.0
                aciklamalar.append(f"İstikrarlı kâr artışı (Ort: %{trend_kar:.1f}).")

        if hasilatlar and hasilatlar[-1] > 0:
            trend_has = self._trend_hesapla(hasilatlar)
            if trend_has > 40:
                puan += 5.0
                aciklamalar.append(f"Yüksek ciro büyümesi (Ort: %{trend_has:.1f}).")
            elif trend_has > 15:
                puan += 3.0
                aciklamalar.append(f"Makul ciro büyümesi (Ort: %{trend_has:.1f}).")

        if karlar and hasilatlar and hasilatlar[-1] > 0:
            nk_marji = (karlar[-1] / hasilatlar[-1]) * 100
            if nk_marji >= 20:
                puan += 5.0
                aciklamalar.append(f"Mükemmel Net Kâr Marjı (%{nk_marji:.1f}).")
            elif nk_marji >= 10:
                puan += 3.0
                aciklamalar.append(f"İyi Net Kâr Marjı (%{nk_marji:.1f}).")
            elif nk_marji >= 5:
                puan += 2.0
                aciklamalar.append(f"Düşük/Kabul edilebilir Net Kâr Marjı (%{nk_marji:.1f}).")

        if favok and hasilatlar and hasilatlar[-1] > 0:
            favok_marji = (favok[-1] / hasilatlar[-1]) * 100
            if favok_marji >= 25:
                puan += 7.0
                aciklamalar.append(f"Çok güçlü FAVÖK Marjı (%{favok_marji:.1f}).")
            elif favok_marji >= 10:
                puan += 4.0
                aciklamalar.append(f"Sağlıklı FAVÖK Marjı (%{favok_marji:.1f}).")

        if borc and favok and favok[-1] > 0:
            borc_favok = borc[-1] / favok[-1]
            if borc_favok < 2:
                puan += 6.0
                aciklamalar.append(f"Borç çevirme kapasitesi çok iyi (Borç/FAVÖK: {borc_favok:.1f}).")
            elif borc_favok < 4:
                puan += 3.0
                aciklamalar.append(f"Yönetilebilir Borç/FAVÖK ({borc_favok:.1f}).")
            else:
                aciklamalar.append(f"Yüksek Borç/FAVÖK oranı! ({borc_favok:.1f}).")

        if borc and ozk and ozk[-1] > 0:
            kaldirac = borc[-1] / ozk[-1]
            if kaldirac < 0.5:
                puan += 4.0

        if karlar and ozk and ozk[-1] > 0:
            roe = (karlar[-1] / ozk[-1]) * 100
            if roe > 30:
                puan += 6.0
                aciklamalar.append(f"Güçlü ROE (%{roe:.1f}).")
            elif roe > 15:
                puan += 3.0

        if karlar and toplam_varlik and toplam_varlik > 0:
            roa = (karlar[-1] / toplam_varlik) * 100
            if roa > 10:
                puan += 4.0
                aciklamalar.append(f"Aktif Kârlılığı (ROA) verimli (%{roa:.1f}).")

        if nakit_akisi:
            if nakit_akisi[-1] > 0:
                puan += 3.0
                aciklamalar.append("Pozitif İşletme Nakit Akışı.")
            else:
                aciklamalar.append("Negatif Nakit Akışı uyarı veriyor.")
                puan -= 2.0

        if not karlar and not hasilatlar:
            return ScoreResultV3(0, max_puan, ["Finansal veriler yetersiz veya gizlenmiş."])

        return ScoreResultV3(min(round(puan, 1), max_puan), max_puan, aciklamalar)

    def degerleme_analizi(
        self,
        fiyat: float,
        pay: float,
        veri: Dict[InfoKey, str],
        fin: Dict[FinKey, List[float]],
        sektor: str
    ) -> ScoreResultV3:
        puan, max_puan, aciklamalar = 0.0, 30.0, []
        if not fiyat or not pay:
            return ScoreResultV3(0, max_puan, ["Eksik veri, değerleme yapılamadı."])

        market_cap = fiyat * pay
        ort = self.SEKTORLER.get(sektor, self.SEKTORLER["GENEL"])

        iskonto = TextUtils.yuzde_bul(veri.get(InfoKey.ISKONTO, ""))
        if iskonto:
            if iskonto >= 30:
                puan += 6.0
                aciklamalar.append(f"Çok cazip halka arz iskontosu (%{iskonto}).")
            elif iskonto >= 20:
                puan += 4.0
                aciklamalar.append(f"Makul iskonto (%{iskonto}).")

        karlar = fin.get(FinKey.NET_KAR, [])
        if karlar and karlar[-1] > 0:
            fk = market_cap / karlar[-1]
            if fk < ort["fk"] * 0.8:
                puan += 8.0
                aciklamalar.append(f"İskontolu F/K ({fk:.1f}).")

            trend_kar = self._trend_hesapla(karlar)
            if trend_kar > 0:
                peg = fk / trend_kar
                if peg < 1.0:
                    puan += 4.0
                    aciklamalar.append(f"Büyümeye göre ucuz (PEG: {peg:.2f}).")

        favok = fin.get(FinKey.FAVOK, [])
        borc = fin.get(FinKey.TOPLAM_BORC, [])
        if favok and favok[-1] > 0:
            net_borc = borc[-1] if borc and borc[-1] > 0 else 0
            ev = market_cap + net_borc
            fd_favok = ev / favok[-1]
            if fd_favok < ort["fd_favok"] * 0.8:
                puan += 8.0
                aciklamalar.append(f"Cazip FD/FAVÖK ({fd_favok:.1f}).")

        ozk = fin.get(FinKey.OZKAYNAK, [])
        if ozk and ozk[-1] > 0:
            pddd = market_cap / ozk[-1]
            if pddd < ort["pddd"] * 0.9:
                puan += 4.0
                aciklamalar.append(f"Makul PD/DD oranı ({pddd:.2f}).")

        return ScoreResultV3(min(round(puan, 1), max_puan), max_puan, aciklamalar)

    def yapi_analizi(self, veri: Dict[InfoKey, str]) -> ScoreResultV3:
        puan, max_puan, aciklamalar = 0.0, 15.0, []
        m_lower = veri.get(InfoKey.HALKA_ARZ_SEKLI, "").lower()
        konsorsiyum = veri.get(InfoKey.ARACI_KURUM, "").lower()

        if "sermaye artırımı" in m_lower and "ortak satış" not in m_lower:
            puan += 6.0
            aciklamalar.append("Tüm gelir şirketin kasasına giriyor.")

        ist = veri.get(InfoKey.FIYAT_ISTIKRARI, "").lower()
        if "planlanmaktadır" in ist or "gün" in ist:
            puan += 3.0

        taahhut = veri.get(InfoKey.TAAHHUT, "").lower()
        if "540 gün" in taahhut:
            puan += 4.0
            aciklamalar.append("Çok uzun lock-up süresi (540 gün).")
        elif "365 gün" in taahhut or "1 yıl" in taahhut:
            puan += 3.0
            aciklamalar.append("Standart lock-up süresi (1 Yıl).")
        elif "180 gün" in taahhut or "6 ay" in taahhut:
            puan += 1.5

        if any(k in konsorsiyum for k in ["garanti", "iş ", "ak ", "yapı kredi", "oyak"]):
            puan += 2.0
            aciklamalar.append("Güçlü konsorsiyum lideri.")

        return ScoreResultV3(min(round(puan, 1), max_puan), max_puan, aciklamalar)

    def fon_kullanimi_analizi(self, metin: str) -> ScoreResultV3:
        puan, max_puan, aciklamalar = 0.0, 10.0, []
        if not metin or metin in ("-", "açıklanmadı", ""):
            return ScoreResultV3(0, max_puan, ["Fon kullanımı belirsiz."])

        m_lower = metin.lower()

        yatirim_orani = sum([
            float(x)
            for x in re.findall(r"%(\d+)[^%]*?(?:yatırım|tesis|kapasite|fabrika|makine|ar-ge)", m_lower)
        ])
        borc_orani = sum([
            float(x)
            for x in re.findall(r"%(\d+)[^%]*?(?:borç|finansman|kredi kapatma)", m_lower)
        ])
        isletme_orani = sum([
            float(x)
            for x in re.findall(r"%(\d+)[^%]*?(?:işletme sermayesi)", m_lower)
        ])

        if yatirim_orani > 0:
            puan += (yatirim_orani * 0.1)
            aciklamalar.append(f"%{yatirim_orani} Yatırım / Büyüme hedefli.")
        if isletme_orani > 0:
            puan += (isletme_orani * 0.05)
            aciklamalar.append(f"%{isletme_orani} İşletme Sermayesi.")
        if borc_orani > 0:
            puan -= (borc_orani * 0.1)
            aciklamalar.append(f"%{borc_orani} Borç Kapatma (Negatif).")

        if puan == 0.0 and re.search(r"yatırım|tesis", m_lower):
            puan = 4.0
            aciklamalar.append("Yatırım hedefleri var (Oran belirtilmemiş).")

        return ScoreResultV3(max(0.0, min(puan, max_puan)), max_puan, aciklamalar)

    def sirket_kalitesi_analizi(self, raw_text: str) -> ScoreResultV3:
        puan, max_puan, aciklamalar = 0.0, 15.0, []
        m = raw_text.lower()

        if any(k in m for k in ["pazar lideri", "türkiye'nin en büyük", "sektör lideri"]):
            puan += 4.0
            aciklamalar.append("Sektöründe pazar lideri/öncü konumda.")

        ihracat_ulke = re.search(r'(\d+)\s+ülkeye ihracat', m)
        if ihracat_ulke and int(ihracat_ulke.group(1)) >= 50:
            puan += 3.0
            aciklamalar.append(f"Global pazara erişim ({ihracat_ulke.group(1)} ülke).")

        patent_sayisi = re.search(r'(\d+)\s+patent', m)
        if patent_sayisi:
            ps = int(patent_sayisi.group(1))
            if ps > 20:
                puan += 4.0
                aciklamalar.append(f"Güçlü fikri mülkiyet ({ps} patent).")
            elif ps > 0:
                puan += 2.0
                aciklamalar.append("Tescilli patentleri bulunuyor.")

        if "%" in m and "ar-ge" in m:
            puan += 3.0
            aciklamalar.append("Bütçeden Ar-Ge'ye aktif pay ayrılıyor.")

        if any(k in m for k in ["iso ", "esg", "sürdürülebilirlik"]):
            puan += 1.0

        return ScoreResultV3(min(puan, max_puan), max_puan, aciklamalar)

    def risk_motoru_hesapla(
        self,
        fiyat: float,
        pay: float,
        fin: Dict[FinKey, List[float]],
        veri: Dict[InfoKey, str],
        raw_text: str
    ) -> Dict:
        fin_risk = lik_risk = kurum_risk = arz_risk = piyasa_risk = sektor_risk = 0.0

        ozk = fin.get(FinKey.OZKAYNAK, [])
        karlar = fin.get(FinKey.NET_KAR, [])
        borc = fin.get(FinKey.TOPLAM_BORC, [])
        if not karlar:
            fin_risk += 20.0
        elif karlar[-1] < 0:
            fin_risk += 15.0
        if borc and ozk and ozk[-1] > 0 and (borc[-1] / ozk[-1]) > 2.0:
            fin_risk += 10.0

        # YENİ EKLENDİ: Milyar/Milyon destekli büyüklük hesabı
        buyukluk_str = veri.get(InfoKey.BUYUKLUK, "").lower()
        if fiyat and pay:
            arz_buyuklugu = fiyat * pay
        else:
            arz_buyuklugu = TextUtils.sayi_bul(buyukluk_str) or 0
            if "milyar" in buyukluk_str and arz_buyuklugu < 1_000_000:
                arz_buyuklugu *= 1_000_000_000
            elif "milyon" in buyukluk_str and arz_buyuklugu < 1_000_000:
                arz_buyuklugu *= 1_000_000

        if arz_buyuklugu > 3_000_000_000:
            lik_risk += 12.0
            
        nakit_akisi = fin.get(FinKey.NAKIT_AKISI, [])
        if nakit_akisi and nakit_akisi[-1] < 0:
            lik_risk += 8.0

        if "bağımsız denetim" not in raw_text.lower():
            kurum_risk += 10.0
        if "yönetim kurulu" not in raw_text.lower():
            kurum_risk += 5.0

        h_sekli = veri.get(InfoKey.HALKA_ARZ_SEKLI, "").lower()
        if "ortak satışı" in h_sekli and "sermaye artırımı" not in h_sekli:
            arz_risk += 15.0
        elif "ortak satışı" in h_sekli:
            arz_risk += 5.0

        piyasa_risk += 5.0
        sektor = self._get_sektor(raw_text)
        if sektor in ["GYO", "GENEL"]:
            sektor_risk += 7.0

        toplam_risk = min(100.0, fin_risk + lik_risk + kurum_risk + arz_risk + piyasa_risk + sektor_risk)

        return {
            "finansal_risk": fin_risk,
            "likidite_riski": lik_risk,
            "kurumsal_risk": kurum_risk,
            "arz_riski": arz_risk,
            "piyasa_riski": piyasa_risk,
            "sektor_riski": sektor_risk,
            "toplam_risk": round(toplam_risk, 1),
            "seviye": (
                "Kritik Risk" if toplam_risk > 75
                else "Yüksek" if toplam_risk > 50
                else "Orta" if toplam_risk > 30
                else "Düşük (Güvenli)"
            )
        }

    def talep_endeksi_hesapla(
        self,
        fiyat: float,
        pay: float,
        veri: Dict[InfoKey, str],
        raw_text: str,
        aktif_arz_sayisi: int
    ) -> Dict:
        # YENİ EKLENDİ: Başlangıç Puanı 50'ye Çıkarıldı ve Hesaplama Yumuşatıldı
        endeks, aciklamalar = 50.0, []
        
        buyukluk_str = veri.get(InfoKey.BUYUKLUK, "").lower()
        if fiyat and pay:
            buyukluk = fiyat * pay
        else:
            buyukluk = TextUtils.sayi_bul(buyukluk_str) or 1.0
            if "milyar" in buyukluk_str and buyukluk < 1_000_000:
                buyukluk *= 1_000_000_000
            elif "milyon" in buyukluk_str and buyukluk < 1_000_000:
                buyukluk *= 1_000_000

        if buyukluk > 1_000_000:
            log_score = max(0, 30 - (math.log10(buyukluk) - 7) * 10)
            endeks += log_score
            aciklamalar.append(f"Tahta Büyüklüğü Puanı: +{log_score:.1f}")

        tahsisat = veri.get(InfoKey.TAHSISAT, "").lower()
        kurumsal_oran = sum([float(x) for x in re.findall(r"%(\d+)[^%]*?kurumsal", tahsisat)])
        if kurumsal_oran > 40:
            endeks += 15
            aciklamalar.append(f"Yüksek Kurumsal Tahsisat (%{kurumsal_oran}). Tahta sahipsiz kalmaz.")
        elif kurumsal_oran > 0:
            endeks += 5

        # YENİ EKLENDİ: Arz Sayısı Cezası Yumuşatıldı (-15 yerine -10)
        if aktif_arz_sayisi > 2:
            endeks -= 10
            aciklamalar.append(f"Talep Bölünmesi: Aynı hafta {aktif_arz_sayisi} arz var (-).")
        elif aktif_arz_sayisi == 1:
            endeks += 10
            aciklamalar.append("Haftanın tek halka arzı, likidite odaklanacak (+).")

        iskonto = TextUtils.yuzde_bul(veri.get(InfoKey.ISKONTO, ""))
        if iskonto and iskonto >= 25.0:
            endeks += 15

        endeks = max(0.0, min(100.0, endeks))
        return {
            "skor": round(endeks, 1),
            "aciklamalar": aciklamalar,
            "seviye": (
                "Aşırı Talep (Güçlü Tavan)" if endeks > 75
                else "Yüksek Talep" if endeks > 55
                else "Dengeli" if endeks > 40
                else "Zayıf Talep"
            )
        }

    def kalite_endeksi(
        self,
        fin: Dict[FinKey, List[float]],
        veri: Dict[InfoKey, str],
        raw_text: str
    ) -> float:
        skor = 20.0

        karlar = fin.get(FinKey.NET_KAR, [])
        if karlar and karlar[-1] > 0:
            skor += 10
            if self._trend_hesapla(karlar) > 20:
                skor += 10
        favok = fin.get(FinKey.FAVOK, [])
        if favok and favok[-1] > 0:
            skor += 10

        m = raw_text.lower()
        if "ihracat" in m:
            skor += 10
        if "patent" in m:
            skor += 10
        if any(k in m for k in ["temettü", "kâr payı"]):
            skor += 10

        yillar = [int(y) for y in re.findall(r"((?:18|19|20)\d{2})\s*yılında\s*kurul", m)]
        if yillar and (datetime.now().year - min(yillar)) >= 20:
            skor += 10

        ozk = fin.get(FinKey.OZKAYNAK, [])
        borc = fin.get(FinKey.TOPLAM_BORC, [])
        if borc and ozk and ozk[-1] > 0 and (borc[-1] / ozk[-1]) > 2:
            skor -= 15

        return max(0.0, min(100.0, skor))

    def guven_skoru(
        self,
        fin: Dict[FinKey, List[float]],
        veri: Dict[InfoKey, str],
        raw_text: str
    ) -> Dict:
        
        def _veri_gecerli_mi(deger: Any) -> bool:
            bos_degerler = ["Açıklanmadı", "Açıklanmadı.", "-", "Belli Değil", "", "Henüz açıklanmadı."]
            return bool(deger and str(deger).strip() not in bos_degerler)

        beklenen_veri = [
            FinKey.NET_KAR in fin,
            FinKey.HASILAT in fin,
            FinKey.FAVOK in fin,
            FinKey.OZKAYNAK in fin,
            FinKey.TOPLAM_BORC in fin,
            _veri_gecerli_mi(veri.get(InfoKey.FIYAT)),
            _veri_gecerli_mi(veri.get(InfoKey.PAY_SAYISI)),
            _veri_gecerli_mi(veri.get(InfoKey.ISKONTO)),
            _veri_gecerli_mi(veri.get(InfoKey.FON_KULLANIM))
        ]

        bulunan = sum(1 for v in beklenen_veri if v)
        toplam = len(beklenen_veri)
        oran = (bulunan / toplam) * 100

        aciklamalar = []
        if FinKey.FAVOK not in fin:
            aciklamalar.append("Eksik: FAVÖK verisi bulunamadı.")
        if not _veri_gecerli_mi(veri.get(InfoKey.ISKONTO)):
            aciklamalar.append("Eksik: İskonto oranı belirtilmemiş.")
        if not fin:
            aciklamalar.append("Kritik: Finansal tablolar okunamadı.")

        return {
            "skor": round(oran, 1),
            "aciklamalar": aciklamalar,
            "mesaj": f"Analiz Güveni: %{oran:.1f}"
        }