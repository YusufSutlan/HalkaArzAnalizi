# scraper.py
import asyncio
import re
from typing import Optional, List, Dict
from datetime import datetime, date
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from config import SETTINGS, InfoKey, FinKey, ArzDurumu
from utils import TextUtils
from scoring import ScoreAnalyzerV3


class DataExtractorV3:
    def __init__(self, analyzer: Optional[ScoreAnalyzerV3] = None):
        self.analyzer = analyzer or ScoreAnalyzerV3()
        self.session: Optional[AsyncSession] = None
        self._session_lock = asyncio.Lock()

        self.FIELD_LABELS: dict[InfoKey, list[str]] = {
            InfoKey.BIST_KODU: ["bist kodu"],
            InfoKey.TARIH: ["talep toplama", "halka arz tarihi", "tarih"],
            InfoKey.FIYAT: ["fiyatı", "halka arz fiyatı"],
            InfoKey.BUYUKLUK: ["büyüklüğ", "arz büyüklüğü"],
            InfoKey.ISLEM_TARIHI: ["işlem tari", "borsada işlem"],
            InfoKey.ACIKLIK: ["açıklık"],
            InfoKey.ISKONTO: ["iskonto"],
            InfoKey.TAAHHUT: ["taahhüt", "satmama"],
            InfoKey.HALKA_ARZ_SEKLI: ["şekli"],
            InfoKey.FON_KULLANIM: ["fon", "kullanım yeri"],
            InfoKey.SATIS_YONTEMI: ["satış"],
            InfoKey.FIYAT_ISTIKRARI: ["istikrar"],
            # TOPLAM PAY MİKTARI İÇİN EN GENİŞ ETİKET LİSTESİ
            InfoKey.PAY_SAYISI: [
                "pay miktar", "halka arz edilecek pay", "dağıtılacak", 
                "toplam pay", "lot", "nominal", "sermaye artırımı", "pay"
            ],
            InfoKey.DAGITIM_YONTEMI: ["dağıtım"],
            InfoKey.ARACI_KURUM: ["aracı kur", "konsorsiyum"],
            InfoKey.PAZAR: ["pazar"],
        }
        self.TUM_ETIKETLER = {e for etiketler in self.FIELD_LABELS.values() for e in etiketler}
        
        self.FIN_LABELS: dict[FinKey, list[str]] = {
            FinKey.NET_KAR: ["net dönem", "net kar"],
            FinKey.OZKAYNAK: ["özkaynak"],
            FinKey.DONEN_VARLIK: ["dönen varlık"],
            FinKey.KISA_VADELI_YUKUMLULUK: ["kısa vadeli"],
            FinKey.TOPLAM_BORC: ["toplam yükümlülük", "finansal borç"],
            FinKey.HASILAT: ["hasılat", "satış"],
            FinKey.FAVOK: ["favök", "fvaök", "brüt kar"], 
            FinKey.NAKIT_AKISI: ["işletme faaliy"]
        }

        self.DEFAULTS: dict[InfoKey, str] = {k: "Açıklanmadı" for k in self.FIELD_LABELS}
        self.DEFAULTS.update({
            InfoKey.BIST_KODU: "Belli Değil",
            InfoKey.HALKA_ARZ_SEKLI: "-",
            InfoKey.FON_KULLANIM: "Açıklanmadı.",
            InfoKey.SATIS_YONTEMI: "-",
            InfoKey.FIYAT_ISTIKRARI: "-",
            InfoKey.FINANSAL_TABLO: "Açıklanmadı.",
            InfoKey.TAHSISAT: "Henüz açıklanmadı.",
            InfoKey.DAGITIM_TABLOSU: "Lot tablosu bulunamadı.",
            InfoKey.DAGITIM_TIPI: "Tahmini Lot Dağıtımı",
        })

    async def _get_session(self) -> AsyncSession:
        if self.session is None or getattr(self.session, "closed", True):
            async with self._session_lock:
                if self.session is None or getattr(self.session, "closed", True):
                    self.session = AsyncSession(impersonate="chrome")
        return self.session

    async def _fetch_url(self, url: str) -> Optional[str]:
        session = await self._get_session()
        for i in range(SETTINGS.MAX_RETRY):
            try:
                res = await session.get(url, timeout=SETTINGS.TIMEOUT)
                if res.status_code == 200: 
                    return res.text
            except Exception:
                pass
            await asyncio.sleep(1)
        return None

    def _tablodan_bilgi_al(self, veri: dict, soup: BeautifulSoup):
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["th", "td"])
            if len(tds) < 2: 
                continue
            
            baslik = TextUtils.normalize(tds[0].get_text(strip=True))
            deger = tds[1].get_text(separator=" ", strip=True)
            
            for alan, etiketler in self.FIELD_LABELS.items():
                if veri.get(alan) == self.DEFAULTS.get(alan) or veri.get(alan) is None:
                    if any(e in baslik for e in etiketler):
                        veri[alan] = deger
                        break

    def _satirdan_deger_al(self, lines: list[str], normalized_lines: list[str], i: int) -> str:
        orijinal = lines[i]
        if ":" in orijinal:
            sonraki = orijinal.split(":", 1)[1].strip()
            if sonraki: 
                return sonraki
        
        toplanan = []
        for j in range(i + 1, min(i + 9, len(lines))):
            satir = lines[j].strip()
            if not satir: 
                break
            if normalized_lines[j] in self.TUM_ETIKETLER: 
                break
            toplanan.append(satir.lstrip("-•").strip())
            
        return "\n".join(t for t in toplanan if t)

    def _satirlardan_doldur(self, veri: dict, raw_text: str):
        lines = [l.strip() for l in raw_text.split("\n")]
        normalized_lines = [TextUtils.normalize(l) for l in lines]
        
        for alan, etiketler in self.FIELD_LABELS.items():
            if veri.get(alan) != self.DEFAULTS.get(alan): 
                continue
            
            for i, nl in enumerate(normalized_lines):
                if any(nl.startswith(e) for e in etiketler) or any(e in nl for e in etiketler):
                    deger = self._satirdan_deger_al(lines, normalized_lines, i)
                    if deger: 
                        veri[alan] = deger
                    break

    def _dagitim_tahsisat_doldur(self, veri: dict, raw_text: str):
        lines = raw_text.split("\n")
        lot_baslik = None
        if "Dağıtılan Pay Miktarı" in raw_text: 
            lot_baslik = "Dağıtılan Pay Miktarı"
        elif "Dağıtılacak Pay Miktarı" in raw_text: 
            lot_baslik = "Dağıtılacak Pay Miktarı"

        if lot_baslik:
            veri[InfoKey.DAGITIM_TIPI] = "Dağıtılan Pay Miktarı (Kesin Sonuç)" if "Dağıtılan" in lot_baslik else "Tahmini Lot Dağıtımı"
            try:
                bolunmus = raw_text.split(lot_baslik)[1]
                lot_lines = [
                    "• " + s.replace("-", "").strip() for s in bolunmus.split("\n")[1:20] 
                    if ("katılım" in s.lower() or "lot" in s.lower() or "kişi" in s.lower()) and len(s.replace("-", "").strip()) > 4
                ]
                if lot_lines: 
                    veri[InfoKey.DAGITIM_TABLOSU] = "\n".join(lot_lines)
            except Exception: 
                pass

        for i, line in enumerate(lines):
            nl = TextUtils.normalize(line)
            if nl == "tahsisat grupları" and veri.get(InfoKey.TAHSISAT) == self.DEFAULTS.get(InfoKey.TAHSISAT):
                t_list = [
                    "• " + lines[i + j].replace("-", "").strip() for j in range(1, 6) 
                    if i + j < len(lines) and ("%" in lines[i + j] or "Lot" in lines[i + j])
                ]
                if t_list: 
                    veri[InfoKey.TAHSISAT] = "\n".join(t_list)

    def _tablodan_finansal_al(self, soup: BeautifulSoup) -> Dict[FinKey, List[float]]:
        fin_data = {}
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["th", "td"])
            if len(tds) < 2: 
                continue
            
            baslik = TextUtils.normalize(tds[0].get_text(strip=True))
            tum_hucreler = " ".join([td.get_text(strip=True) for td in tds[1:]])
            sayilar = TextUtils.sayilari_bul(tum_hucreler)
            
            for alan, etiketler in self.FIN_LABELS.items():
                if alan not in fin_data:
                    if any(e in baslik for e in etiketler):
                        if sayilar: 
                            fin_data[alan] = sayilar
                        break
        return fin_data

    def _durum_belirle(self, tarih_metni: str, islem_tarihi_metni: str, raw_text: str, kart_metni: str) -> ArzDurumu:
        rt_lower, bugun = raw_text.lower(), datetime.now().date()
        aylar = {
            "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, 
            "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12
        }

        if "işlem görmeye başlamıştır" in rt_lower or "gong!" in kart_metni.lower(): 
            return ArzDurumu.ISLEM_GORMEYE_BASLADI

        is_trh = str(islem_tarihi_metni).lower().strip()
        if is_trh and is_trh not in ("-", "açıklanmadı", "belli değil"):
            ay_str = next((ay for ay in aylar if ay in is_trh), None)
            sayilar = re.findall(r'\d+', is_trh)
            if ay_str and sayilar:
                try:
                    yil = next((int(s) for s in sayilar if len(s) == 4), bugun.year)
                    gun = next((int(s) for s in sayilar if 1 <= int(s) <= 31 and len(s) <= 2), 1)
                    if bugun >= date(yil, aylar[ay_str], gun): 
                        return ArzDurumu.ISLEM_GORMEYE_BASLADI
                except Exception: 
                    pass

        if "dağıtılan pay miktarı" in rt_lower or "kesinleşen" in rt_lower: 
            return ArzDurumu.ISLEME_BEKLENIYOR

        t_metin = str(tarih_metni).lower().strip()
        if not t_metin or t_metin in ("-", "açıklanmadı", "belli değil"):
            return ArzDurumu.HAZIRLANIYOR if "taslak" in kart_metni or "hazırlanıyor" in kart_metni else ArzDurumu.SPK_ONAYLI

        ay_str = next((ay for ay in aylar if ay in t_metin), None)
        sayilar = re.findall(r'\d+', t_metin)

        if ay_str and len(sayilar) >= 2:
            try:
                yil = next((int(s) for s in sayilar if len(s) == 4), bugun.year)
                gunler = [int(s) for s in sayilar if len(s) < 4] or [1]
                bas_dt, bit_dt = date(yil, aylar[ay_str], gunler[0]), date(yil, aylar[ay_str], max(gunler))
                if bugun < bas_dt: 
                    return ArzDurumu.TALEP_YAKLASIYOR
                elif bas_dt <= bugun <= bit_dt: 
                    return ArzDurumu.TALEP_TOPLANIYOR
                elif bugun > bit_dt: 
                    return ArzDurumu.DAGITIM_BEKLENIYOR
            except Exception: 
                pass

        return ArzDurumu.HAZIRLANIYOR if "hazırlanıyor" in kart_metni or "taslak" in kart_metni else ArzDurumu.SPK_ONAYLI

    async def _sirket_isle(self, sirket_adi: str, link: str, kart_metni: str, aktif_arz_sayisi: int) -> Optional[dict]:
        html = await self._fetch_url(link)
        if not html: 
            return None

        soup = BeautifulSoup(html, "html.parser")
        raw_text = soup.get_text(separator="\n", strip=True)

        veri = dict(self.DEFAULTS)
        self._tablodan_bilgi_al(veri, soup)
        self._satirlardan_doldur(veri, raw_text)
        self._dagitim_tahsisat_doldur(veri, raw_text)
        fin = self._tablodan_finansal_al(soup)

        t1_t2 = "t1-t2 kullanılabilir" in raw_text.lower() or "t1 ve t2 kullanılabilir" in raw_text.lower()
        katilim = ("katılım endeksine uygun değildir" not in raw_text.lower() and "katılım endeksine uygun" in raw_text.lower())
        islem_menusu = "Hisse Alış/Satış Menüsü" if "borsada satış" in veri.get(InfoKey.DAGITIM_YONTEMI, "").lower() else "Halka Arz Menüsü"

        if veri.get(InfoKey.TARIH) == self.DEFAULTS.get(InfoKey.TARIH):
            tarih_match = re.search(r"(\d{1,2}(?:-\d{1,2})*\s+(?:ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık)\s+\d{4})", kart_metni, re.IGNORECASE)
            if tarih_match: 
                veri[InfoKey.TARIH] = tarih_match.group(1).title()

        durum = self._durum_belirle(veri.get(InfoKey.TARIH, ""), veri.get(InfoKey.ISLEM_TARIHI, ""), raw_text, kart_metni)

        fiyat = TextUtils.sayi_bul(veri.get(InfoKey.FIYAT, "")) or 0.0
        pay = TextUtils.sayi_bul(veri.get(InfoKey.PAY_SAYISI, "")) or 0.0
        sektor = self.analyzer._get_sektor(raw_text)

        c_finans = self.analyzer.finansal_analiz(fin)
        c_degerleme = self.analyzer.degerleme_analizi(fiyat, pay, veri, fin, sektor)
        c_yapi = self.analyzer.yapi_analizi(veri)
        c_fon = self.analyzer.fon_kullanimi_analizi(veri.get(InfoKey.FON_KULLANIM, ""))
        c_kalite = self.analyzer.sirket_kalitesi_analizi(raw_text)
        
        toplam_skor = c_finans.score + c_degerleme.score + c_yapi.score + c_fon.score + c_kalite.score
        risk_raporu = self.analyzer.risk_motoru_hesapla(fiyat, pay, fin, veri, raw_text)
        talep_endeksi = self.analyzer.talep_endeksi_hesapla(fiyat, pay, veri, raw_text, aktif_arz_sayisi)
        
        c_kalite_endeksi = self.analyzer.kalite_endeksi(fin, veri, raw_text)
        c_guven = self.analyzer.guven_skoru(fin, veri, raw_text)

        return {
            "sirket": sirket_adi,
            "bist_kodu": veri[InfoKey.BIST_KODU],
            "durum": durum,
            "genel_skor": round(toplam_skor, 1),
            "skor_yildizi": TextUtils.yildiz_uret(toplam_skor, 100),
            
            "kategoriler": {
                "finansal_guc": {"skor_metni": c_finans.label, "detaylar": c_finans.explanations},
                "degerleme": {"skor_metni": c_degerleme.label, "detaylar": c_degerleme.explanations},
                "arz_yapisi": {"skor_metni": c_yapi.label, "detaylar": c_yapi.explanations},
                "fon_kullanimi": {"skor_metni": c_fon.label, "detaylar": c_fon.explanations},
                "sirket_kalitesi": {"skor_metni": c_kalite.label, "detaylar": c_kalite.explanations}
            },
            
            "talep_endeksi": talep_endeksi,
            "risk_raporu": risk_raporu,
            "kalite_endeksi": round(c_kalite_endeksi, 1),
            "guven_skoru": c_guven,
            
            "fiyat": veri.get(InfoKey.FIYAT),
            "tarih": veri.get(InfoKey.TARIH),
            "islem_tarihi": veri.get(InfoKey.ISLEM_TARIHI),
            "halka_arz_buyuklugu": veri.get(InfoKey.BUYUKLUK),
            "halka_aciklik": veri.get(InfoKey.ACIKLIK),
            "iskonto": veri.get(InfoKey.ISKONTO),
            "fon_kullanim_ham": veri.get(InfoKey.FON_KULLANIM),
            "pay_sayisi": veri.get(InfoKey.PAY_SAYISI), # FLUTTER İÇİN EKLENEN ANAHTAR
            "sektor": sektor,
            "katilim_endeksine_uygun": katilim,
            "t1_t2_kullanilabilir": t1_t2,
            "islem_menusu": islem_menusu,
            "dagitim_tablosu": veri.get(InfoKey.DAGITIM_TABLOSU),
            "dagitim_tipi": veri.get(InfoKey.DAGITIM_TIPI),
            "dagitim_yontemi": veri.get(InfoKey.DAGITIM_YONTEMI),
            "tahsisat": veri.get(InfoKey.TAHSISAT),
            "satis_yontemi": veri.get(InfoKey.SATIS_YONTEMI),
            "fiyat_istikrari": veri.get(InfoKey.FIYAT_ISTIKRARI),
            "taahhut": veri.get(InfoKey.TAAHHUT),
            "araci_kurum": veri.get(InfoKey.ARACI_KURUM),
            "pazar": veri.get(InfoKey.PAZAR),
        }

    async def analiz_et(self) -> list[dict]:
        html = await self._fetch_url(SETTINGS.BASE_URL)
        if not html: 
            return []

        soup = BeautifulSoup(html, "html.parser")
        meta_list = []
        gorulen = set()

        # Orijinal ve en kararlı arama mantığı
        for etiket in soup.find_all(["h3", "h2", "a"]):
            link = etiket.find("a") or (etiket if etiket.name == "a" else etiket.find_parent("a"))
            if not link or "href" not in link.attrs: 
                continue
            
            adi = etiket.get_text(strip=True)
            if not adi or adi in gorulen or len(adi) <= 3: 
                continue
            
            href = link["href"]
            tam_link = href if href.startswith("http") else f"{SETTINGS.BASE_URL.rstrip('/')}{href}"
            
            parent_li = etiket.find_parent(["li", "div", "article"])
            kart_metni = parent_li.get_text(strip=True).lower() if parent_li else adi.lower()
            
            # Aktif halka arzları belirten anahtar kelime kontrolü
            if any(b in kart_metni for b in ["yeni!", "talep toplan", "taslak", "onaylı", "yaklaşan", "hazırlanıyor", "işlem görüyor", "gong!"]):
                gorulen.add(adi)
                meta_list.append((adi, tam_link, kart_metni))
                if len(meta_list) >= SETTINGS.MAX_SIRKET: 
                    break

        aktif_arz_sayisi = len(meta_list)
        semaphore = asyncio.Semaphore(SETTINGS.ESZAMANLI_ISTEK_LIMITI)
        
        async def _bound_worker(adi, link, kart):
            async with semaphore:
                res = await self._sirket_isle(adi, link, kart, aktif_arz_sayisi)
                await asyncio.sleep(SETTINGS.ISTEK_ARASI_BEKLEME)
                return res

        tasks = [asyncio.create_task(_bound_worker(a, l, k)) for a, l, k in meta_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [r for r in results if isinstance(r, dict)]

    async def close(self):
        if self.session and not getattr(self.session, "closed", True):
            await self.session.close()