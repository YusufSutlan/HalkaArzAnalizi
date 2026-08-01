# ═══════════════════════════════════════════════════════════════
# Bu dosya NE YAPAR?
#
# Render sunucusuna, PDF içindeki yazıyı okuyabilen "tesseract"
# programını kurar. İzahnameler taranmış (fotoğraf gibi) olduğu için
# bu program olmadan içindeki rakamlar okunamıyor.
#
# Yapay zeka ile ilgisi YOKTUR. Sadece bir OCR (görüntüden yazı
# tanıma) programı kuruluyor.
# ═══════════════════════════════════════════════════════════════

FROM python:3.12-slim

# Sistem paketleri:
#   tesseract-ocr      -> görüntüden yazı okuma motoru
#   tesseract-ocr-tur  -> TÜRKÇE dil paketi. Bu olmadan "ğ, ş, ı, İ"
#                         harfleri yanlış okunur ve kalem adları
#                         ("Dönem Net Kârı") tanınmaz.
#   poppler-utils      -> PDF sayfalarını görüntüye çevirmek için
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-tur \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Önce sadece requirements kopyalanır; böylece kod değiştiğinde
# kütüphaneler yeniden kurulmaz (deploy hızlanır).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render PORT değişkenini kendi atar; sabit port yazılmaz.
CMD uvicorn proje:app --host 0.0.0.0 --port $PORT