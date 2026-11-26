// "Son tamamlanan arzlar", kaynak hatası ekranı ve yanıt çözücü testleri.
// Ağ yok: ana ekran testleri http.runWithClient + MockClient ile çalışır.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:mobil_app/arz_yaniti.dart';
import 'package:mobil_app/main.dart';
import 'package:mobil_app/son_arzlar.dart';

const Map<String, dynamic> _netgl = {
  'sirket': 'Örnek Endüstriyel Yatırımlar A.Ş.',
  'kod': 'ORNEK',
  'talep_bit': '2026-09-11',
  'islem': '2026-09-17',
  'fiyat': 25.52,
  'arz': 2233000000.0,
  'dagitim': 'Eşit Dağıtım',
  'katilimci': 652248,
};

const Map<String, dynamic> _eksik = {
  'sirket': 'Eksik Veri A.Ş.',
  'kod': 'EKSIK',
  'talep_bit': '2026-08-27',
  'fiyat': 12.0,
  'arz': 850000000.0,
};

String _govde(Map<String, dynamic> m) => jsonEncode(m);

final String _veriYok = _govde({
  'status': 'veri_yok',
  'halka_arzlar': [],
  'son_tamamlananlar': [_netgl, _eksik],
  'uyari': 'Yatırım tavsiyesi değildir.',
});

final String _kaynakHatasi = _govde({
  'status': 'kaynak_hatasi',
  'detail': 'Kaynak sitenin sayfa yapısı okunamadı.',
  'halka_arzlar': [],
});

Widget _uygula(Widget w) => MaterialApp(
      theme: ThemeData(brightness: Brightness.dark),
      home: Scaffold(body: SingleChildScrollView(child: w)),
    );

void _buyukEkran(WidgetTester tester) {
  tester.view.physicalSize = const Size(1290, 2796);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.reset);
}

