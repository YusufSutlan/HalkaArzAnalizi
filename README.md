# Halka Arz Analizi

BIST'te (Borsa İstanbul) halka arz sürecindeki şirketleri otomatik olarak
tarayan, izahnamedeki finansal verileri çıkarıp doğrulayan ve her şirket için
bir "yatırım kalitesi" skoru üreten bir FastAPI servisi.

Servis, [halkarz.com](https://halkarz.com/) üzerindeki güncel halka arz
ilanlarını tarar; her arz için fiyat, arz büyüklüğü, halka açıklık oranı,
iskonto, dağıtım yöntemi, tahsisat grupları gibi bilgileri sayfa metninden
çıkarır ve şirketin finansal tablolarına (kâr, özkaynak, borç, hasılat vb.)
dayalı kantitatif bir değerleme yapar. Sonuç; kategorilere ayrılmış bir skor,
yıldız derecelendirmesi, risk raporu ve talep endeksi olarak JSON formatında
sunulur.

> Üretilen skorlar otomatik bir algoritmanın çıktısıdır ve yatırım tavsiyesi
> değildir. Karar vermeden önce izahnameyi ve KAP açıklamalarını kendiniz
> doğrulayın.

## Mimari

- **`proje.py`** — API'nin tamamı: FastAPI uygulaması, tarayıcı ve
  skorlama mantığı tek dosyada. `Dockerfile` bu dosyayı (`proje:app`)
  çalıştırır ve Render'a bu şekilde deploy edilir.

Finansal veri tarafında ise ayrı ve daha ağır bir hat var:

- **`Izahnameisle.py`** — GitHub Actions üzerinde günde bir kez çalışır
  (`.github/workflows/izahname.yml`). İzahname PDF'lerini indirir, finansal
  tablo sayfalarını görüntüye çevirip bir LLM'e (Gemini) okutur, dönen
  sonucu muhasebe kurallarıyla doğrular ve `veri/finansallar/{slug}.json`
  olarak depoya commit eder.
- **`Finansaldepo.py`** — Sunucu tarafında bu hazır JSON dosyalarını okuyan
  hafif katman. Böylece Render sunucusu hiç PDF indirmez, OCR yapmaz veya
  LLM'e istek atmaz; sadece diskteki JSON'u okur.
- **`izahname.py` / `izahname_servis.py`** — PDF'ten satır bazlı finansal
  veri çıkarımı ve bu işi proje.py'ye bağlayan önbellekli entegrasyon
  katmanı (üç kademeli: disk önbelleği, hedefli sayfa taraması, OCR).
- **`kap.py`** — Şirket zaten borsada işlem görmeye başladıysa KAP (Kamuyu
  Aydınlatma Platformu) üzerinden finansal veri tamamlama; test'leri
  `test_kap.py` içinde.

- **`Gecmisveri.py`** + **`veri/gecmis_arzlar.json`** — 2023'ten bu yana
  tamamlanan halka arzların gerçek sonuçları (katılımcı sayısı, kişi başı
  dağıtılan tutar, tavan serisi, getiriler). Kısa vade göstergesi beklenen
  katılımı, kıyas değerlerini ve "benzer arzlar geçmişte ne yaptı"
  istatistiğini buradan alır.
- **`araclar/geriye_donuk_test.py`** — Bu dosyayı üretir ve modeli gerçek
  sonuçlarla sınar. `python araclar/geriye_donuk_test.py` rapor basar,
  `--yaz` ile veri dosyasını günceller. GitHub Actions her gün çalıştırır.
- **`Endeksveri.py`** — BIST 100 kapanışlarını TCMB EVDS'ten alır (piyasa
  momentumu). `EVDS_API_KEY` ortam değişkeni gerekir; anahtar koda ve depoya
  yazılmaz. Render'da ortam değişkeni, GitHub Actions'ta Secret olarak tanımlıdır.
  Anahtar yoksa momentum "bilinmiyor" sayılır ve `/health` bunu gösterir.
- **`test_skor.py`** — Skor motoru testleri (`python test_skor.py`, ağ gerekmez).

## Skor modeli (v4)

İki ayrı çıktı vardır ve dayanakları farklıdır:

