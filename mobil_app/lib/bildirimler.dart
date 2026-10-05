// Halka arz bildirimleri.
//
// NEDEN YEREL BİLDİRİM: Gerçek push bildirimi iOS'ta ücretli Apple
// Developer hesabı (APNs) gerektiriyor. Bunun yerine:
//   1. Tarihe bağlı hatırlatmalar (talep başlıyor / son gün / işlem başlıyor)
//      cihazın kendisinde önceden planlanır; uygulama kapalıyken de gelir.
//   2. Yeni bir arz duyurulduğunda haber vermek için arka planda düzenli
//      kontrol yapılır (Android WorkManager / iOS BGTaskScheduler). Android'de
//      güvenilirdir; iOS çalışma zamanını kendisi seçer, gecikebilir.

import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timezone/data/latest.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;
import 'package:workmanager/workmanager.dart';

/// iOS'ta AppDelegate.swift ve Info.plist içindeki kimlikle aynı olmalı.
const String arzKontrolGorevi = 'com.yusufsutlan.halkarzasistan.arzKontrol';

/// Kullanıcı ayarları (cihazda saklanır).
class BildirimAyarlari {
  final bool yeniArz;
  final bool hatirlatma;
  final bool sadeceFavoriler;
  const BildirimAyarlari({
    this.yeniArz = true,
    this.hatirlatma = true,
    this.sadeceFavoriler = false,
  });

  static Future<BildirimAyarlari> yukle() async {
    final p = await SharedPreferences.getInstance();
    return BildirimAyarlari(
      yeniArz: p.getBool('bildirim_yeni_arz') ?? true,
      hatirlatma: p.getBool('bildirim_hatirlatma') ?? true,
      sadeceFavoriler: p.getBool('bildirim_sadece_favori') ?? false,
    );
  }

  Future<void> kaydet() async {
    final p = await SharedPreferences.getInstance();
    await p.setBool('bildirim_yeni_arz', yeniArz);
    await p.setBool('bildirim_hatirlatma', hatirlatma);
    await p.setBool('bildirim_sadece_favori', sadeceFavoriler);
  }
}

/// Planlanan tek bir hatırlatma (test edilebilir olsun diye ayrı).
class PlanliBildirim {
  final int id;
  final DateTime zaman; // İstanbul saatiyle
  final String baslik;
  final String govde;
  const PlanliBildirim(this.id, this.zaman, this.baslik, this.govde);
}

/// FNV-1a: String.hashCode çalıştırmalar arasında sabit değil; bildirim
/// kimliği her seferinde aynı çıkmalı.
int kararliKimlik(String s) {
  int h = 0x811c9dc5;
  for (final c in utf8.encode(s)) {
    h ^= c;
    h = (h * 0x01000193) & 0x7fffffff;
  }
  return h;
}

DateTime? _gun(dynamic iso) {
  if (iso is! String || iso.isEmpty) return null;
  return DateTime.tryParse(iso);
}

/// Bir arz listesinden gelecekteki hatırlatmaları üretir.
/// Saf fonksiyon: platformdan bağımsız, birim testle doğrulanabilir.
List<PlanliBildirim> hatirlatmalariUret(
  List<dynamic> arzlar,
  DateTime simdi, {
  Set<String>? sadeceBunlar,
}) {
  final List<PlanliBildirim> sonuc = [];
  for (final a in arzlar) {
    final String ad = (a['sirket'] ?? '').toString();
    if (ad.isEmpty) continue;
    if (sadeceBunlar != null && !sadeceBunlar.contains(ad)) continue;
    final String kod = (a['bist_kodu'] ?? '').toString();
    final String kisa = (kod.isNotEmpty && kod != 'Belli Değil') ? kod : ad;
    final bas = _gun(a['talep_baslangic']);
    final bit = _gun(a['talep_bitis']);
    final islem = _gun(a['islem_baslangic']);

    void ekle(String tur, DateTime? gun, int saat, int dakika, String b, String g) {
      if (gun == null) return;
      final z = DateTime(gun.year, gun.month, gun.day, saat, dakika);
      if (!z.isAfter(simdi)) return;
      sonuc.add(PlanliBildirim(kararliKimlik('$ad|$tur'), z, b, g));
    }

    ekle('talep', bas, 9, 0, 'Talep toplama başladı: $kisa',
        '$ad için talep toplama bugün başladı.');
    if (bit != null && bas != null && bit.isAfter(bas)) {
      ekle('sonGun', bit, 9, 0, 'Talep toplamada son gün: $kisa',
          '$ad için talep toplama bugün sona eriyor.');
    }
    ekle('islem', islem, 9, 45, '$kisa bugün borsada',
        '$ad bugün Borsa İstanbul\'da işlem görmeye başlıyor.');
  }
  return sonuc;
}

class BildirimServisi {
  BildirimServisi._();
  static final BildirimServisi instance = BildirimServisi._();

  final FlutterLocalNotificationsPlugin _eklenti =
      FlutterLocalNotificationsPlugin();
  bool _hazir = false;

  static const NotificationDetails _detay = NotificationDetails(
    android: AndroidNotificationDetails(
      'halka_arz',
      'Halka arz bildirimleri',
      channelDescription: 'Yeni arzlar ve talep/işlem tarihi hatırlatmaları',
      importance: Importance.high,
      priority: Priority.high,
    ),
    iOS: DarwinNotificationDetails(),
  );

