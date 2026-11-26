// Aktif arz yokken gösterilen "Son tamamlanan arzlar" bölümü ve kaynak
// hatası ekranı.
//
// BİLEREK yalın: bu bölümde yıldız, puan, renkli kalite göstergesi veya
// "avantaj/zayıf yön" gibi değerlendirme YOK. Yalnızca veri gösterilir:
// şirket adı, kod, tarih, fiyat, büyüklük, dağıtım yöntemi, katılımcı sayısı.

import 'package:flutter/material.dart';

const String aktifArzYokMetni =
    'Şu an aktif halka arz bulunmuyor. Aşağıda son tamamlanan arzların '
    'verileri listelenmiştir.';

const Color _yuzey = Color(0xFF12161F);
const Color _kenar = Color(0xFF262C38);
const Color _metin = Color(0xFFF3F5F8);
const Color _ikincil = Color(0xFF9AA2B4);
const Color _soluk = Color(0xFF5D6577);
const Color _vurgu = Color(0xFFC9A24B);

String _binlik(num deger) {
  final String rakamlar = deger.round().abs().toString();
  final StringBuffer sb = StringBuffer();
  for (int i = 0; i < rakamlar.length; i++) {
    if (i > 0 && (rakamlar.length - i) % 3 == 0) sb.write('.');
    sb.write(rakamlar[i]);
  }
  return (deger < 0 ? '-' : '') + sb.toString();
}

/// "2026-09-11" -> "11.09.2026"; çözülemezse null.
String? tarihGoster(dynamic iso) {
  final DateTime? t = iso is String ? DateTime.tryParse(iso) : null;
  if (t == null) return null;
  String iki(int n) => n.toString().padLeft(2, '0');
  return '${iki(t.day)}.${iki(t.month)}.${t.year}';
}

/// 25.52 -> "25,52 TL"
String? fiyatGoster(dynamic f) =>
    f is num && f > 0 ? '${f.toStringAsFixed(2).replaceAll('.', ',')} TL' : null;

/// 2233000000 -> "2,23 milyar TL"; 850000000 -> "850 milyon TL"
String? buyuklukGoster(dynamic b) {
  if (b is! num || b <= 0) return null;
  if (b >= 1e9) {
    return '${(b / 1e9).toStringAsFixed(2).replaceAll('.', ',')} milyar TL';
  }
  return '${_binlik(b / 1e6)} milyon TL';
}

String? katilimciGoster(dynamic k) =>
    k is num && k > 0 ? '${_binlik(k)} kişi' : null;

/// Bilgi satırı + "Son tamamlanan arzlar" başlığı + kartlar.
class SonTamamlananlarBolumu extends StatelessWidget {
  final List<dynamic> arzlar;
  const SonTamamlananlarBolumu({super.key, required this.arzlar});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: _yuzey,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: _kenar),
          ),
          child: const Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.event_available_rounded, size: 20, color: _ikincil),
              SizedBox(width: 10),
              Expanded(
                child: Text(
                  aktifArzYokMetni,
                  style: TextStyle(fontSize: 14, height: 1.45, color: _metin),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 18),
        const Text(
          'Son tamamlanan arzlar',
          style: TextStyle(
              fontSize: 17, fontWeight: FontWeight.w700, color: _metin),
        ),
        const SizedBox(height: 10),
        for (final a in arzlar)
          if (a is Map) SonArzKarti(arz: a.cast<String, dynamic>()),
      ],
    );
  }
}

/// Tek bir tamamlanmış arzın yalın veri kartı (dokunulmaz, detay yok).
class SonArzKarti extends StatelessWidget {
  final Map<String, dynamic> arz;
  const SonArzKarti({super.key, required this.arz});

  @override
  Widget build(BuildContext context) {
    final String ad = (arz['sirket'] ?? '').toString();
    final String kod = (arz['kod'] ?? '').toString();
    final satirlar = <(String, String?)>[
      ('Talep toplama bitişi', tarihGoster(arz['talep_bit'])),
      ('İşlem başlangıcı', tarihGoster(arz['islem'])),
      ('Arz fiyatı', fiyatGoster(arz['fiyat'])),
      ('Arz büyüklüğü', buyuklukGoster(arz['arz'])),
      ('Dağıtım yöntemi', (arz['dagitim'] as String?)),
      ('Katılımcı sayısı', katilimciGoster(arz['katilimci'])),
    ].where((s) => s.$2 != null && s.$2!.isNotEmpty).toList();

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      decoration: BoxDecoration(
        color: _yuzey,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: _kenar),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  ad,
                  style: const TextStyle(
                      fontSize: 15.5,
                      fontWeight: FontWeight.w700,
                      color: _metin,
                      height: 1.3),
                ),
              ),
              if (kod.isNotEmpty) ...[
                const SizedBox(width: 10),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: _kenar),
                  ),
                  child: Text(kod,
                      style: const TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: _ikincil,
                          letterSpacing: 0.5)),
                ),
              ],
            ],
          ),
          const SizedBox(height: 10),
          for (final s in satirlar)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    flex: 5,
                    child: Text(s.$1,
                        style: const TextStyle(fontSize: 13, color: _ikincil)),
                  ),
                  Expanded(
                    flex: 6,
                    child: Text(s.$2!,
                        textAlign: TextAlign.right,
                        style: const TextStyle(
                            fontSize: 13.5,
                            color: _metin,
                            fontWeight: FontWeight.w600)),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Kaynak site okunamadığında (çöktü / yapısı değişti) gösterilen ekran.
/// "Aktif arz yok" ekranından bilerek farklı: kullanıcı listenin boş
/// olmadığını, verinin şu an alınamadığını anlamalı.
class KaynakHatasiGorunumu extends StatelessWidget {
  final VoidCallback onTekrar;
  const KaynakHatasiGorunumu({super.key, required this.onTekrar});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.sync_problem_rounded, size: 64, color: _vurgu),
            const SizedBox(height: 18),
            const Text(
              'Veri kaynağına şu an ulaşılamıyor',
              textAlign: TextAlign.center,
              style: TextStyle(
                  fontSize: 19, fontWeight: FontWeight.w800, color: _metin),
            ),
            const SizedBox(height: 10),
            const Text(
              'Halka arz bilgilerini aldığımız kaynak siteye şu anda erişilemiyor '
              'veya sitenin yapısı değişmiş olabilir. Bu, aktif halka arz '
              'olmadığı anlamına gelmez. Biraz sonra tekrar deneyin.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 14, height: 1.5, color: _ikincil),
            ),
            const SizedBox(height: 24),
            ElevatedButton.icon(
              style: ElevatedButton.styleFrom(
                backgroundColor: _vurgu,
                foregroundColor: const Color(0xFF0A0D14),
                minimumSize: const Size(180, 48),
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14)),
              ),
              onPressed: onTekrar,
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('Tekrar dene',
                  style: TextStyle(fontWeight: FontWeight.w700)),
            ),
            const SizedBox(height: 14),
            const Text(
              'Kaynak: halkarz.com',
              style: TextStyle(fontSize: 12, color: _soluk),
            ),
          ],
        ),
      ),
    );
  }
}
