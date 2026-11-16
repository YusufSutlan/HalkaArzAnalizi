import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:mobil_app/hakkinda.dart';
import 'package:mobil_app/main.dart';

void main() {
  testWidgets('İlk açılışta yatırım uyarısı ve veri kaynakları gösterilir',
      (WidgetTester tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const HalkaArzApp());
    await tester.pumpAndSettle();

    expect(find.text('Yatırım tavsiyesi değildir'), findsOneWidget);
    expect(find.text('Veri kaynakları'), findsOneWidget);
    expect(find.text('Okudum, Anladım'), findsOneWidget);
  });

  testWidgets('Hakkında ekranı sürüm, gizlilik ve iletişim bilgisini içerir',
      (WidgetTester tester) async {
    tester.view.physicalSize = const Size(1290, 2796);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const MaterialApp(home: HakkindaEkrani()));
    await tester.pump();

    expect(find.text('Halka Arz Analiz'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Web Sitesi'), 300);
    expect(find.text('Gizlilik Politikası'), findsOneWidget);
    expect(find.text(UygulamaBilgisi.eposta), findsOneWidget);
    expect(find.text('Web Sitesi'), findsOneWidget);
  });
}
