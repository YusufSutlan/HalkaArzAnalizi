import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:mobil_app/main.dart';

Map<String, dynamic> _ornekArz({bool yeniAlanlar = true}) {
  final m = <String, dynamic>{
    'sirket': 'Örnek Makine Sanayi A.Ş.',
    'bist_kodu': 'ORNEK',
    'durum': 'Talep Toplanıyor',
    'skor': 72.4,
    'temel_kalite_skoru': 72.4,
    'tavan_potansiyeli_skoru': 41.0,
    'risk_skoru': 18.5,
    'sektor': 'SANAYİ',
    'genel_degerlendirme': 'Örnek değerlendirme metni.',
    'guclu_yanlar': ['[karlilik] Güçlü kârlılık. (+8.0 Puan)'],
    'riskler': <String>[],
    'kirmizi_bayraklar': <String>[],
    'uyarilar': ['ℹ️ İlk gün satış baskısı orta seviyede.'],
    'ilk_gun_satis_baskisi': 48.0,
    'ilk_gun_baski_seviyesi': 'Orta',
    'ilk_gun_baski_uyarisi': 'ℹ️ İlk gün satış baskısı orta seviyede.',
    'ilk_gun_baski_gerekceleri': ['Tahmini kişi başı dağıtım ~2.300 TL.'],
    'kisi_basi_tahmini_tutar': 2300,
    'kisi_basi_tahmini_lot': 115,
    'ayni_hafta_arz_sayisi': 1,
    'rakip_arzlar': <String>[],
    'veri_guvenilirligi': 88,
    'skor_guvenilir': true,
    'finansal_veri_var': true,
    'tahmini_volatilite': 'Orta (piyasa koşullarına bağlı)',
    'fiyat': '20,00 TL',
    'fiyat_sayi': 20.0,
    'pay_sayisi_sayi': 60000000,
    'tarih': '12-13 Kasım 2026',
    'islem_tarihi': 'Açıklanmadı',
    'buyukluk': '1,2 Milyar TL',
    'dagitim_yontemi': 'Eşit Dağıtım',
    'pazar': 'Yıldız Pazar',
    'araci_kurum': 'Örnek Yatırım',
    'aciklik': '%25',
    'iskonto': '%20',
    'fiyat_istikrari': '30 gün',
    'fon_kullanim': '%70 Yeni tesis',
    'halka_arz_sekli': 'Sermaye Artırımı : 60.000.000 Lot',
    'taahhut': '1 Yıl',
    'finansal_tablo': 'Açıklanmadı.',
    'tahsisat': 'Henüz açıklanmadı.',
    'dagitim_tablosu': 'Lot tablosu bulunamadı.',
    'dagitim_tipi': 'Tahmini Lot Tablosu',
    'katilim_endeksine_uygun': true,
    't1_t2_kullanilabilir': true,
    'degerleme': {
      'genel_yorum':
          'Halka arz fiyatındaki çarpanlar, kıyas alınan değerlerin ALTINDA.',
      'piyasa_degeri': 4800000000,
      'carpanlar': [
        {
          'ad': 'F/K',
          'deger': 21.5,
          'sektor_ortalamasi': 36.1,
          'yorum': 'Emsallerin altında',
          'aciklama': 'Açıklama.',
        },
        {
          'ad': 'FD/FAVÖK',
          'deger': 9.1,
          'sektor_ortalamasi': null,
          'yorum': 'Karşılaştırma verisi yok',
          'aciklama': 'Açıklama.',
        },
      ],
    },
  };
  if (yeniAlanlar) {
    m.addAll({
      'kisa_vade_olculebilir': true,
      'beklenen_katilimci': 520000,
      'bist_momentum_60g': -0.18,
      'kisa_vade_etkenleri': [
        {'ad': 'Kişi başı dağıtım', 'puan': -4.0, 'etki': 'olumsuz', 'aciklama': '~2.300 TL.'},
        {'ad': 'Piyasa momentumu', 'puan': 0.0, 'etki': 'bilinmiyor', 'aciklama': 'Veri yok.'},
        {'ad': 'Temel kalite', 'puan': 8.6, 'etki': 'olumlu', 'aciklama': '72/100.'},
      ],
      'benzer_arzlar': {
        'ornek_sayisi': 31,
        'donem': '2023-04 – 2026-08',
        'tavan_medyan': 3.5,
        'tavan_alt_ceyrek': 1,
        'tavan_ust_ceyrek': 6,
        'tavansiz_yuzde': 22,
        'getiri_20g_ekside_yuzde': 35,
      },
    });
  }
  return m;
}

Future<void> _ac(WidgetTester tester, Map<String, dynamic> arz,
    {double metinOlcegi = 1.0}) async {
  // Küçük ekran (iPhone SE genişliği) + isteğe bağlı büyük yazı tipi:
  // taşma hataları en kolay burada ortaya çıkar.
  tester.view.physicalSize = const Size(640, 1136);
  tester.view.devicePixelRatio = 2.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: ThemeData.dark(),
    home: MediaQuery(
      data: MediaQueryData(
        size: const Size(320, 568),
        textScaler: TextScaler.linear(metinOlcegi),
      ),
      child: HalkaArzDetaySayfasi(arz: arz, renk: Colors.green),
    ),
  ));
  await tester.pump();
  // Sayfanın tamamını çizdir: aşağıdaki widget'lar tembel değil ama
  // kaydırma sırasında oluşabilecek hataları da yakalamak için.
  await tester.drag(find.byType(SingleChildScrollView).first, const Offset(0, -6000));
  await tester.pump();
}

void main() {
  testWidgets('Detay sayfası yeni sunucu alanlarıyla hatasız çizilir',
      (tester) async {
    await _ac(tester, _ornekArz());
    expect(tester.takeException(), isNull);
    expect(find.text('KISA VADE GÖSTERGESİ', skipOffstage: false), findsOneWidget);
    expect(find.textContaining('benzer olan 31 halka arz', skipOffstage: false),
        findsOneWidget);
    // Lot hesaplayıcı sunucunun katılımcı beklentisini kullanır (eski sabit
    // 2.500.000 değil): 60.000.000 / 520.000 = 115 lot.
    expect(find.text('115', skipOffstage: false), findsWidgets);
  });

  testWidgets('Eski sunucu yanıtıyla (yeni alanlar yok) da hatasız çizilir',
      (tester) async {
    await _ac(tester, _ornekArz(yeniAlanlar: false));
    expect(tester.takeException(), isNull);
    expect(find.text('KISA VADE GÖSTERGESİ', skipOffstage: false), findsNothing);
  });

  testWidgets('Büyük yazı tipinde ve dar ekranda taşma olmaz', (tester) async {
    await _ac(tester, _ornekArz(), metinOlcegi: 1.5);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Neredeyse boş veriyle çökmeden açılır', (tester) async {
    await _ac(tester, {'sirket': '', 'durum': 'Hazırlanıyor'});
    expect(tester.takeException(), isNull);
  });
}