void main() {
  group('arzYanitiCoz', () {
    test('ok: arzlar dolu', () {
      final y = arzYanitiCoz(
          200, _govde({'status': 'ok', 'halka_arzlar': [{'sirket': 'A'}]}))!;
      expect(y.durum, YanitDurumu.arzVar);
      expect(y.arzlar.length, 1);
    });

    test('veri_yok: son tamamlananlar gelir', () {
      final y = arzYanitiCoz(200, _veriYok)!;
      expect(y.durum, YanitDurumu.veriYok);
      expect(y.arzlar, isEmpty);
      expect(y.sonTamamlananlar.length, 2);
    });

    test('kaynak_hatasi (503) veri_yok ile karışmaz', () {
      final y = arzYanitiCoz(503, _kaynakHatasi)!;
      expect(y.durum, YanitDurumu.kaynakHatasi);
      expect(y.hataDetayi, contains('okunamadı'));
    });

    test('status alanı olmayan eski sunucu yanıtı desteklenir', () {
      expect(arzYanitiCoz(200, _govde({'halka_arzlar': []}))!.durum,
          YanitDurumu.veriYok);
      expect(
          arzYanitiCoz(200, _govde({'halka_arzlar': [{'sirket': 'A'}]}))!.durum,
          YanitDurumu.arzVar);
    });

    test('Render uyanırken dönen HTML / bozuk JSON / bilinmeyen 5xx -> null',
        () {
      expect(arzYanitiCoz(502, '<html>Bad Gateway</html>'), isNull);
      expect(arzYanitiCoz(200, '{bozuk'), isNull);
      expect(arzYanitiCoz(500, _govde({'status': 'sunucu_hatasi'})), isNull);
      expect(arzYanitiCoz(200, '[1,2]'), isNull);
    });
  });

  group('biçimlendirme', () {
    test('tarih, fiyat, büyüklük, katılımcı', () {
      expect(tarihGoster('2026-09-11'), '11.09.2026');
      expect(tarihGoster(null), isNull);
      expect(fiyatGoster(25.52), '25,52 TL');
      expect(buyuklukGoster(2233000000.0), '2,23 milyar TL');
      expect(buyuklukGoster(850000000.0), '850 milyon TL');
      expect(katilimciGoster(652248), '652.248 kişi');
      expect(katilimciGoster(null), isNull);
    });
  });

  group('SonTamamlananlarBolumu', () {
    testWidgets('bilgi satırı ve yalın veriler gösterilir', (tester) async {
      _buyukEkran(tester);
      await tester.pumpWidget(
          _uygula(const SonTamamlananlarBolumu(arzlar: [_netgl, _eksik])));

      expect(find.text(aktifArzYokMetni), findsOneWidget);
      expect(find.text('Son tamamlanan arzlar'), findsOneWidget);
      expect(find.text('Örnek Endüstriyel Yatırımlar A.Ş.'), findsOneWidget);
      expect(find.text('ORNEK'), findsOneWidget);
      expect(find.text('11.09.2026'), findsOneWidget);
      expect(find.text('25,52 TL'), findsOneWidget);
      expect(find.text('2,23 milyar TL'), findsOneWidget);
      expect(find.text('Eşit Dağıtım'), findsOneWidget);
      expect(find.text('652.248 kişi'), findsOneWidget);
    });

    testWidgets('eksik alanın satırı hiç çizilmez', (tester) async {
      _buyukEkran(tester);
      await tester.pumpWidget(
          _uygula(const SonTamamlananlarBolumu(arzlar: [_eksik])));
      expect(find.text('Dağıtım yöntemi'), findsNothing);
      expect(find.text('Katılımcı sayısı'), findsNothing);
      expect(find.text('İşlem başlangıcı'), findsNothing);
      expect(find.text('Arz fiyatı'), findsOneWidget);
    });

    testWidgets('yıldız, puan, kalite veya avantaj/zayıf yön YOK',
        (tester) async {
      _buyukEkran(tester);
      // Sunucu yanlışlıkla puan alanı gönderse bile gösterilmemeli.
      await tester.pumpWidget(_uygula(SonTamamlananlarBolumu(arzlar: [
        {..._netgl, 'kalite': 70, 'skor': 70, 'yildiz': '★★★★☆'},
      ])));
      final metinler = tester
          .widgetList<Text>(find.byType(Text))
          .map((t) => t.data ?? '')
          .join(' | ');
      for (final yasak in [
        '★',
        '☆',
        'Puan',
        'puan',
        'Kalite',
        'Avantaj',
        'Zayıf',
        '/100'
      ]) {
        expect(metinler.contains(yasak), isFalse, reason: 'bulundu: $yasak');
      }
    });

    testWidgets('büyük yazı tipinde dar ekranda taşma olmaz', (tester) async {
      tester.view.physicalSize = const Size(320 * 3, 640 * 3);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MediaQuery(
        data: const MediaQueryData(textScaler: TextScaler.linear(1.5)),
        child: _uygula(const SonTamamlananlarBolumu(arzlar: [_netgl, _eksik])),
      ));
      expect(tester.takeException(), isNull);
    });
  });

  group('KaynakHatasiGorunumu', () {
    testWidgets('açıklama ve çalışan "Tekrar dene" butonu', (tester) async {
      _buyukEkran(tester);
      int basildi = 0;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: KaynakHatasiGorunumu(onTekrar: () => basildi++))));
      expect(find.text('Veri kaynağına şu an ulaşılamıyor'), findsOneWidget);
      expect(find.textContaining('aktif halka arz olmadığı anlamına gelmez'),
          findsOneWidget);
      await tester.tap(find.text('Tekrar dene'));
      expect(basildi, 1);
    });
  });

  group('AnaEkran', () {
    setUp(() {
      SharedPreferences.setMockInitialValues({
        'sozlesme_onay': true,
        'bildirim_izin_istendi': true,
      });
    });

    Future<void> ac(WidgetTester tester, List<http.Response> yanitlar,
        List<int> sayac) async {
      _buyukEkran(tester);
      final istemci = MockClient((_) async {
        final r = yanitlar[sayac[0].clamp(0, yanitlar.length - 1)];
        sayac[0]++;
        return r;
      });
      await http.runWithClient(() async {
        await tester.pumpWidget(const MaterialApp(home: AnaEkran()));
        await tester.pumpAndSettle();
      }, () => istemci);
    }

    http.Response yanit(String govde, int kod) => http.Response.bytes(
        utf8.encode(govde), kod,
        headers: {'content-type': 'application/json; charset=utf-8'});

    testWidgets('aktif arz yokken son tamamlanan arzlar listelenir',
        (tester) async {
      final sayac = [0];
      await ac(tester, [yanit(_veriYok, 200)], sayac);
      expect(find.text(aktifArzYokMetni), findsOneWidget);
      expect(find.text('Örnek Endüstriyel Yatırımlar A.Ş.'), findsOneWidget);
      expect(find.text('Veri kaynağına şu an ulaşılamıyor'), findsNothing);
      expect(sayac[0], 1);
    });

    testWidgets(
        'kaynak hatasında ayrı ekran; otomatik tekrar yok; Tekrar dene çalışır',
        (tester) async {
      final sayac = [0];
      await ac(tester, [yanit(_kaynakHatasi, 503), yanit(_veriYok, 200)],
          sayac);
      expect(find.text('Veri kaynağına şu an ulaşılamıyor'), findsOneWidget);
      expect(find.text(aktifArzYokMetni), findsNothing);
      expect(find.text('Şu an aktif halka arz yok'), findsNothing);
      expect(sayac[0], 1, reason: 'kaynak hatasında otomatik tekrar denenmez');

      await http.runWithClient(() async {
        await tester.tap(find.text('Tekrar dene'));
        await tester.pumpAndSettle();
      }, () => MockClient((_) async {
            sayac[0]++;
            return yanit(_veriYok, 200);
          }));
      expect(find.text(aktifArzYokMetni), findsOneWidget);
      expect(find.text('Veri kaynağına şu an ulaşılamıyor'), findsNothing);
    });
  });
}
