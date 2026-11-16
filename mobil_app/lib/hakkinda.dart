// Hakkında ekranı ve uygulama genelinde kullanılan yasal uyarı metinleri.
//
// Mağaza incelemesi (Apple 3.1/5.1, Google Play finansal hizmetler politikası)
// için yatırım tavsiyesi olmadığı ve verilerin kaynağı uygulama içinde açıkça
// görünmeli: ilk açılışta bir kez (SozlesmeEkrani) + bu ekranda kalıcı olarak.

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

class UygulamaBilgisi {
  UygulamaBilgisi._();
  static const String ad = 'Halka Arz Analiz';
  static const String gelistirici = 'STL TECHNOLOGY';
  static const String web = 'https://stltechnology.com.tr';
  static const String gizlilik =
      'https://stltechnology.com.tr/gizlilik-politikasi.html';
  static const String eposta = 'stlteknoloji.2026@gmail.com';
}

/// Kısa uyarı: liste ve detay sayfalarının altında.
const String kisaYatirimUyarisi =
    'Yatırım tavsiyesi değildir; puanlar ve yorumlar yalnızca bilgilendirme '
    'amaçlıdır.';

/// Tam uyarı metni: ilk açılış ve Hakkında ekranı.
const String yatirimUyarisiMetni =
    'Bu uygulamadaki bilgiler, puanlar, analizler ve hesaplamalar yalnızca '
    'bilgilendirme amaçlıdır; YATIRIM TAVSİYESİ DEĞİLDİR. 6362 sayılı Sermaye '
    'Piyasası Kanunu kapsamında yatırım danışmanlığı hizmeti, yetkili kuruluşlar '
    'ile kişiye özel sözleşme çerçevesinde verilir.\n\n'
    'Veriler otomatik olarak toplanır ve işlenir; eksik, gecikmeli veya hatalı '
    'olabilir. Puanlar geçmiş verilere dayanan bir modelin çıktısıdır ve gelecek '
    'performansı garanti etmez. Yatırım kararı vermeden önce şirketin '
    'izahnamesini ve KAP açıklamalarını kendiniz inceleyin. Bu bilgilere '
    'dayanarak alınan kararlardan doğabilecek zararlardan uygulama ve '
    'geliştiricisi sorumlu tutulamaz.';

const String veriKaynaklariMetni =
    'Halka arz takvimi ve arz bilgileri kamuya açık halka arz duyurularından '
    '(halkarz.com) derlenir. Finansal tablolar ve şirket bilgileri, Kamuyu '
    'Aydınlatma Platformu (KAP) ve SPK onaylı izahnamelerden otomatik olarak '
    'çıkarılır; bu işlemde yapay zekâ destekli metin okuma kullanılır ve hata '
    'payı bulunabilir. Endeks ve geçmiş fiyat verileri üçüncü taraf fiyat '
    'sağlayıcılardan alınır ve gecikmeli olabilir. Uygulama Borsa İstanbul, SPK '
    'veya KAP ile bağlantılı değildir.';

const String yontemMetni =
    'Temel kalite puanı; kârlılık, büyüme, borçluluk, likidite, nakit akışı, '
    'değerleme ve arz yapısını izahname verilerinden puanlar. Bu puanın finansal '
    'boyutları kurama dayanır; yeterli örnek olmadığı için istatistiksel olarak '
    'doğrulanmamıştır.\n\n'
    'Kısa vade göstergesi ve ilk gün satış baskısı; kişi başına düşen tahmini '
    'tutar, BIST 100\'ün son 60 işlem günündeki seyri, arz büyüklüğü ve temel '
    'kaliteden hesaplanır. 2023\'ten bu yana tamamlanan halka arzların gerçek '
    'sonuçlarıyla geriye dönük olarak sınanmıştır. "Geçmişte benzer arzlar" '
    'bölümü bir tahmin değil, geçmişin özetidir.\n\n'
    'Veri eksikse puanlar buna göre işaretlenir. Hiçbir puan veya gösterge '
    'getiri vaadi değildir.';

Future<void> baglantiAc(BuildContext context, Uri uri) async {
  bool acildi = false;
  try {
    acildi = await launchUrl(uri, mode: LaunchMode.externalApplication);
  } catch (_) {}
  if (!acildi && context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Açılamadı: ${uri.toString().replaceFirst('mailto:', '')}')),
    );
  }
}