  Future<void> baslat() async {
    if (_hazir) return;
    tzdata.initializeTimeZones();
    tz.setLocalLocation(tz.getLocation('Europe/Istanbul'));
    await _eklenti.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/launcher_icon'),
        // İzin burada değil, kullanıcı ön plandayken ayrıca isteniyor;
        // arka plan görevinde izin penceresi açılmamalı.
        iOS: DarwinInitializationSettings(
          requestAlertPermission: false,
          requestSoundPermission: false,
          requestBadgePermission: false,
        ),
      ),
    );
    _hazir = true;
  }

  /// İzin ister (iOS ve Android 13+). Sonuç: izin verildi mi?
  Future<bool> izinIste() async {
    await baslat();
    final ios = _eklenti.resolvePlatformSpecificImplementation<
        IOSFlutterLocalNotificationsPlugin>();
    if (ios != null) {
      return await ios.requestPermissions(alert: true, sound: true, badge: true) ??
          false;
    }
    final android = _eklenti.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android != null) {
      return await android.requestNotificationsPermission() ?? false;
    }
    return false;
  }

  /// Arka planda düzenli yeni arz kontrolünü kaydeder.
  Future<void> arkaPlanKontroluKaydet() async {
    await Workmanager().initialize(arkaPlanGirisi);
    await Workmanager().registerPeriodicTask(
      arzKontrolGorevi,
      arzKontrolGorevi,
      frequency: const Duration(hours: 6),
      constraints: Constraints(networkType: NetworkType.connected),
      existingWorkPolicy: ExistingPeriodicWorkPolicy.keep,
    );
  }

  /// Yeni arzları bildirir ve tarih hatırlatmalarını yeniden planlar.
  /// Hem uygulama içinden hem arka plan görevinden çağrılır.
  Future<void> guncelle(List<dynamic> arzlar) async {
    await baslat();
    final ayar = await BildirimAyarlari.yukle();
    final p = await SharedPreferences.getInstance();

    // 1) Yeni arz tespiti. İlk çalıştırmada mevcut arzlar "yeni" sayılmaz.
    final bool ilkKez = !p.containsKey('bilinen_arzlar');
    final Set<String> bilinen =
        (p.getStringList('bilinen_arzlar') ?? []).toSet();
    final List<dynamic> yeniler = arzlar
        .where((a) => !bilinen.contains((a['sirket'] ?? '').toString()))
        .toList();
    if (!ilkKez && ayar.yeniArz) {
      for (final a in yeniler) {
        final String ad = (a['sirket'] ?? '').toString();
        await _eklenti.show(
          id: kararliKimlik('$ad|yeni'),
          title: 'Yeni halka arz: $ad',
          body: '${a['durum'] ?? ''} · Kalite puanı ${(a['skor'] ?? 0).round()}/100',
          notificationDetails: _detay,
        );
      }
    }
    await p.setStringList('bilinen_arzlar', <String>{
      ...bilinen,
      ...arzlar.map((a) => (a['sirket'] ?? '').toString()),
    }.toList());

    // 2) Tarih hatırlatmaları: hepsini silip güncel listeden yeniden kur.
    await _eklenti.cancelAllPendingNotifications();
    if (!ayar.hatirlatma) return;
    final Set<String>? filtre = ayar.sadeceFavoriler
        ? (p.getStringList('favoriler') ?? []).toSet()
        : null;
    final simdi = tz.TZDateTime.now(tz.local);
    final planlar = hatirlatmalariUret(
      arzlar,
      DateTime(simdi.year, simdi.month, simdi.day, simdi.hour, simdi.minute),
      sadeceBunlar: filtre,
    );
    for (final b in planlar) {
      await _eklenti.zonedSchedule(
        id: b.id,
        scheduledDate: tz.TZDateTime(tz.local, b.zaman.year, b.zaman.month,
            b.zaman.day, b.zaman.hour, b.zaman.minute),
        title: b.baslik,
        body: b.govde,
        notificationDetails: _detay,
        // Kesin alarm izni (SCHEDULE_EXACT_ALARM) istememek için esnek mod;
        // bildirim birkaç dakika gecikebilir.
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
      );
    }
  }
}

/// Arka plan görevinin giriş noktası (ayrı bir isolate'te çalışır).
@pragma('vm:entry-point')
void arkaPlanGirisi() {
  Workmanager().executeTask((gorev, girdi) async {
    WidgetsFlutterBinding.ensureInitialized();
    try {
      final String url = const String.fromEnvironment(
        'API_URL',
        defaultValue: 'https://halkaarzanaliziapi.onrender.com',
      );
      final r = await http
          .get(Uri.parse('$url/api/halkarzlar'))
          .timeout(const Duration(seconds: 60));
      if (r.statusCode != 200) return true;
      final List<dynamic> arzlar =
          json.decode(utf8.decode(r.bodyBytes))['halka_arzlar'] ?? [];
      await BildirimServisi.instance.guncelle(arzlar);
    } catch (_) {
      // Ağ hatası vb. — bir sonraki çalıştırmada tekrar denenir.
    }
    return true;
  });
}
