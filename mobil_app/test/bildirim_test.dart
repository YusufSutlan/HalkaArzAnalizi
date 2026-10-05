import 'package:flutter_test/flutter_test.dart';
import 'package:mobil_app/bildirimler.dart';

void main() {
  final arzlar = [
    {
      'sirket': 'Örnek Enerji A.Ş.',
      'bist_kodu': 'ORNEK',
      'talep_baslangic': '2026-10-08',
      'talep_bitis': '2026-10-10',
      'islem_baslangic': '2026-10-16',
    },
    {
      // Tek günlük talep: "son gün" ayrıca bildirilmemeli
      'sirket': 'Tek Gün A.Ş.',
      'bist_kodu': 'Belli Değil',
      'talep_baslangic': '2026-10-09',
      'talep_bitis': '2026-10-09',
      'islem_baslangic': null,
    },
    {'sirket': 'Tarihsiz A.Ş.', 'talep_baslangic': null},
  ];

  test('gelecekteki tüm hatırlatmalar doğru saatte üretilir', () {
    final p = hatirlatmalariUret(arzlar, DateTime(2026, 10, 5, 10, 0));
    final ozet = p.map((b) => '${b.zaman} ${b.baslik}').toList();
    expect(ozet, [
      '2026-10-08 09:00:00.000 Talep toplama başladı: ORNEK',
      '2026-10-10 09:00:00.000 Talep toplamada son gün: ORNEK',
      '2026-10-16 09:45:00.000 ORNEK bugün borsada',
      '2026-10-09 09:00:00.000 Talep toplama başladı: Tek Gün A.Ş.',
    ]);
  });

  test('geçmiş saatler planlanmaz', () {
    // 8 Ekim 09:30: başlangıç bildirimi geçti, son gün ve işlem kaldı
    final p = hatirlatmalariUret([arzlar[0]], DateTime(2026, 10, 8, 9, 30));
    expect(p.map((b) => b.baslik), [
      'Talep toplamada son gün: ORNEK',
      'ORNEK bugün borsada',
    ]);
  });

  test('sadece favoriler filtresi', () {
    final p = hatirlatmalariUret(arzlar, DateTime(2026, 10, 5),
        sadeceBunlar: {'Tek Gün A.Ş.'});
    expect(p.length, 1);
    expect(p.first.baslik, contains('Tek Gün'));
  });

  test('bildirim kimlikleri kararlı ve farklı', () {
    expect(kararliKimlik('a|talep'), kararliKimlik('a|talep'));
    expect(kararliKimlik('a|talep'), isNot(kararliKimlik('a|sonGun')));
    expect(kararliKimlik('x' * 500), greaterThanOrEqualTo(0));
  });
}
