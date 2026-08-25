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

Depoda aynı işi gören iki farklı uygulama gövdesi bulunuyor:

- **`proje.py`** — Şu an **kullanımda olan**, tek dosyaya yazılmış monolitik
  sürüm. `Dockerfile` bu dosyayı (`proje:app`) çalıştırır ve Render'a bu
  şekilde deploy edilir.
- **`main.py` + `config.py` + `scraper.py` + `scoring.py` + `utils.py`** —
  `proje.py`'nin modüllere ayrılmış "V3" yeniden yazımı. Mantık olarak
  `proje.py` ile aynı işi hedefler ancak henüz Dockerfile/deploy sürecine
  bağlanmadı; geliştirme/refaktör aşamasında.

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

Bir Flutter mobil istemcisi (`mobil_app/`, bu depoda takip edilmiyor) API'nin
`/api/halkarzlar` uç noktasına doğrudan bağlanacak şekilde tasarlandı.

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

# Deploy edilen sürüm:
uvicorn proje:app --reload

# Geliştirme aşamasındaki modüler V3 sürümü:
uvicorn main:app --reload
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
- `mobil_app/` — Flutter istemcisi (bu depoda izlenmiyor).