/// Liste ve detay sayfalarının sonuna eklenen küçük uyarı notu.
class YatirimUyarisiNotu extends StatelessWidget {
  const YatirimUyarisiNotu({super.key});
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 10, 4, 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.info_outline_rounded,
              size: 15, color: Color(0xFF5D6577)),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              '$kisaYatirimUyarisi Kaynak: halkarz.com, KAP, izahnameler.',
              style: const TextStyle(
                  fontSize: 11.5, height: 1.4, color: Color(0xFF5D6577)),
            ),
          ),
        ],
      ),
    );
  }
}

class HakkindaEkrani extends StatelessWidget {
  const HakkindaEkrani({super.key});

  static const Color _yuzey = Color(0xFF12161F);
  static const Color _kenar = Color(0xFF262C38);
  static const Color _vurgu = Color(0xFFC9A24B);
  static const Color _metin = Color(0xFFF3F5F8);
  static const Color _ikincil = Color(0xFF9AA2B4);

  Widget _bolum(String baslik, String metin, IconData ikon, Color renk) {
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: _yuzey,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: _kenar),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Icon(ikon, size: 19, color: renk),
            const SizedBox(width: 8),
            Expanded(
              child: Text(baslik,
                  style: const TextStyle(
                      fontSize: 15.5, fontWeight: FontWeight.w700, color: _metin)),
            ),
          ]),
          const SizedBox(height: 10),
          Text(metin,
              style: const TextStyle(fontSize: 13.5, height: 1.5, color: _ikincil)),
        ],
      ),
    );
  }

  Widget _baglanti(BuildContext context, IconData ikon, String baslik,
      String alt, Uri uri) {
    return ListTile(
      leading: Icon(ikon, color: _vurgu),
      title: Text(baslik,
          style: const TextStyle(color: _metin, fontWeight: FontWeight.w600)),
      subtitle: Text(alt, style: const TextStyle(color: _ikincil, fontSize: 12.5)),
      trailing: const Icon(Icons.open_in_new_rounded, size: 18, color: _ikincil),
      onTap: () => baglantiAc(context, uri),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Hakkında')),
      body: ListView(
        padding: EdgeInsets.fromLTRB(
            20, 8, 20, 32 + MediaQuery.paddingOf(context).bottom),
        children: [
          const SizedBox(height: 8),
          Center(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(18),
              child: Image.asset('assets/icon/app_icon.png', width: 72, height: 72),
            ),
          ),
          const SizedBox(height: 12),
          const Center(
            child: Text(UygulamaBilgisi.ad,
                style: TextStyle(
                    fontSize: 20, fontWeight: FontWeight.w800, color: _metin)),
          ),
          const SizedBox(height: 4),
          Center(
            child: FutureBuilder<PackageInfo>(
              future: PackageInfo.fromPlatform(),
              builder: (context, s) => Text(
                s.hasData
                    ? 'Sürüm ${s.data!.version} (${s.data!.buildNumber})'
                    : ' ',
                style: const TextStyle(fontSize: 13, color: _ikincil),
              ),
            ),
          ),
          const SizedBox(height: 22),
          _bolum('Yatırım tavsiyesi değildir', yatirimUyarisiMetni,
              Icons.gavel_rounded, const Color(0xFFEBA23B)),
          _bolum('Veri kaynakları', veriKaynaklariMetni,
              Icons.source_outlined, const Color(0xFF6C8EEF)),
          _bolum('Puanlar nasıl hesaplanır?', yontemMetni,
              Icons.functions_rounded, const Color(0xFF3FCE8E)),
          Material(
            color: _yuzey,
            clipBehavior: Clip.antiAlias,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: _kenar),
            ),
            child: Column(children: [
              _baglanti(context, Icons.privacy_tip_outlined, 'Gizlilik Politikası',
                  'stltechnology.com.tr', Uri.parse(UygulamaBilgisi.gizlilik)),
              const Divider(height: 1, color: _kenar),
              _baglanti(
                  context,
                  Icons.mail_outline_rounded,
                  'İletişim',
                  UygulamaBilgisi.eposta,
                  Uri(
                    scheme: 'mailto',
                    path: UygulamaBilgisi.eposta,
                    query: 'subject=${Uri.encodeComponent('${UygulamaBilgisi.ad} - Geri bildirim')}',
                  )),
              const Divider(height: 1, color: _kenar),
              _baglanti(context, Icons.language_rounded, 'Web Sitesi',
                  'stltechnology.com.tr', Uri.parse(UygulamaBilgisi.web)),
            ]),
          ),
          const SizedBox(height: 22),
          const Center(
            child: Text('© 2026 ${UygulamaBilgisi.gelistirici}',
                style: TextStyle(fontSize: 12, color: Color(0xFF5D6577))),
          ),
        ],
      ),
    );
  }
}
