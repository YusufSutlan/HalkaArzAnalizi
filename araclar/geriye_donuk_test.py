"""
═══════════════════════════════════════════════════════════════════
GERİYE DÖNÜK TEST  +  veri/gecmis_arzlar.json ÜRETİCİ
═══════════════════════════════════════════════════════════════════

Ne yapar?
  1. Kaynak sitenin yıl arşivlerinden tamamlanan halka arzları toplar.
  2. Her arzın sayfasını UYGULAMANIN KENDİ ayrıştırıcısıyla okur
     (fiyat, büyüklük, dağıtım; ayrıca gerçek katılımcı sayısı ve kişi
     başına dağıtılan tutar).
  3. Hisselerin borsa fiyat geçmişinden gerçek sonuçları hesaplar:
     tavan serisi uzunluğu, ilk gün ve 20/60 günlük getiri.
  4. Modeli her arz için "o gün bilinen" veriyle çalıştırır
     (ileriye bakmadan: katılımcı ve kişi başı referansları yalnızca
     daha ÖNCE biten arzlardan gelir).
  5. Model çıktılarının gerçek sonuçlarla ilişkisini raporlar.
  6. --yaz verilirse veri/gecmis_arzlar.json dosyasını günceller.
     Sunucu bu dosyayı okur (bkz. Gecmisveri.py).

Kullanım (depo kökünden):
    python araclar/geriye_donuk_test.py            # yalnızca rapor
    python araclar/geriye_donuk_test.py --yaz      # raporu yaz + dosyayı güncelle

İndirilen sayfalar .onbellek/geriye_donuk/ altında saklanır; tekrar
çalıştırmada yalnızca yeni arzlar ve güncel fiyatlar indirilir.

Sınırlar (dürüstçe):
  - Fiyat sağlayıcıda 2023'ün ikinci yarısındaki arzların ilk ayları eksik;
    o arzlar sonuç istatistiklerine girmez.
  - Emsal çarpan medyanları tüm örneklemden hesaplanır (ileriye bakış
    içerir); etkisi yalnızca temel kalite skorunun değerleme boyutundadır.
  - ~100 gözlem küçük bir örneklemdir. Katsayılar bu yüzden yuvarlak
    tutuldu; rapordaki korelasyonlar ±0,2 belirsizlik taşır.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import datetime as dt
import json
import logging
import math
import os
import re
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ARSIV = "https://halkarz.com/k/halka-arz/{yil}/"
FIYAT_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{kod}.IS"
             "?period1=1640995200&period2=1900000000&interval=1d&events=split")
TAVAN_ESIGI = 1.0945     # günlük +%10 limit, yuvarlama payıyla


# ───────────────────────── indirme ─────────────────────────

def indir(url: str, hedef: Path, en_az: int = 800, tazele_saat: float | None = None) -> bool:
    if hedef.exists() and hedef.stat().st_size >= en_az:
        if tazele_saat is None or (time.time() - hedef.stat().st_mtime) < tazele_saat * 3600:
            return True
    gecici = hedef.with_suffix(hedef.suffix + ".tmp")
    kod = subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A", UA, "-o", str(gecici),
         "-w", "%{http_code}", url],
        capture_output=True, text=True).stdout.strip()
    time.sleep(0.5)
    if kod == "200" and gecici.exists() and gecici.stat().st_size >= en_az:
        gecici.replace(hedef)
        return True
    gecici.unlink(missing_ok=True)
    return hedef.exists() and hedef.stat().st_size >= en_az


def arsivi_topla(onbellek: Path, yillar: list[int]) -> list[dict]:
    from bs4 import BeautifulSoup
    (onbellek / "arsiv").mkdir(parents=True, exist_ok=True)
    bulunan: dict[str, dict] = {}
    bu_yil = dt.date.today().year
    for yil in yillar:
        for sayfa in range(1, 12):
            url = ARSIV.format(yil=yil) + (f"page/{sayfa}/" if sayfa > 1 else "")
            dosya = onbellek / "arsiv" / f"{yil}_{sayfa}.html"
            if not indir(url, dosya, 3000, tazele_saat=12 if yil == bu_yil else None):
                break
            soup = BeautifulSoup(dosya.read_text(encoding="utf-8", errors="ignore"), "html.parser")
            yeni = 0
            for h in soup.find_all("h3"):
                a = h.find("a") or h.find_parent("a")
                if not a or not a.get("href") or "/k/" in a["href"]:
                    continue
                kap = h.find_parent(["li", "article", "div"])
                if a["href"] not in bulunan:
                    yeni += 1
                bulunan.setdefault(a["href"], {
                    "ad": h.get_text(strip=True), "url": a["href"], "yil": yil,
                    "kart": (kap.get_text(" ", strip=True) if kap else "")[:200],
                    "slug": a["href"].rstrip("/").split("/")[-1],
                })
            if not yeni:
                break
    return list(bulunan.values())


# ───────────────────────── fiyat verisi ─────────────────────────

def fiyat_oku(dosya: Path):
    try:
        j = json.loads(dosya.read_text())["chart"]["result"][0]
        q = j["indicators"]["quote"][0]
        satirlar = [
            (dt.datetime.fromtimestamp(t + 10800, dt.UTC).date(), k)
            for t, k in zip(j["timestamp"], q["close"]) if k
        ]
        bolunmeler = [
            (dt.datetime.fromtimestamp(int(v["date"]) + 10800, dt.UTC).date(),
             v["numerator"] / v["denominator"])
            for v in ((j.get("events") or {}).get("splits") or {}).values()
        ]
        return satirlar, bolunmeler
    except Exception:
        return None, None


def sonuc_hesapla(satirlar, bolunmeler, islem: dt.date, fiyat: float) -> dict:
    """Halka arz fiyatına göre tavan serisi ve getiriler."""
    gunler = [s[0] for s in satirlar]
    i = bisect.bisect_left(gunler, islem)
    if i >= len(satirlar) or (satirlar[i][0] - islem).days > 12:
        return {}
    carpan = 1.0
    for g, oran in bolunmeler:          # sonradan yapılan bedelsiz/bölünme
        if g > satirlar[i][0]:
            carpan *= oran
    arz_fiyati = fiyat / carpan
    kap = [s[1] for s in satirlar[i:]]
    # Sağlayıcıda ilk günler eksikse (ilk kapanış tam k tavana denk geliyorsa)
    # eksik günleri tavan olarak tamamla.
    ham = kap[0] / arz_fiyati
    if 1.105 < ham < 1.1 ** 7:
        k = round(math.log(ham) / math.log(1.1))
        if k >= 2 and abs(ham - 1.1 ** k) / 1.1 ** k < 0.012:
            kap = [arz_fiyati * 1.1 ** j for j in range(1, k)] + kap
    ilk_gun = kap[0] / arz_fiyati - 1
    if not -0.105 <= ilk_gun <= 0.105:
        return {}                        # fiyat verisi arz fiyatıyla tutarsız
    tavan, onceki = 0, arz_fiyati
    for k in kap:
        if k >= onceki * TAVAN_ESIGI:
            tavan += 1
            onceki = k
        else:
            break
    s = {"tavan": tavan, "ilk_gun": round(ilk_gun, 4), "gun_sayisi": len(kap)}
    for g in (5, 20, 60):
        if len(kap) >= g:
            s[f"getiri_{g}g"] = round(kap[g - 1] / arz_fiyati - 1, 4)
    return s


def momentum(endeks, gun: dt.date, n: int = 60):
    gunler = [s[0] for s in endeks]
    i = bisect.bisect_left(gunler, gun) - 1
    if i - n < 0:
        return None
    return endeks[i][1] / endeks[i - n][1] - 1


# ───────────────────────── istatistik ─────────────────────────

def _sira(v):
    s = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
            j += 1
        for k in range(i, j + 1):
            r[s[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(satirlar, fx, fy):
    c = [(fx(r), fy(r)) for r in satirlar]
    c = [(x, y) for x, y in c if x is not None and y is not None]
    if len(c) < 8:
        return None, len(c)
    a, b = _sira([p[0] for p in c]), _sira([p[1] for p in c])
    n = len(c)
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if not sa or not sb:
        return None, n
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb), n


# ───────────────────────── ana akış ─────────────────────────

async def calistir(args) -> int:
    logging.disable(logging.CRITICAL)
    import proje
    from proje import (ArzDurumu, DataExtractor, FinKey, InfoKey, PiyasaBaglami,
                       ScoreAnalyzer, TextUtils)
    from Gecmisveri import GecmisArzlar

    onbellek = Path(args.onbellek)
    for alt in ("sayfa", "fiyat"):
        (onbellek / alt).mkdir(parents=True, exist_ok=True)
    yillar = list(range(args.baslangic, dt.date.today().year + 1))

    print(f"1/5 Arşiv taranıyor ({yillar[0]}–{yillar[-1]})…")
    liste = arsivi_topla(onbellek, yillar)
    print(f"    {len(liste)} tamamlanmış halka arz bulundu.")

    print("2/5 Arz sayfaları indiriliyor ve ayrıştırılıyor…")
    analizci = ScoreAnalyzer()
    cikarici = DataExtractor(analizci)
    aktif = {}

    async def _dosyadan(_url):
        return aktif["dosya"].read_text(encoding="utf-8", errors="ignore")
    cikarici._fetch_url_with_retry = _dosyadan

    ayristirilan = []
    for kayit in liste:
        dosya = onbellek / "sayfa" / f"{kayit['slug']}.html"
        # Yeni arzların sayfası sonuçlar açıklandıkça değişir; bu yılın
        # sayfaları 3 günde bir tazelenir.
        taze = 72 if kayit["yil"] == dt.date.today().year else None
        if not indir(kayit["url"], dosya, 5000, tazele_saat=taze):
            continue
        aktif["dosya"] = dosya
        try:
            p = await cikarici._sirket_parse(kayit["ad"], kayit["url"], kayit["kart"].lower())
        except Exception as e:
            print(f"    ayrıştırılamadı: {kayit['slug']} ({e!r})")
            continue
        if p:
            p["slug"], p["yil"] = kayit["slug"], kayit["yil"]
            ayristirilan.append(p)
    print(f"    {len(ayristirilan)} sayfa ayrıştırıldı.")

    print("3/5 Fiyat geçmişleri indiriliyor…")
    indir(FIYAT_URL.format(kod="XU100"), onbellek / "fiyat" / "XU100.json", tazele_saat=12)
    endeks, _ = fiyat_oku(onbellek / "fiyat" / "XU100.json")
    if not endeks:
        print("    HATA: endeks verisi alınamadı."); return 1

    satirlar: list[dict] = []
    for p in ayristirilan:
        v = p["veri"]
        kod = str(v[InfoKey.BIST_KODU]).strip().upper()
        rt = p["raw_text"]
        r = {"kod": kod, "sirket": p["sirket_adi"], "slug": p["slug"], "yil": p["yil"],
             "fiyat": p["fiyat"], "arz": p["arz_buyuklugu"]}
        ta = p["talep_aralik"]
        r["talep_bit"] = ta[1].isoformat() if ta else None
        ia = TextUtils.tarih_araligi_coz(str(v.get(InfoKey.ISLEM_TARIHI, "")))
        r["islem"] = ia[0].isoformat() if ia else None

        # Gerçekleşen dağıtım (sayfadaki "Halka Arz Sonuçları" tablosu)
        m = re.search(r"Yurt İçi Bireysel\n([\d.]+)\n([\d.]+)\n%([\d,.]+)", rt)
        if m:
            r["katilimci"] = int(m.group(1).replace(".", ""))
            lot = int(m.group(2).replace(".", ""))
            r["bireysel_oran"] = float(m.group(3).replace(",", "."))
            if r["katilimci"] and r["fiyat"]:
                r["kisi_basi_tl"] = round(lot / r["katilimci"] * r["fiyat"])
        m = re.search(r"Dağıtılan Pay Miktarı[^\n]*\n-?\s*([\d.]+)\s*~\s*([\d.]+)\s*Lot\s*\(([\d.,]+)\s*TL\)", rt)
        if m:
            r.setdefault("katilimci", int(m.group(1).replace(".", "")))
            r["kisi_basi_tl"] = round(float(m.group(3).replace(".", "").replace(",", ".")))
        if not r.get("talep_bit") or not r.get("arz"):
            continue

        r["mom60"] = momentum(endeks, dt.date.fromisoformat(r["talep_bit"]))
        if r["islem"] and r["fiyat"] and kod not in ("", "BELLI DEĞIL"):
            dosya = onbellek / "fiyat" / f"{kod}.json"
            yeni_mi = (dt.date.today() - dt.date.fromisoformat(r["islem"])).days < 120
            if indir(FIYAT_URL.format(kod=kod), dosya, tazele_saat=24 if yeni_mi else None):
                f, b = fiyat_oku(dosya)
                if f:
                    r.update(sonuc_hesapla(f, b, dt.date.fromisoformat(r["islem"]), r["fiyat"]))
        r["_p"] = p
        satirlar.append(r)
    satirlar.sort(key=lambda r: r["talep_bit"])
    gecerli = [r for r in satirlar if r.get("tavan") is not None]
    print(f"    {len(satirlar)} arz; {len(gecerli)} tanesinde doğrulanmış fiyat sonucu var.")

    print("4/5 Model her arz için o günkü bilgiyle çalıştırılıyor…")
    mevcut = {}
    hedef = KOK / "veri" / "gecmis_arzlar.json"
    if hedef.exists():
        try:
            mevcut = json.loads(hedef.read_text(encoding="utf-8"))
        except Exception:
            mevcut = {}
    elle = {k: mevcut[k] for k in ("bist100_medyan", "finans_medyan") if k in mevcut}

    def _acik(liste):
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in liste]

    def _puanla(ozet):
        proje.GECMIS = GecmisArzlar(kayitlar=_acik(satirlar), ozet=ozet)
        baglamlar = cikarici._piyasa_baglami_olustur([r["_p"] for r in satirlar])
        for r in satirlar:
            p = r["_p"]
            b = baglamlar.get(p["sirket_adi"], PiyasaBaglami())
            b.bist_mom60 = r.get("mom60")
            b.referans_tarih = dt.date.fromisoformat(r["talep_bit"])
            try:
                s = analizci.skoru_topla(
                    p["veri"], p["fin"], p["seriler"], ArzDurumu.TALEP_TOPLANIYOR,
                    p["raw_text"], b, olasi_lot=p.get("olasi_lot_tablosu"),
                    kurulus_yili=p.get("kurulus_yili"), sirket_adi=p["sirket_adi"])
            except Exception as e:
                print(f"    puanlanamadı: {r['kod']} ({e!r})")
                continue
            r["kalite"] = s["temel_kalite"]
            r["kisa_vade_skoru"] = s["kisa_vade"]["skor"] if s["kisa_vade"]["olculebilir"] else None
            r["baski_skoru"] = s["baski"]["skor"]
            r["tahmin_kisi_basi_tl"] = s["baski"].get("kisi_basi_tahmini_tutar")
            r["_carpan"] = {c["ad"]: c["deger"] for c in s["degerleme"].get("carpanlar", [])}
            r["_sektor"] = s["sektor"]
            r["_finansal"] = s.get("finansal_veri_var")

    _puanla({**elle})                                  # 1. geçiş: çarpanları topla
    fk = [r["_carpan"]["F/K"] for r in satirlar
          if r.get("_carpan", {}).get("F/K") and r.get("_sektor") not in ("FİNANS", "GYO")]
    pd = [r["_carpan"]["PD/DD"] for r in satirlar
          if r.get("_carpan", {}).get("PD/DD") and r.get("_sektor") not in ("FİNANS", "GYO")]
    emsal = ({"fk": round(st.median(fk), 1), "pddd": round(st.median(pd), 2),
              "n": min(len(fk), len(pd))} if len(fk) >= 8 and len(pd) >= 8 else None)
    ozet = {**elle, "guncelleme": dt.date.today().isoformat()}
    if emsal:
        ozet["emsal"] = emsal
    _puanla(ozet)                                      # 2. geçiş: nihai skorlar

    # ── 5. Rapor ──
    print("5/5 Rapor\n" + "═" * 78)
    G = [r for r in satirlar if r.get("tavan") is not None and r.get("kisa_vade_skoru") is not None]
    print(f"Örneklem: {len(G)} arz ({G[0]['talep_bit'][:7]} – {G[-1]['talep_bit'][:7]})")
    if emsal:
        print(f"Halka arz emsal medyanları: F/K {emsal['fk']}, PD/DD {emsal['pddd']} (n={emsal['n']})")
    son = [r for r in satirlar if r.get("katilimci")][-5:]
    print("Son arzlarda katılım: " + ", ".join(f"{r['kod']} {r['katilimci']:,}".replace(",", ".") for r in son))

    def satir(ad, fx, hedefler=("tavan", "getiri_5g", "getiri_20g", "getiri_60g")):
        h = f"  {ad:34}"
        for o in hedefler:
            rho, n = spearman(G, fx, lambda r, o=o: r.get(o))
            h += f"{'   yok' if rho is None else f'{rho:+6.2f}'} (n={n:3d})  "
        print(h)

    print("\nSpearman sıra korelasyonu             tavan         5 gün         20 gün        60 gün")
    satir("KISA VADE GÖSTERGESİ (model)", lambda r: r["kisa_vade_skoru"])
    satir("satış baskısı (model)", lambda r: r["baski_skoru"])
    satir("temel kalite (model)", lambda r: r.get("kalite"))
    print("  " + "-" * 76)
    satir("tahmini kişi başı TL (ex-ante)", lambda r: r.get("tahmin_kisi_basi_tl"))
    satir("GERÇEKLEŞEN kişi başı TL", lambda r: r.get("kisi_basi_tl"))
    satir("BIST100 60g momentum", lambda r: r.get("mom60"))
    satir("arz büyüklüğü", lambda r: r.get("arz"))

    print("\nYıl bazında kararlılık (gösterge ↔ tavan sayısı):")
    for y in yillar:
        g = [r for r in G if r["yil"] == y]
        rho, n = spearman(g, lambda r: r["kisa_vade_skoru"], lambda r: r["tavan"])
        print(f"  {y}: " + ("yetersiz örnek" if rho is None else f"{rho:+.2f}") + f"  (n={n})")

    hata = [abs(r["tahmin_kisi_basi_tl"] / r["kisi_basi_tl"] - 1) for r in G
            if r.get("tahmin_kisi_basi_tl") and r.get("kisi_basi_tl")]
    if hata:
        print(f"\nKişi başı tutar tahmininin medyan mutlak hatası: %{100 * st.median(hata):.0f} (n={len(hata)})")

    def dilim(ad, alan, sinirlar):
        print(f"\n{ad} dilimlerinde GERÇEK sonuçlar:")
        print("  aralık      n   tavan medyan  çeyrekler  hiç tavan yok  ilk gün ekside  20g medyan getiri")
        for alt, ust in zip(sinirlar[:-1], sinirlar[1:]):
            g = [r for r in G if alt <= r[alan] < ust]
            if not g:
                continue
            t = sorted(r["tavan"] for r in g); n = len(t)
            g20 = [r["getiri_20g"] for r in g if r.get("getiri_20g") is not None]
            print(f"  {alt:3.0f}–{ust:<3.0f}  {n:4d}   {st.median(t):8.1f}      {t[n // 4]:2d}–{t[(3 * n) // 4]:<2d}"
                  f"        %{100 * sum(x == 0 for x in t) // n:<3d}           %{100 * sum(r['ilk_gun'] < 0 for r in g) // n:<3d}"
                  f"          {'%' + format(100 * st.median(g20), '+.0f') if g20 else '-'}")
    dilim("Kısa vade göstergesi", "kisa_vade_skoru", [0, 30, 45, 60, 75, 101])
    dilim("Satış baskısı", "baski_skoru", [0, 25, 65, 85, 101])
    print("═" * 78)

    if args.yaz:
        alanlar = ("kod", "sirket", "talep_bit", "islem", "arz", "fiyat", "katilimci",
                   "kisi_basi_tl", "mom60", "tavan", "ilk_gun", "getiri_5g", "getiri_20g",
                   "getiri_60g", "kalite", "baski_skoru", "kisa_vade_skoru")
        cikti = {
            **ozet,
            "aciklama": "araclar/geriye_donuk_test.py tarafından üretilir; elle düzenlemeyin "
                        "(bist100_medyan ve finans_medyan blokları hariç).",
            "ornek_sayisi": len(G),
            "arzlar": [
                {k: (round(r[k], 4) if isinstance(r.get(k), float) else r.get(k))
                 for k in alanlar if r.get(k) is not None}
                for r in satirlar
            ],
        }
        hedef.parent.mkdir(parents=True, exist_ok=True)
        hedef.write_text(json.dumps(cikti, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Yazıldı: {hedef.relative_to(KOK)} ({len(cikti['arzlar'])} arz)")
    await cikarici.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yaz", action="store_true", help="veri/gecmis_arzlar.json dosyasını güncelle")
    ap.add_argument("--baslangic", type=int, default=2023, help="ilk yıl (varsayılan 2023)")
    ap.add_argument("--onbellek", default=str(KOK / ".onbellek" / "geriye_donuk"))
    sys.exit(asyncio.run(calistir(ap.parse_args())))