**Kısa vade göstergesi ve ilk gün satış baskısı — geriye dönük test edilmiş.**
108 halka arzın (2023–2026) gerçek sonuçlarıyla sınandı. Gösterge dört
etkenden oluşur: kişi başına düşen tahmini tutar (son arzların medyanına
göre), BIST 100'ün son 60 günlük getirisi, arz büyüklüğü ve temel kalite.
Tavan serisi uzunluğuyla sıra korelasyonu ≈ 0,57; 2023, 2024, 2025 ve 2026
yıllarının her birinde ayrı ayrı pozitif (0,47–0,64). Halka arz iskontosu,
ortak satışı oranı, rakip arz sayısı ve fiyat istikrarı taahhüdünün ölçülebilir
etkisi çıkmadığı için bu göstergeye girmezler.

**Temel kalite skoru — kurama dayalı, istatistiksel olarak doğrulanmamış.**
Kârlılık, büyüme, borç, likidite, nakit akışı, değerleme ve arz yapısını
puanlar. İzahname finansalı olan tamamlanmış arz sayısı (~20) doğrulama için
yetersizdir. Değerleme, kaynağı belirsiz sektör sabitleri yerine geçmiş halka
arzların çarpan medyanlarıyla kıyaslanır ve halka arz sonrası bilanço
kullanılır (sermaye artırımı geliri özkaynağa eklenir).

Bilinen sınırlar: FAVÖK izahnamelerin çok azında hesaplanabildiği için
FD/FAVÖK ve Net Borç/FAVÖK nadiren ölçülür; fiyat sağlayıcıda 2023'ün ikinci
yarısındaki arzların sonuçları eksiktir; ~100 gözlem küçük bir örneklemdir.
Araç hisse fiyatı indirmez (izinli bir kaynak henüz yok): mevcut 108 arzın
sonuçları dosyada korunur, yeni arzların tavan/getiri alanları boş kalır.
Katılımcı, kişi başı tutar ve arz büyüklüğü verisi günlük güncellenir.

**`mobil_app/`** — API'nin `/api/halkarzlar` uç noktasına doğrudan bağlanan
Flutter mobil istemcisi (`lib/main.dart`). `ApiConfig.baseUrl` production
adresine (Render) işaret eder; yerel geliştirmede `pubspec.yaml` bulunan
klasörde `flutter run` yeterlidir, ayrı bir backend kurmaya gerek yoktur.

## API Uçları

| Uç Nokta | Açıklama |
|---|---|
| `GET /api/halkarzlar` | Güncel halka arz listesini ve analiz sonuçlarını döner. Sonuçlar `CACHE_TTL` saniye önbelleklenir. |
| `GET /api/halkarzlar?debug=true` | Ham veriyi döner; `X-Debug-Key` header'ında geçerli bir anahtar ister. |
| `POST /api/cache/clear` | Önbelleği temizler; `X-Debug-Key` header'ı gerekir. |
| `GET /health` | Basit sağlık kontrolü. |

## Kurulum ve Çalıştırma

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

uvicorn proje:app --reload
```

### Docker

```bash
docker build -t halka-arz-analizi .
docker run -p 8000:8000 -e PORT=8000 halka-arz-analizi
```

Dockerfile; izahname PDF'lerindeki taranmış (görüntü) metni okuyabilmek için
`tesseract-ocr` ve Türkçe dil paketini kurar.

### Önemli ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `BASE_URL` | `https://halkarz.com/` | Taranacak kaynak site |
| `CACHE_TTL` | `300` | Önbellek süresi (saniye) |
| `MAX_SIRKET` | `15` / `40` | Aynı anda işlenecek maksimum şirket sayısı |
| `DEBUG_API_KEY` | — | Debug uçları için gerekli anahtar |
| `ALLOWED_ORIGINS` | `*` | CORS izinli originler |
| `PORT` | `8000` | Sunucu portu |
| `LLM_API_KEY` | — | İzahname çıkarımı için LLM (Gemini) anahtarı (yalnızca GitHub Actions) |

## Test

```bash
python test_kap.py
```

## Klasör notları

- `veri/finansallar/` — GitHub Actions tarafından üretilen, izahnamelerden
  çıkarılan finansal veri JSON'ları. Sunucu bunları doğrudan okur.
- `mobil_app/` — Flutter istemcisi. Çalıştırmak için: `cd mobil_app && flutter pub get && flutter run`.
  Yerel bir backend'e bağlamak için: `flutter run --dart-define=API_URL=http://localhost:8000`.
