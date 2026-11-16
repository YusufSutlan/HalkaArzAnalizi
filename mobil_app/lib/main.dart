import 'dart:async';
// FontFeature (tabular rakamlar) finansal tabloda kolonların
// hizalı görünmesi için gerekli.
import 'dart:ui' show FontFeature;
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'bildirimler.dart';
import 'hakkinda.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const HalkaArzApp());
}

// ---------------------------------------------------------
// ⚙️ API AYARLARI
// ---------------------------------------------------------
class ApiConfig {
  ApiConfig._();
  // Geliştirmede yerel sunucuya bağlanmak için:
  //   flutter run --dart-define=API_URL=http://localhost:8000
  static const String baseUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: 'https://halkaarzanaliziapi.onrender.com',
  );
  // Render ücretsiz planda sunucu uykudan uyanırken ilk istek 30-60 sn
  // sürebiliyor. Deneme başına süre + otomatik tekrar sayısı.
  static const Duration timeout = Duration(seconds: 45);
  static const int otomatikTekrar = 2;
}

// ---------------------------------------------------------
// 🎨 TASARIM SİSTEMİ
// ---------------------------------------------------------
class AppColors {
  AppColors._();
  static const Color bg = Color(0xFF0A0D14);
  static const Color surface = Color(0xFF12161F);
  static const Color surfaceElevated = Color(0xFF1A2029);
  static const Color border = Color(0xFF262C38);
  static const Color accent = Color(0xFFC9A24B);
  static const Color accentSoft = Color(0xFFE4C77A);
  static const Color info = Color(0xFF6C8EEF);
  static const Color positive = Color(0xFF3FCE8E);
  static const Color warning = Color(0xFFEBA23B);
  static const Color danger = Color(0xFFE4584F);
  static const Color dangerDark = Color(0xFF8A2B26);
  static const Color neutral = Color(0xFF6B7385);
  static const Color textPrimary = Color(0xFFF3F5F8);
  static const Color textSecondary = Color(0xFF9AA2B4);
  static const Color textTertiary = Color(0xFF5D6577);
}

/// Büyük sayıyı binlik ayraçla yazar: 243243243 -> "243.243.243"
String _binlikAyrac(num? deger) {
  if (deger == null) return "-";
  final String tam = deger.toStringAsFixed(0);
  final bool negatif = tam.startsWith('-');
  final String rakamlar = negatif ? tam.substring(1) : tam;
  final StringBuffer sb = StringBuffer();
  for (int i = 0; i < rakamlar.length; i++) {
    if (i > 0 && (rakamlar.length - i) % 3 == 0) sb.write('.');
    sb.write(rakamlar[i]);
  }
  return (negatif ? '-' : '') + sb.toString();
}

/// Türkçe'ye güvenli küçük harf: Dart'ın toLowerCase() metodu "İ" harfini
/// "i̇" (i + birleşik nokta), "I" harfini "i" yapar; "ALTINDA" -> "altinda",
/// "ÜZERİNDE" -> "üzeri̇nde" olur ve "altında"/"üzerinde" ile eşleşmez.
String _trKucult(String s) =>
    s.replaceAll('İ', 'i').replaceAll('I', 'ı').toLowerCase();

/// Harf notunu (A+, B, C...) ayıklayıp sadece yıldızları döndürür.
String _sadeceYildiz(dynamic ham) {
  final String metin = (ham ?? "").toString();
  final yildizlar = RegExp(r'[★☆]+').firstMatch(metin);
  if (yildizlar != null) return yildizlar.group(0)!;
  return metin.isEmpty ? "☆☆☆☆☆" : metin;
}

/// Backend metinleri baştan 🚨/⚠️/ℹ️ emojisiyle geliyor. Bu emojiler zaten
/// panelin kendi ikonuyla (renkli başlık ikonu) veriliyor; metinde de
/// tekrarlanması "ikon + emoji + madde imi" kalabalığı yaratıp amatörce
/// görünmesine yol açıyordu. Görüntülemeden önce temizleniyor.
String _onEkTemizle(String s) {
  return s
      .replaceFirst(RegExp(r'^[\u{1F6A8}⚠ℹ]️?\s*', unicode: true), '')
      .trim();
}

/// İlk gün satış baskısı metinleri "İLK GÜN SATIŞ BASKISI ÇOK YÜKSEK:" gibi
/// panelin kendi başlığını ve rozetini tekrarlayan bir önek taşıyor. Bu önek
/// (iki noktaya kadar tamamı büyük harf ise) atılıp yalnızca asıl açıklama
/// bırakılıyor.
String _baskiMetniSadelestir(String s) {
  final String t = _onEkTemizle(s);
  final int ikiNokta = t.indexOf(':');
  if (ikiNokta > 0 &&
      ikiNokta < 50 &&
      t.substring(0, ikiNokta) == t.substring(0, ikiNokta).toUpperCase()) {
    return t.substring(ikiNokta + 1).trim();
  }
  return t;
}

class HalkaArzApp extends StatelessWidget {
  const HalkaArzApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Halka Arz Analiz',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: AppColors.bg,
        colorScheme: const ColorScheme.dark(
          primary: AppColors.accent,
          secondary: AppColors.info,
          surface: AppColors.surface,
          error: AppColors.danger,
        ),
        appBarTheme: const AppBarTheme(
          backgroundColor: AppColors.bg,
          elevation: 0,
          centerTitle: true,
          surfaceTintColor: Colors.transparent,
          titleTextStyle: TextStyle(
            color: AppColors.textPrimary,
            fontSize: 18,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
      home: const BaslangicKontrol(),
    );
  }
}

class BaslangicKontrol extends StatefulWidget {
  const BaslangicKontrol({super.key});
  @override
  State<BaslangicKontrol> createState() => _BaslangicKontrolState();
}

class _BaslangicKontrolState extends State<BaslangicKontrol> {
  @override
  void initState() {
    super.initState();
    _sozlesmeKontrolEt();
  }

  Future<void> _sozlesmeKontrolEt() async {
    final prefs = await SharedPreferences.getInstance();
    final onaylandi = prefs.getBool('sozlesme_onay') ?? false;
    // DÜZELTME: await sonrası widget ağaçtan kaldırılmış olabilir,
    // context/Navigator kullanmadan önce mounted kontrolü şart.
    if (!mounted) return;
    if (onaylandi) {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (context) => const AnaEkran()),
      );
    } else {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (context) => const SozlesmeEkrani()),
      );
    }
  }

  @override
  Widget build(BuildContext context) => const Scaffold(
    body: Center(child: CircularProgressIndicator(color: AppColors.accent)),
  );
}

class SozlesmeEkrani extends StatelessWidget {
  const SozlesmeEkrani({super.key});
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(
                Icons.gavel_rounded,
                color: AppColors.warning,
                size: 40,
              ),
              const SizedBox(height: 20),
              const Text(
                "Başlamadan önce",
                style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 20),
              const Expanded(
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        "Yatırım tavsiyesi değildir",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w800,
                          color: AppColors.warning,
                        ),
                      ),
                      SizedBox(height: 8),
                      Text(
                        yatirimUyarisiMetni,
                        style: TextStyle(
                          fontSize: 14.5,
                          height: 1.5,
                          color: AppColors.textSecondary,
                        ),
                      ),
                      SizedBox(height: 20),
                      Text(
                        "Veri kaynakları",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w800,
                          color: AppColors.textPrimary,
                        ),
                      ),
                      SizedBox(height: 8),
                      Text(
                        veriKaynaklariMetni,
                        style: TextStyle(
                          fontSize: 14.5,
                          height: 1.5,
                          color: AppColors.textSecondary,
                        ),
                      ),
                      SizedBox(height: 16),
                      Text(
                        "Bu metne istediğiniz zaman sağ üstteki ⓘ (Hakkında) "
                        "simgesinden ulaşabilirsiniz.",
                        style: TextStyle(
                          fontSize: 12.5,
                          height: 1.4,
                          color: AppColors.textTertiary,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                height: 56,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.accent,
                    foregroundColor: AppColors.bg,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(16),
                    ),
                  ),
                  onPressed: () async {
                    final prefs = await SharedPreferences.getInstance();
                    await prefs.setBool('sozlesme_onay', true);
                    if (context.mounted) {
                      Navigator.pushReplacement(
                        context,
                        MaterialPageRoute(
                          builder: (context) => const AnaEkran(),
                        ),
                      );
                    }
                  },
                  child: const Text(
                    "Okudum, Anladım",
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class AnaEkran extends StatefulWidget {
  const AnaEkran({super.key});
  @override
  State<AnaEkran> createState() => _AnaEkranState();
}

class _AnaEkranState extends State<AnaEkran> {
  List<dynamic> halkaArzlar = [];
  bool yukleniyor = true;
  String hataMesaji = "";
  // Son başarılı yanıtın zamanı (önbellekten gösterilirken kullanıcıya
  // belirtmek için). Önbellek: sunucu uyurken bile liste anında görünsün.
  DateTime? sonGuncelleme;
  bool onbellektenGosteriliyor = false;
  // Aynı anda iki istek (ör. yenile + otomatik) çakışmasın.
  bool _istekSuruyor = false;
  // Kullanıcının yıldızladığı arzlar (şirket adıyla). Listede en üstte
  // gösterilir; cihazda saklanır.
  Set<String> favoriler = {};

  @override
  void initState() {
    super.initState();
    _favorileriYukle();
    _onbellegiYukleVeGetir();
    _bildirimleriHazirla();
  }

  /// İlk açılışta bir kez bildirim izni ister ve arka plan kontrolünü kurar.
  Future<void> _bildirimleriHazirla() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!(prefs.getBool('bildirim_izin_istendi') ?? false)) {
        await BildirimServisi.instance.izinIste();
        await prefs.setBool('bildirim_izin_istendi', true);
      }
      await BildirimServisi.instance.arkaPlanKontroluKaydet();
    } catch (e) {
      debugPrint('Bildirim kurulumu başarısız: $e');
    }
  }

  Future<void> _bildirimleriGuncelle() async {
    try {
      await BildirimServisi.instance.guncelle(halkaArzlar);
    } catch (e) {
      debugPrint('Bildirimler güncellenemedi: $e');
    }
  }

  Future<void> _bildirimAyarlariniAc() async {
    BildirimAyarlari ayar = await BildirimAyarlari.yukle();
    if (!mounted) return;
    await showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) {
          Future<void> degistir(BildirimAyarlari yeni) async {
            setSheet(() => ayar = yeni);
            await yeni.kaydet();
            await _bildirimleriGuncelle();
          }

          Widget anahtar(
            String baslik,
            String alt,
            bool deger,
            ValueChanged<bool>? onChanged,
          ) {
            return SwitchListTile.adaptive(
              value: deger,
              onChanged: onChanged,
              activeTrackColor: AppColors.accent,
              title: Text(
                baslik,
                style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.w600,
                ),
              ),
              subtitle: Text(
                alt,
                style: const TextStyle(
                  color: AppColors.textSecondary,
                  fontSize: 12.5,
                ),
              ),
            );
          }

          return SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Padding(
                    padding: EdgeInsets.fromLTRB(20, 4, 20, 8),
                    child: Text(
                      "Bildirimler",
                      style: TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w800,
                        color: AppColors.textPrimary,
                      ),
                    ),
                  ),
                  anahtar(
                    "Yeni halka arzlar",
                    "Listeye yeni bir arz eklendiğinde haber ver",
                    ayar.yeniArz,
                    (v) => degistir(
                      BildirimAyarlari(
                        yeniArz: v,
                        hatirlatma: ayar.hatirlatma,
                        sadeceFavoriler: ayar.sadeceFavoriler,
                      ),
                    ),
                  ),
                  anahtar(
                    "Tarih hatırlatmaları",
                    "Talep toplama başlangıcı, son gün ve işlem başlangıcı",
                    ayar.hatirlatma,
                    (v) => degistir(
                      BildirimAyarlari(
                        yeniArz: ayar.yeniArz,
                        hatirlatma: v,
                        sadeceFavoriler: ayar.sadeceFavoriler,
                      ),
                    ),
                  ),
                  anahtar(
                    "Sadece favorilerim",
                    "Hatırlatmaları yalnızca işaretlediğin arzlar için gönder",
                    ayar.sadeceFavoriler,
                    ayar.hatirlatma
                        ? (v) => degistir(
                            BildirimAyarlari(
                              yeniArz: ayar.yeniArz,
                              hatirlatma: ayar.hatirlatma,
                              sadeceFavoriler: v,
                            ),
                          )
                        : null,
                  ),
                  const Padding(
                    padding: EdgeInsets.fromLTRB(20, 8, 20, 4),
                    child: Text(
                      "Hatırlatmalar telefonunda planlanır, uygulama kapalıyken "
                      "de gelir. Yeni arz kontrolü arka planda birkaç saatte bir "
                      "yapılır; iOS bu zamanlamayı kendisi belirler, gecikebilir.",
                      style: TextStyle(
                        fontSize: 11.5,
                        height: 1.4,
                        color: AppColors.textTertiary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Future<void> _favorileriYukle() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(
      () => favoriler = (prefs.getStringList('favoriler') ?? []).toSet(),
    );
  }

  Future<void> _favoriDegistir(String sirket) async {
    setState(() {
      if (!favoriler.remove(sirket)) favoriler.add(sirket);
    });
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList('favoriler', favoriler.toList());
  }

  /// Favoriler önde, kalan sıra sunucunun sırası (duruma göre) korunur.
  List<dynamic> get _siraliArzlar {
    final fav = halkaArzlar.where((a) => favoriler.contains(a['sirket']));
    final diger = halkaArzlar.where((a) => !favoriler.contains(a['sirket']));
    return [...fav, ...diger];
  }

  static const String _onbellekAnahtari = 'arz_onbellek_v1';
  static const String _onbellekZamanAnahtari = 'arz_onbellek_zaman_v1';

  Future<void> _onbellegiYukleVeGetir() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final String? ham = prefs.getString(_onbellekAnahtari);
      final int? zaman = prefs.getInt(_onbellekZamanAnahtari);
      if (ham != null && mounted) {
        final decoded = json.decode(ham);
        setState(() {
          halkaArzlar = decoded['halka_arzlar'] ?? [];
          sonGuncelleme = zaman != null
              ? DateTime.fromMillisecondsSinceEpoch(zaman)
              : null;
          onbellektenGosteriliyor = true;
          yukleniyor = false;
        });
      }
    } catch (_) {
      // Bozuk önbellek: yok say, sunucudan çekilecek.
    }
    await verileriGetir();
  }

  String _hataMetni(Object e, [int? kod]) {
    if (e is TimeoutException) {
      return "Sunucudan zamanında yanıt alınamadı.\nSunucu uyanıyor olabilir; "
          "birkaç saniye sonra tekrar deneyin.";
    }
    if (e is FormatException) return "Sunucudan beklenmeyen bir yanıt alındı.";
    if (kod != null) {
      return (kod == 502 || kod == 503 || kod == 504)
          ? "Sunucu şu anda hazırlanıyor veya veri kaynağına ulaşamıyor.\n"
                "Lütfen biraz sonra tekrar deneyin."
          : "Sunucu hatası ($kod). Lütfen daha sonra tekrar deneyin.";
    }
    return "Bağlantı kurulamadı.\nLütfen internet bağlantınızı kontrol edin.";
  }

  /// Sunucudan güncel listeyi çeker. Ücretsiz sunucunun soğuk başlangıcı için
  /// zaman aşımı ve geçici 5xx hatalarında otomatik olarak tekrar dener.
  /// Elde gösterilecek veri varsa hata tam ekran yerine alt bildirimle verilir.
  Future<void> verileriGetir() async {
    if (_istekSuruyor) return;
    _istekSuruyor = true;
    Object? sonHata;
    int? sonKod;
    try {
      for (int deneme = 0; deneme <= ApiConfig.otomatikTekrar; deneme++) {
        if (!mounted) return;
        try {
          final response = await http
              .get(Uri.parse('${ApiConfig.baseUrl}/api/halkarzlar'))
              .timeout(ApiConfig.timeout);
          if (!mounted) return;
          if (response.statusCode == 200) {
            final String govde = utf8.decode(response.bodyBytes);
            final decoded = json.decode(govde);
            setState(() {
              halkaArzlar = decoded['halka_arzlar'] ?? [];
              yukleniyor = false;
              hataMesaji = "";
              sonGuncelleme = DateTime.now();
              onbellektenGosteriliyor = false;
            });
            _bildirimleriGuncelle();
            final prefs = await SharedPreferences.getInstance();
            await prefs.setString(_onbellekAnahtari, govde);
            await prefs.setInt(
              _onbellekZamanAnahtari,
              DateTime.now().millisecondsSinceEpoch,
            );
            return;
          }
          sonKod = response.statusCode;
          sonHata = Exception('HTTP $sonKod');
          // Kalıcı hata (4xx vb.) için tekrar denemenin anlamı yok.
          if (sonKod < 500) break;
        } on TimeoutException catch (e) {
          sonHata = e;
          sonKod = null;
        } on FormatException catch (e) {
          sonHata = e;
          sonKod = null;
          break;
        } catch (e) {
          sonHata = e;
          sonKod = null;
        }
        if (deneme < ApiConfig.otomatikTekrar) {
          await Future.delayed(const Duration(seconds: 3));
        }
      }

      if (!mounted) return;
      final String mesaj = _hataMetni(sonHata ?? Exception(), sonKod);
      if (halkaArzlar.isNotEmpty) {
        // Önbellekteki liste ekranda kalsın; kullanıcı yine de kullanabilsin.
        setState(() => yukleniyor = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              "Güncel veri alınamadı, son kayıtlı liste gösteriliyor.\n"
              "${mesaj.split('\n').first}",
            ),
            duration: const Duration(seconds: 6),
            action: SnackBarAction(
              label: "Tekrar dene",
              onPressed: verileriGetir,
            ),
          ),
        );
      } else {
        setState(() {
          hataMesaji = mesaj;
          yukleniyor = false;
        });
      }
    } finally {
      _istekSuruyor = false;
    }
  }

  void _yenidenDene() {
    setState(() {
      yukleniyor = halkaArzlar.isEmpty;
      hataMesaji = "";
    });
    verileriGetir();
  }

  String _sonGuncellemeMetni() {
    final z = sonGuncelleme;
    if (z == null) return "";
    String iki(int n) => n.toString().padLeft(2, '0');
    final simdi = DateTime.now();
    final bugun =
        z.year == simdi.year && z.month == simdi.month && z.day == simdi.day;
    final saat = "${iki(z.hour)}:${iki(z.minute)}";
    return bugun
        ? "bugün $saat"
        : "${iki(z.day)}.${iki(z.month)}.${z.year} $saat";
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Halka Arz Analiz'),
        actions: [
          IconButton(
            tooltip: "Hakkında",
            icon: const Icon(Icons.info_outline_rounded),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const HakkindaEkrani()),
            ),
          ),
          IconButton(
            tooltip: "Bildirim ayarları",
            icon: const Icon(Icons.notifications_none_rounded),
            onPressed: _bildirimAyarlariniAc,
          ),
          IconButton(
            tooltip: "Yenile",
            icon: const Icon(Icons.refresh_rounded),
            onPressed: _yenidenDene,
          ),
        ],
      ),
      body: yukleniyor
          ? const _YukleniyorGorunumu()
          : hataMesaji.isNotEmpty
          ? Center(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(
                      Icons.cloud_off_rounded,
                      color: AppColors.textTertiary,
                      size: 40,
                    ),
                    const SizedBox(height: 12),
                    Text(
                      hataMesaji,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: AppColors.textSecondary),
                    ),
                    const SizedBox(height: 16),
                    ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.accent,
                        foregroundColor: AppColors.bg,
                      ),
                      onPressed: _yenidenDene,
                      icon: const Icon(Icons.refresh_rounded),
                      label: const Text("Tekrar Dene"),
                    ),
                  ],
                ),
              ),
            )
          : halkaArzlar.isEmpty
          // DÜZELTME: Liste boş geldiğinde (örn. site şu an hiçbir halka
          // arz listelemiyorsa) eskiden boş bir ekran gösteriliyordu;
          // artık kullanıcıya bilgi veriliyor.
          // Aktif arz olmadığı dönemler çok sık; tek satırlık bir metin
          // uygulamanın bozuk olduğu izlenimini veriyordu. Artık durum
          // açıklanıyor ve aşağı çekerek yenileme de çalışıyor.
          ? RefreshIndicator(
              color: AppColors.accent,
              backgroundColor: AppColors.surface,
              onRefresh: verileriGetir,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.symmetric(horizontal: 32),
                children: [
                  SizedBox(height: MediaQuery.of(context).size.height * 0.25),
                  const Icon(
                    Icons.event_available_rounded,
                    size: 44,
                    color: AppColors.textTertiary,
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    "Şu an aktif halka arz yok",
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                      color: AppColors.textPrimary,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    "Talep toplama tarihi açıklanan veya talep toplayan bir arz "
                    "olduğunda burada analiziyle birlikte görünecek. Yenilemek "
                    "için aşağı çekin.",
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 13.5,
                      height: 1.5,
                      color: AppColors.textSecondary,
                    ),
                  ),
                  const SizedBox(height: 32),
                  const YatirimUyarisiNotu(),
                ],
              ),
            )
          : RefreshIndicator(
              color: AppColors.accent,
              backgroundColor: AppColors.surface,
              onRefresh: verileriGetir,
              child: ListView.builder(
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 10,
                ),
                // +2: baştaki "X aktif halka arz" özet satırı ve sondaki
                // yatırım tavsiyesi uyarısı.
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: halkaArzlar.length + 2,
                itemBuilder: (context, index) {
                  if (index == halkaArzlar.length + 1) {
                    return const YatirimUyarisiNotu();
                  }
                  if (index == 0) {
                    final String guncelleme = _sonGuncellemeMetni();
                    return Padding(
                      padding: const EdgeInsets.fromLTRB(2, 0, 2, 14),
                      child: Text(
                        "${halkaArzlar.length} aktif halka arz izleniyor"
                        "${guncelleme.isEmpty ? "" : " · ${onbellektenGosteriliyor ? "son kayıt" : "güncellendi"} $guncelleme"}",
                        style: const TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                          color: AppColors.textTertiary,
                          letterSpacing: 0.2,
                        ),
                      ),
                    );
                  }
                  final arz = _siraliArzlar[index - 1];
                  final double skor = (arz['skor'] ?? 0).toDouble();
                  final String durum = arz['durum'] ?? "Bilinmiyor";
                  final String sirketAdi =
                      (arz['sirket'] ?? "Bilinmeyen Şirket").toString();
                  // YENİ: Listede de baskı uyarısını göster, kullanıcı
                  // detaya girmeden riski görsün.
                  final double listeBaski = (arz['ilk_gun_satis_baskisi'] ?? 0)
                      .toDouble();

                  // Renk eşikleri sunucunun yıldız eşikleriyle aynı:
                  // 4-5 yıldız yeşil, 3 mavi, 2 turuncu, 0-1 kırmızı.
                  Color renk = skor >= 70
                      ? AppColors.positive
                      : skor >= 55
                      ? AppColors.info
                      : skor >= 40
                      ? AppColors.warning
                      : AppColors.danger;
                  if (skor == 0) renk = AppColors.neutral;
                  // DÜZELTME: Eskiden "Borsada İşlem Görüyor" durumu puan
                  // rengini de yeşile çeviriyordu; 53 puanlık bir arz 75
                  // puanlık gibi "güçlü" görünüyordu. Artık yalnızca durum
                  // rozeti bu renge boyanıyor, puan rengi puana ait.
                  final Color durumRenk = durum == "Borsada İşlem Görüyor"
                      ? AppColors.positive
                      : renk;

                  return InkWell(
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (context) =>
                            HalkaArzDetaySayfasi(arz: arz, renk: renk),
                      ),
                    ),
                    child: Container(
                      margin: const EdgeInsets.only(bottom: 14),
                      padding: const EdgeInsets.all(18),
                      decoration: BoxDecoration(
                        color: AppColors.surface,
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: AppColors.border),
                      ),
                      child: Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    Expanded(
                                      child: Text(
                                        sirketAdi,
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: const TextStyle(
                                          fontSize: 16.5,
                                          fontWeight: FontWeight.w700,
                                          color: AppColors.textPrimary,
                                        ),
                                      ),
                                    ),
                                    GestureDetector(
                                      behavior: HitTestBehavior.opaque,
                                      onTap: () => _favoriDegistir(sirketAdi),
                                      child: Padding(
                                        padding: const EdgeInsets.only(left: 8),
                                        child: Icon(
                                          favoriler.contains(sirketAdi)
                                              ? Icons.bookmark_rounded
                                              : Icons.bookmark_border_rounded,
                                          size: 21,
                                          color: favoriler.contains(sirketAdi)
                                              ? AppColors.accent
                                              : AppColors.textTertiary,
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                Text(
                                  // Sunucu artık sadece yıldız gönderiyor.
                                  // Eski/cache'li yanıt gelirse "A+ (★★★★★)"
                                  // gibi harf notunu burada da temizliyoruz.
                                  _sadeceYildiz(arz['yildiz']),
                                  style: TextStyle(
                                    fontSize: 13,
                                    color: renk,
                                    letterSpacing: 2,
                                  ),
                                ),
                                const SizedBox(height: 10),
                                Wrap(
                                  spacing: 6,
                                  runSpacing: 6,
                                  children: [
                                    Container(
                                      padding: const EdgeInsets.symmetric(
                                        horizontal: 10,
                                        vertical: 5,
                                      ),
                                      decoration: BoxDecoration(
                                        color: durumRenk.withValues(
                                          alpha: 0.12,
                                        ),
                                        borderRadius: BorderRadius.circular(20),
                                      ),
                                      child: Text(
                                        durum,
                                        style: TextStyle(
                                          fontSize: 11.5,
                                          color: durumRenk,
                                          fontWeight: FontWeight.w700,
                                        ),
                                      ),
                                    ),
                                    if (listeBaski >= 65)
                                      Container(
                                        padding: const EdgeInsets.symmetric(
                                          horizontal: 10,
                                          vertical: 5,
                                        ),
                                        decoration: BoxDecoration(
                                          color:
                                              (listeBaski >= 85
                                                      ? AppColors.danger
                                                      : AppColors.warning)
                                                  .withValues(alpha: 0.15),
                                          borderRadius: BorderRadius.circular(
                                            20,
                                          ),
                                        ),
                                        child: Row(
                                          mainAxisSize: MainAxisSize.min,
                                          children: [
                                            Icon(
                                              Icons.waves_rounded,
                                              size: 12,
                                              color: listeBaski >= 85
                                                  ? AppColors.danger
                                                  : AppColors.warning,
                                            ),
                                            const SizedBox(width: 4),
                                            Text(
                                              "İlk gün satış baskısı",
                                              style: TextStyle(
                                                fontSize: 11,
                                                color: listeBaski >= 85
                                                    ? AppColors.danger
                                                    : AppColors.warning,
                                                fontWeight: FontWeight.w700,
                                              ),
                                            ),
                                          ],
                                        ),
                                      ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 12),
                          // Renkli "Kalite" gösterimi korunuyor.
                          // Veri kapsamı düşükse etiket değiştirmek
                          // yerine yanına küçük bir yüzde ekleniyor —
                          // uyarı kaybolmuyor ama görsel bozulmuyor.
                          _SkorGostergesi(
                            skor: skor,
                            renk: renk,
                            etiket: "Kalite",
                            altNot: (arz['skor_guvenilir'] ?? false)
                                ? null
                                : "veri %${arz['veri_guvenilirligi'] ?? 0}",
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
    );
  }
}

class HalkaArzDetaySayfasi extends StatelessWidget {
  final Map<String, dynamic> arz;
  final Color renk;

  const HalkaArzDetaySayfasi({
    super.key,
    required this.arz,
    required this.renk,
  });

  @override
  Widget build(BuildContext context) {
    List<dynamic> gucluYanlar = arz['guclu_yanlar'] ?? [];
    List<dynamic> riskler = arz['riskler'] ?? [];
    List<dynamic> kirmiziBayraklar = arz['kirmizi_bayraklar'] ?? [];

    String sirketAdi = (arz['sirket'] ?? "Bilinmeyen Şirket").toString();
    String bistKodu = arz['bist_kodu'] ?? "Belli Değil";
    String durum = arz['durum'] ?? "Bilinmiyor";
    bool katilimUygun = arz['katilim_endeksine_uygun'] ?? false;
    bool t1Kullanilabilir = arz['t1_t2_kullanilabilir'] ?? false;
    String islemMenusu = arz['islem_menusu'] ?? "Halka Arz Menüsü";

    double temelKalite = (arz['temel_kalite_skoru'] ?? arz['skor'] ?? 0)
        .toDouble();
    // Kaynakta finansal tablo yoksa skor "finansal kalite" değil,
    // yalnızca "arz yapısı" skorudur; etiketi buna göre değiştiriyoruz.
    final bool finansalVeriVar = arz['finansal_veri_var'] ?? false;
    final String kaliteEtiketi = finansalVeriVar
        ? "Temel Kalite"
        : "Arz Yapısı";
    double tavanPotansiyeli = (arz['tavan_potansiyeli_skoru'] ?? 0).toDouble();
    double riskSkoru = (arz['risk_skoru'] ?? 0).toDouble();

    int veriGuvenilirligi = arz['veri_guvenilirligi'] ?? 0;
    String volatilite = arz['tahmini_volatilite'] ?? "Belirsiz";

    // YENİ: İlk gün satış baskısı bilgileri
    double ilkGunBaski = (arz['ilk_gun_satis_baskisi'] ?? 0).toDouble();
    String baskiSeviyesi = arz['ilk_gun_baski_seviyesi'] ?? "Bilinmiyor";
    String baskiUyarisi = arz['ilk_gun_baski_uyarisi'] ?? "";
    List<dynamic> baskiGerekceleri = arz['ilk_gun_baski_gerekceleri'] ?? [];
    List<dynamic> uyarilar = arz['uyarilar'] ?? [];
    int ayniHaftaArz = arz['ayni_hafta_arz_sayisi'] ?? 1;
    List<dynamic> rakipArzlar = arz['rakip_arzlar'] ?? [];
    final num? kisiBasiTutar = arz['kisi_basi_tahmini_tutar'];
    final num? kisiBasiLot = arz['kisi_basi_tahmini_lot'];
    String sektor = arz['sektor'] ?? "";
    // YENİ: F/K, PD/DD, FD/FAVÖK oranları ve "pahalı mı ucuz mu" yorumu
    final Map<String, dynamic> degerleme =
        (arz['degerleme'] as Map?)?.cast<String, dynamic>() ?? {};

    String gosterilecekTarihBaslik = "Talep Toplama";
    String gosterilecekTarihDeger = arz['tarih']?.toString() ?? "Açıklanmadı";
    String islemTarihi = arz['islem_tarihi']?.toString() ?? "Açıklanmadı";

    if ((durum == "İşleme Girmesi Bekleniyor" ||
            durum == "Dağıtım Bekleniyor" ||
            durum == "Borsada İşlem Görüyor") &&
        islemTarihi != "Açıklanmadı" &&
        islemTarihi != "-") {
      gosterilecekTarihBaslik = "İşlem Tarihi";
      gosterilecekTarihDeger = islemTarihi;
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(
          bistKodu != "Belli Değil"
              ? bistKodu
              // DÜZELTME: sirketAdi boş string olabilir; bu durumda
              // split(" ")[0] boş string döner ve başlık boş kalırdı.
              : (sirketAdi.isNotEmpty ? sirketAdi.split(" ")[0] : "Detay"),
        ),
      ),
      body: SingleChildScrollView(
        physics: const BouncingScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 10, 20, 40),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              sirketAdi,
              style: const TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.w800,
                color: AppColors.textPrimary,
              ),
            ),
            const SizedBox(height: 12),

            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                _Rozet(
                  katilimUygun ? "Katılıma Uygun" : "Uygun Değil",
                  katilimUygun ? AppColors.positive : AppColors.danger,
                  Icons.mosque,
                ),
                _Rozet(
                  t1Kullanilabilir ? "T1-T2 Var" : "T1-T2 Yok",
                  t1Kullanilabilir ? AppColors.positive : AppColors.warning,
                  Icons.account_balance_wallet,
                ),
                _Rozet(islemMenusu, AppColors.info, Icons.touch_app),
                if (sektor.isNotEmpty)
                  _Rozet(sektor, AppColors.neutral, Icons.category_outlined),
                if (ilkGunBaski >= 65)
                  _Rozet(
                    "Satış Baskısı: $baskiSeviyesi",
                    ilkGunBaski >= 85 ? AppColors.danger : AppColors.warning,
                    Icons.waves_rounded,
                  ),
              ],
            ),
            const SizedBox(height: 20),

            if (temelKalite > 0) ...[
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    Expanded(
                      child: FittedBox(
                        fit: BoxFit.scaleDown,
                        child: _SkorGostergesi(
                          skor: temelKalite,
                          renk: renk,
                          etiket: kaliteEtiketi,
                        ),
                      ),
                    ),
                    Container(width: 1, height: 40, color: AppColors.border),
                    Expanded(
                      child: FittedBox(
                        fit: BoxFit.scaleDown,
                        child: _SkorGostergesi(
                          skor: tavanPotansiyeli,
                          renk: AppColors.info,
                          // "Tavan Şansı" bir vaat gibi okunuyordu; nötr etiket.
                          etiket: "Kısa Vade",
                        ),
                      ),
                    ),
                    Container(width: 1, height: 40, color: AppColors.border),
                    Expanded(
                      child: FittedBox(
                        fit: BoxFit.scaleDown,
                        child: _SkorGostergesi(
                          skor: riskSkoru,
                          renk: riskSkoru > 60
                              ? AppColors.danger
                              : AppColors.warning,
                          etiket: "Risk Skoru",
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
            ],

            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: renk.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: renk.withValues(alpha: 0.3)),
              ),
              child: Text(
                arz['genel_degerlendirme'] ?? "",
                style: const TextStyle(
                  fontSize: 14.5,
                  height: 1.5,
                  color: AppColors.textPrimary,
                ),
              ),
            ),
            const SizedBox(height: 20),

            if ((arz['kisa_vade_olculebilir'] ?? false) &&
                ((arz['kisa_vade_etkenleri'] as List?)?.isNotEmpty ??
                    false)) ...[
              _KisaVadePaneli(
                skor: tavanPotansiyeli,
                etkenler: arz['kisa_vade_etkenleri'] as List,
                benzer: (arz['benzer_arzlar'] as Map?)?.cast<String, dynamic>(),
              ),
              const SizedBox(height: 12),
            ],

            // ─────────────────────────────────────────────────────────
            // YENİ: İLK GÜN SATIŞ BASKISI PANELİ
            // Yüksek kalite skorunun tek başına "tavan yapar" gibi
            // okunmasını engellemek için, kısa vadeli satış baskısı
            // ayrı ve görünür bir blok olarak sunuluyor.
            // ─────────────────────────────────────────────────────────
            if (baskiUyarisi.isNotEmpty) ...[
              _BaskiPaneli(
                skor: ilkGunBaski,
                seviye: baskiSeviyesi,
                uyari: baskiUyarisi,
                gerekceler: baskiGerekceleri,
                ayniHaftaArz: ayniHaftaArz,
                rakipArzlar: rakipArzlar,
                kisiBasiTutar: kisiBasiTutar,
                kisiBasiLot: kisiBasiLot,
              ),
              const SizedBox(height: 12),
            ],

            if (!(arz['skor_guvenilir'] ?? false)) ...[
              Container(
                width: double.infinity,
                margin: const EdgeInsets.only(bottom: 12),
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: AppColors.neutral.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(
                    color: AppColors.neutral.withValues(alpha: 0.4),
                  ),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(
                      Icons.help_outline_rounded,
                      size: 18,
                      color: AppColors.textTertiary,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        "Bu skor eksik veriyle hesaplandı (%$veriGuvenilirligi "
                        "kapsam). Düşük puan şirketin kötü olduğu anlamına "
                        "GELMEZ; izahname finansalları henüz işlenmemiş "
                        "olabilir. Diğer şirketlerle karşılaştırmayın.",
                        style: const TextStyle(
                          fontSize: 12.5,
                          height: 1.45,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],

            if (uyarilar.isNotEmpty) ...[
              ...uyarilar
                  .where((u) => u.toString() != baskiUyarisi)
                  .map(
                    (u) => Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(14),
                        decoration: BoxDecoration(
                          color: AppColors.warning.withValues(alpha: 0.08),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(
                            color: AppColors.warning.withValues(alpha: 0.3),
                          ),
                        ),
                        child: Text(
                          _onEkTemizle(u.toString()),
                          style: const TextStyle(
                            fontSize: 13,
                            height: 1.5,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ),
                    ),
                  ),
            ],

            if (kirmiziBayraklar.isNotEmpty) ...[
              _NitelikPaneli(
                baslik: "KIRMIZI BAYRAKLAR (Kritik Riskler)",
                ikon: Icons.flag_rounded,
                renk: AppColors.danger,
                arkaplan: AppColors.dangerDark,
                maddeler: kirmiziBayraklar,
              ),
              const SizedBox(height: 12),
            ],

            if (temelKalite > 0) ...[
              if (gucluYanlar.isNotEmpty)
                _NitelikPaneli(
                  baslik: "Avantajlar",
                  ikon: Icons.trending_up,
                  renk: AppColors.positive,
                  maddeler: gucluYanlar,
                ),
              if (gucluYanlar.isNotEmpty) const SizedBox(height: 12),
              if (riskler.isNotEmpty)
                _NitelikPaneli(
                  baslik: "Zayıf Yönler",
                  ikon: Icons.warning_amber,
                  renk: AppColors.warning,
                  maddeler: riskler,
                ),
              const SizedBox(height: 24),
            ],

            _KutuBaslik(
              "Risk ve Veri Güvenilirliği",
              Icons.health_and_safety_outlined,
            ),
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: AppColors.surface,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: AppColors.border),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            "Veri Güvenilirliği",
                            style: TextStyle(
                              fontSize: 11.5,
                              color: AppColors.textSecondary,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            "%$veriGuvenilirligi",
                            style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w800,
                              color: veriGuvenilirligi >= 80
                                  ? AppColors.positive
                                  : AppColors.warning,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: AppColors.surface,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: AppColors.border),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            "Tahmini Volatilite",
                            style: TextStyle(
                              fontSize: 11.5,
                              color: AppColors.textSecondary,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            volatilite.split('(')[0].trim(),
                            style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w800,
                              color:
                                  (volatilite.contains("satış baskısı") ||
                                      volatilite.contains("finansal"))
                                  ? AppColors.danger
                                  : AppColors.info,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _BilgiKarti("Hisse Fiyatı", arz['fiyat'], Icons.payments),
                  const SizedBox(width: 12),
                  _BilgiKarti(
                    gosterilecekTarihBaslik,
                    gosterilecekTarihDeger,
                    Icons.calendar_month,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _BilgiKarti(
                    "Arz Büyüklüğü",
                    arz['buyukluk'],
                    Icons.donut_large,
                  ),
                  const SizedBox(width: 12),
                  _BilgiKarti(
                    "Dağıtım Yöntemi",
                    arz['dagitim_yontemi'],
                    Icons.pie_chart,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _BilgiKarti("Pazar", arz['pazar'], Icons.store),
                  const SizedBox(width: 12),
                  _BilgiKarti(
                    "Aracı Kurum",
                    arz['araci_kurum'],
                    Icons.business,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            if ((degerleme['carpanlar'] as List?)?.isNotEmpty ?? false) ...[
              _KutuBaslik("Fiyat Değerlendirmesi", Icons.sell_outlined),
              _DegerlemePaneli(degerleme: degerleme),
              const SizedBox(height: 26),
            ],

            _KutuBaslik("Finansal Detaylar", Icons.query_stats_rounded),
            // Sabit en-boy oranlı GridView, uzun metinde veya büyük yazı
            // tipinde hücreyi taşırıyordu; satırlar artık içeriğe göre uzar.
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: _DetayHucresiYorumlu(
                      baslik: "Halka Açıklık",
                      deger: arz['aciklik']?.toString() ?? "-",
                      yorum: "Halka açılma oranı.",
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _DetayHucresiYorumlu(
                      baslik: "İskonto Oranı",
                      deger: arz['iskonto']?.toString() ?? "-",
                      yorum:
                          "Şirketin beyanıdır; geçmiş arzlarda getiriyle "
                          "ilişkisi görülmedi.",
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: _DetayHucresiYorumlu(
                      baslik: "Fiyat İstikrarı",
                      deger: arz['fiyat_istikrari']?.toString() ?? "-",
                      yorum: "Düşüş riskine karşı alınan önlemdir.",
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _DetayHucresiYorumlu(
                      baslik: "Toplam Pay Miktarı",
                      deger: arz['pay_sayisi_sayi'] != null
                          ? "${_binlikAyrac(arz['pay_sayisi_sayi'])} lot"
                          : (arz['pay_miktari']?.toString() ?? "Açıklanmadı"),
                      yorum:
                          (arz['pay_sayisi_kaynagi']?.toString() ?? "")
                              .startsWith("hesaplandı")
                          ? "Arz büyüklüğünden hesaplandı."
                          : "Halka arz edilecek toplam lot.",
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 26),

            _KutuBaslik(
              "Fonun Kullanım Yeri",
              Icons.account_balance_wallet_outlined,
            ),
            _GeniPanel(
              icerik: arz['fon_kullanim']?.toString() ?? "Açıklanmadı.",
            ),
            const SizedBox(height: 26),

            _KutuBaslik("Halka Arz Şekli ve Taahhütler", Icons.gavel_outlined),
            _GeniPanel(
              icerik:
                  "${arz['halka_arz_sekli']}\n\nSatmama Taahhüdü:\n${arz['taahhut']}",
            ),
            const SizedBox(height: 26),

            _KutuBaslik("Özet Finansal Tablo", Icons.bar_chart_rounded),
            _FinansalTabloPaneli(
              yapisal: arz['finansal_tablo_yapisal'],
              yedekMetin: arz['finansal_tablo']?.toString() ?? "Açıklanmadı.",
            ),
            const SizedBox(height: 26),

            _KutuBaslik("Tahsisat Grupları", Icons.pie_chart_outline_rounded),
            _GeniPanel(
              icerik: arz['tahsisat']?.toString() ?? "Açıklanmadı.",
              bosMesaji:
                  "Tahsisat dağılımı henüz izahnamede yayınlanmamış görünüyor.",
            ),
            const SizedBox(height: 26),

            _KutuBaslik(
              arz['dagitim_tipi']?.toString() ?? "Lot Dağıtımı",
              Icons.people_alt_outlined,
            ),
            _GeniPanel(
              icerik:
                  arz['dagitim_tablosu']?.toString() ?? "Henüz açıklanmadı.",
              isMonospace: true,
              bosMesaji:
                  "Lot dağıtım tablosu, talep toplama bittikten sonra "
                  "yayınlanır. Şu an bu veri kaynakta bulunmuyor.",
            ),
            const SizedBox(height: 26),

            _KutuBaslik("Tahmini Lot Hesaplayıcı", Icons.calculate_outlined),
            LotHesaplayici(
              // DÜZELTME: Eskiden TOPLAM pay sayısı kullanılıyordu ve
              // kişi başı lot 2-2,5 kat fazla çıkıyordu. Bireysel
              // yatırımcılar arasında yalnızca BİREYSEL TAHSİSAT
              // dağıtılır; kurumsal ve yurt dışı paylar ayrı gruplara
              // gider. Çitlekçi: toplam 36,5 mn lot ama bireysele
              // ayrılan 14,6 mn lot (%40).
              beklenenKatilimci: (arz['beklenen_katilimci'] as num?)?.toInt(),
              bireyselLot: arz['bireysel_pay_sayisi'],
              bireyselOran: arz['bireysel_tahsisat_orani'],
              payMiktariSayi: arz['pay_sayisi_sayi'],
              fiyatSayi: arz['fiyat_sayi'],
              payMiktariStr: arz['pay_miktari']?.toString(),
              fiyatStr: arz['fiyat']?.toString(),
            ),
            if ((arz['fiyat_sayi'] ?? 0) > 0) ...[
              const SizedBox(height: 26),
              _KutuBaslik(
                "Tavan Serisi Hesaplayıcı",
                Icons.trending_up_rounded,
              ),
              TavanHesaplayici(
                fiyat: (arz['fiyat_sayi'] as num).toDouble(),
                varsayilanLot: (arz['kisi_basi_tahmini_lot'] as num?)?.round(),
                benzer: (arz['benzer_arzlar'] as Map?)?.cast<String, dynamic>(),
              ),
            ],
            const SizedBox(height: 24),
            const YatirimUyarisiNotu(),
          ],
        ),
      ),
    );
  }

  Widget _KutuBaslik(String baslik, IconData ikon) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12.0),
      child: Row(
        children: [
          Icon(ikon, color: AppColors.textTertiary, size: 19),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              baslik,
              style: const TextStyle(
                fontSize: 16.5,
                fontWeight: FontWeight.w700,
                letterSpacing: -0.2,
                color: AppColors.textPrimary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------
// 🧩 WIDGET SINIFLARI
// ---------------------------------------------------------

/// İlk yükleme göstergesi. Ücretsiz sunucu uykudan uyanırken bekleme uzun
/// sürebildiği için geçen süreye göre açıklayıcı mesaj gösterir; kullanıcı
/// (ve mağaza inceleyicisi) uygulamanın donduğunu düşünmesin.
class _YukleniyorGorunumu extends StatefulWidget {
  const _YukleniyorGorunumu();
  @override
  State<_YukleniyorGorunumu> createState() => _YukleniyorGorunumuState();
}

class _YukleniyorGorunumuState extends State<_YukleniyorGorunumu> {
  late final Timer _sayac;
  int _saniye = 0;

  @override
  void initState() {
    super.initState();
    _sayac = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _saniye++);
    });
  }

  @override
  void dispose() {
    _sayac.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final String baslik;
    final String alt;
    if (_saniye < 4) {
      baslik = "Halka arzlar yükleniyor";
      alt = "";
    } else if (_saniye < 15) {
      baslik = "Sunucuya bağlanılıyor";
      alt = "Güncel arz listesi ve analizler hazırlanıyor.";
    } else {
      baslik = "Sunucu uyanıyor…";
      alt =
          "Bir süre kullanılmadığında sunucunun açılması 1 dakikayı "
          "bulabilir. Bu yalnızca ilk açılışta olur, lütfen bekleyin.";
    }
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(
              width: 34,
              height: 34,
              child: CircularProgressIndicator(
                color: AppColors.accent,
                strokeWidth: 3,
              ),
            ),
            const SizedBox(height: 22),
            Text(
              baslik,
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                color: AppColors.textPrimary,
              ),
            ),
            if (alt.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                alt,
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontSize: 13.5,
                  height: 1.5,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
            if (_saniye >= 15) ...[
              const SizedBox(height: 14),
              Text(
                "$_saniye sn",
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textTertiary,
                  fontFeatures: [FontFeature.tabularFigures()],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _GeniPanel extends StatelessWidget {
  final String icerik;
  final bool isMonospace;

  /// Veri gelmediğinde gösterilecek açıklayıcı mesaj. Kullanıcı
  /// "Açıklanmadı." yazısını uygulama hatası sanmasın diye eklendi.
  final String? bosMesaji;

  const _GeniPanel({
    required this.icerik,
    this.isMonospace = false,
    this.bosMesaji,
  });

  static const Set<String> _bosDegerler = {
    "Açıklanmadı.",
    "Açıklanmadı",
    "Henüz açıklanmadı.",
    "Lot tablosu bulunamadı.",
    "-",
    "",
  };

  @override
  Widget build(BuildContext context) {
    final bool bos = _bosDegerler.contains(icerik.trim());
    if (bos && bosMesaji != null) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(
              Icons.info_outline_rounded,
              size: 18,
              color: AppColors.textTertiary,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                bosMesaji!,
                style: const TextStyle(
                  fontSize: 12.5,
                  height: 1.5,
                  color: AppColors.textTertiary,
                ),
              ),
            ),
          ],
        ),
      );
    }
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Text(
        icerik,
        style: TextStyle(
          fontSize: 13.5,
          color: AppColors.textSecondary,
          height: 1.6,
          fontFamily: isMonospace ? 'Courier' : null,
        ),
      ),
    );
  }
}

/// 3,5 -> "3,5" ; 4.0 -> "4"
String _sayiKisa(dynamic d) {
  if (d is! num) return "-";
  return d == d.roundToDouble()
      ? d.toStringAsFixed(0)
      : d.toStringAsFixed(1).replaceAll('.', ',');
}

/// Kısa vade göstergesinin NEDEN o değerde olduğunu ve göstergesi benzer
/// olan geçmiş arzların GERÇEKTE ne yaptığını gösterir. Amaç, tek bir
/// sayının "tahmin" gibi okunmasını önlemek: kullanıcı hem etkenleri hem
/// de geçmişteki dağılımı (kötü sonuçlar dahil) görür.
class _KisaVadePaneli extends StatelessWidget {
  final double skor;
  final List<dynamic> etkenler;
  final Map<String, dynamic>? benzer;
  const _KisaVadePaneli({
    required this.skor,
    required this.etkenler,
    this.benzer,
  });

  Widget _istatistik(String baslik, String deger, Color renk) {
    return Expanded(
      child: Column(
        children: [
          Text(
            deger,
            style: TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w800,
              color: renk,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
          const SizedBox(height: 4),
          Text(
            baslik,
            textAlign: TextAlign.center,
            style: const TextStyle(
              fontSize: 10.5,
              height: 1.25,
              color: AppColors.textTertiary,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final b = benzer;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.insights_rounded,
                color: AppColors.info,
                size: 20,
              ),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  "KISA VADE GÖSTERGESİ",
                  style: TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.3,
                    color: AppColors.textPrimary,
                  ),
                ),
              ),
              Text(
                "${skor.toStringAsFixed(0)}/100",
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w800,
                  color: AppColors.info,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          ...etkenler.map((e) {
            final Map m = e as Map;
            final String etki = m['etki']?.toString() ?? "";
            final num puan = (m['puan'] as num?) ?? 0;
            final Color renk = etki == "olumlu"
                ? AppColors.positive
                : etki == "olumsuz"
                ? AppColors.danger
                : AppColors.textTertiary;
            return Padding(
              padding: const EdgeInsets.only(bottom: 9),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 42,
                    child: Text(
                      etki == "bilinmiyor"
                          ? "?"
                          : "${puan > 0 ? '+' : ''}${puan.toStringAsFixed(0)}",
                      style: TextStyle(
                        fontSize: 13.5,
                        fontWeight: FontWeight.w800,
                        color: renk,
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          m['ad']?.toString() ?? "",
                          style: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                            color: AppColors.textPrimary,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          m['aciklama']?.toString() ?? "",
                          style: const TextStyle(
                            fontSize: 11.5,
                            height: 1.35,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            );
          }),
          if (b != null) ...[
            const Divider(color: AppColors.border, height: 20),
            Text(
              "Geçmişte göstergesi benzer olan ${b['ornek_sayisi']} halka arz "
              "(${b['donem']})",
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _istatistik(
                  "tavan serisi\n(medyan gün)",
                  _sayiKisa(b['tavan_medyan']),
                  AppColors.textPrimary,
                ),
                _istatistik(
                  "arzların yarısı\n(tavan günü)",
                  "${b['tavan_alt_ceyrek']}–${b['tavan_ust_ceyrek']}",
                  AppColors.textPrimary,
                ),
                _istatistik(
                  "hiç tavan\nyapmayan",
                  "%${b['tavansiz_yuzde']}",
                  AppColors.warning,
                ),
                if (b['getiri_20g_ekside_yuzde'] != null)
                  _istatistik(
                    "20 gün sonra\nekside olan",
                    "%${b['getiri_20g_ekside_yuzde']}",
                    AppColors.danger,
                  ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          const Text(
            "Gösterge; kişi başı dağıtım, piyasa momentumu, arz büyüklüğü ve "
            "temel kaliteden hesaplanır ve geçmiş halka arzların gerçek "
            "sonuçlarıyla sınanmıştır. Geçmiş sonuçlar geleceği göstermez; "
            "bu bir getiri tahmini veya yatırım tavsiyesi değildir.",
            style: TextStyle(
              fontSize: 11,
              fontStyle: FontStyle.italic,
              height: 1.4,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

/// YENİ WIDGET: İlk gün satış baskısı paneli.
/// Kullanıcı "yüksek puan = tavan yapar" şeklinde okumasın diye,
/// baskı seviyesi ayrı, görünür ve gerekçeli biçimde gösteriliyor.
class _BaskiPaneli extends StatelessWidget {
  final double skor;
  final String seviye;
  final String uyari;
  final List<dynamic> gerekceler;
  final int ayniHaftaArz;
  final List<dynamic> rakipArzlar;
  final num? kisiBasiTutar;
  final num? kisiBasiLot;

  const _BaskiPaneli({
    required this.skor,
    required this.seviye,
    required this.uyari,
    required this.gerekceler,
    required this.ayniHaftaArz,
    required this.rakipArzlar,
    this.kisiBasiTutar,
    this.kisiBasiLot,
  });

  @override
  Widget build(BuildContext context) {
    final Color renk = skor >= 85
        ? AppColors.danger
        : skor >= 65
        ? AppColors.warning
        : AppColors.info;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: renk.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: renk.withValues(alpha: 0.45), width: 1.4),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.waves_rounded, color: renk, size: 20),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  "İLK GÜN SATIŞ BASKISI",
                  style: TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.3,
                    color: AppColors.textPrimary,
                  ),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 4,
                ),
                decoration: BoxDecoration(
                  color: renk,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  "$seviye · ${skor.toStringAsFixed(0)}",
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    color: Colors.white,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
              value: (skor / 100).clamp(0.0, 1.0),
              minHeight: 7,
              backgroundColor: AppColors.border,
              valueColor: AlwaysStoppedAnimation<Color>(renk),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            _baskiMetniSadelestir(uyari),
            style: const TextStyle(
              fontSize: 13.5,
              height: 1.55,
              color: AppColors.textPrimary,
            ),
          ),
          if (kisiBasiTutar != null) ...[
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.bg.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          "Tahmini kişi başı dağıtım",
                          style: TextStyle(
                            fontSize: 10.5,
                            color: AppColors.textTertiary,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          "₺${_binlikAyrac(kisiBasiTutar)}"
                          "${kisiBasiLot != null ? " · ~${kisiBasiLot!.toStringAsFixed(0)} lot" : ""}",
                          style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w800,
                            color: AppColors.textPrimary,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (ayniHaftaArz > 1)
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        const Text(
                          "Aynı dönemdeki arz",
                          style: TextStyle(
                            fontSize: 10.5,
                            color: AppColors.textTertiary,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          "$ayniHaftaArz adet",
                          style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w800,
                            color: AppColors.warning,
                          ),
                        ),
                      ],
                    ),
                ],
              ),
            ),
          ],
          if (gerekceler.isNotEmpty) ...[
            const SizedBox(height: 12),
            const Text(
              "Neden?",
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w700,
                color: AppColors.textTertiary,
              ),
            ),
            const SizedBox(height: 6),
            ...gerekceler.map(
              (g) => Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      "• ",
                      style: TextStyle(
                        color: renk,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    Expanded(
                      child: Text(
                        g.toString(),
                        style: const TextStyle(
                          fontSize: 12.5,
                          height: 1.45,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
          const SizedBox(height: 10),
          const Text(
            "Kişi başına düşen tutarın son arzlara göre düzeyini ölçer. "
            "2023'ten bu yana tamamlanan halka arzların gerçek sonuçlarıyla "
            "sınanmıştır; yine de bir olasılık göstergesidir, fiyat tahmini "
            "değildir.",
            style: TextStyle(
              fontSize: 11,
              fontStyle: FontStyle.italic,
              height: 1.4,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

/// YENİ: Halka arz fiyatının pahalı mı ucuz mu olduğunu gösterir.
/// F/K, PD/DD ve FD/FAVÖK oranlarını sektör ortalamasıyla karşılaştırır.
class _DegerlemePaneli extends StatelessWidget {
  final Map<String, dynamic> degerleme;
  const _DegerlemePaneli({required this.degerleme});

  /// Yorum metnine göre renk: ucuz -> yeşil, pahalı -> kırmızı
  Color _renk(String yorum) {
    final y = _trKucult(yorum);
    if (y.contains("altında")) return AppColors.positive;
    if (y.contains("çok üzerinde") || y.contains("2 katından")) {
      return AppColors.danger;
    }
    if (y.contains("üzerinde")) return AppColors.warning;
    if (y.contains("zararda")) return AppColors.danger;
    return AppColors.info;
  }

  @override
  Widget build(BuildContext context) {
    final List carpanlar = (degerleme['carpanlar'] as List?) ?? [];
    final String genel = degerleme['genel_yorum']?.toString() ?? "";
    final num? pd = degerleme['piyasa_degeri'];
    final bool bayat = degerleme['sektor_verisi_bayat'] ?? false;
    final Color genelRenk = _renk(genel);

    String pdMetin = "";
    if (pd != null) {
      if (pd >= 1e9) {
        pdMetin =
            "${(pd / 1e9).toStringAsFixed(1).replaceAll('.', ',')} milyar TL";
      } else if (pd >= 1e6) {
        pdMetin = "${(pd / 1e6).toStringAsFixed(0)} milyon TL";
      }
    }

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (genel.isNotEmpty) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: genelRenk.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: genelRenk.withValues(alpha: 0.35)),
              ),
              child: Text(
                genel,
                style: TextStyle(
                  fontSize: 13.5,
                  height: 1.45,
                  fontWeight: FontWeight.w600,
                  color: genelRenk,
                ),
              ),
            ),
            const SizedBox(height: 14),
          ],
          if (pdMetin.isNotEmpty) ...[
            Row(
              children: [
                const Icon(
                  Icons.business_center_outlined,
                  size: 15,
                  color: AppColors.textTertiary,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    "Şirket değeri: $pdMetin",
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
          ],
          ...carpanlar.map((c) {
            final Map m = c as Map;
            final String yorum = m['yorum']?.toString() ?? "";
            final Color renk = _renk(yorum);
            final deger = m['deger'];
            return Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      SizedBox(
                        width: 66,
                        child: Text(
                          m['ad']?.toString() ?? "",
                          style: const TextStyle(
                            fontSize: 13.5,
                            fontWeight: FontWeight.w800,
                            color: AppColors.textPrimary,
                          ),
                        ),
                      ),
                      Text(
                        deger == null ? "—" : deger.toString(),
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w800,
                          color: renk,
                          fontFeatures: const [FontFeature.tabularFigures()],
                        ),
                      ),
                      if (m['sektor_ortalamasi'] != null)
                        Text(
                          "emsal ~${m['sektor_ortalamasi']}",
                          style: const TextStyle(
                            fontSize: 11,
                            color: AppColors.textTertiary,
                          ),
                        ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 3,
                        ),
                        decoration: BoxDecoration(
                          color: renk.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(20),
                        ),
                        child: Text(
                          yorum,
                          style: TextStyle(
                            fontSize: 10.5,
                            fontWeight: FontWeight.w700,
                            color: renk,
                          ),
                        ),
                      ),
                    ],
                  ),
                  if ((m['aciklama']?.toString() ?? "").isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        m['aciklama'].toString(),
                        style: const TextStyle(
                          fontSize: 11,
                          height: 1.35,
                          color: AppColors.textTertiary,
                        ),
                      ),
                    ),
                ],
              ),
            );
          }),
          if ((degerleme['duzeltme_notu']?.toString() ?? "").isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                degerleme['duzeltme_notu'].toString(),
                style: const TextStyle(
                  fontSize: 10.5,
                  height: 1.35,
                  color: AppColors.textTertiary,
                ),
              ),
            ),
          if (degerleme['bist100_medyan'] is Map)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                "Karşılaştırma için: BIST 100 hisselerinde medyan F/K "
                "${degerleme['bist100_medyan']['fk']}, PD/DD "
                "${degerleme['bist100_medyan']['pddd']}. Halka arzlar genellikle "
                "borsa ortalamasından yüksek çarpanlarla fiyatlanır.",
                style: const TextStyle(
                  fontSize: 10.5,
                  height: 1.35,
                  color: AppColors.textTertiary,
                ),
              ),
            ),
          Text(
            bayat
                ? "Emsal verisi güncel olmayabilir; oranları bu kısıtla okuyun."
                : "Oranlar geçmiş halka arzların medyanıyla karşılaştırılmıştır. "
                      "Düşük oran her zaman iyi anlamına gelmez.",
            style: const TextStyle(
              fontSize: 10.5,
              fontStyle: FontStyle.italic,
              height: 1.35,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

class _NitelikPaneli extends StatelessWidget {
  final String baslik;
  final IconData ikon;
  final Color renk;
  final List<dynamic> maddeler;
  final Color? arkaplan;
  const _NitelikPaneli({
    required this.baslik,
    required this.ikon,
    required this.renk,
    required this.maddeler,
    this.arkaplan,
  });

  @override
  Widget build(BuildContext context) {
    Color gercekArkaplan = arkaplan ?? renk.withValues(alpha: 0.05);
    Color metinRengi = arkaplan != null ? Colors.white : AppColors.textPrimary;
    Color ikonMetinRengi = arkaplan != null ? Colors.white : renk;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: gercekArkaplan,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: renk.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(ikon, color: ikonMetinRengi, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  baslik,
                  style: TextStyle(
                    fontSize: 15.5,
                    fontWeight: FontWeight.bold,
                    color: ikonMetinRengi,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          ...maddeler.map((madde) {
            String yazi = _onEkTemizle(madde.toString());
            String puan = "";
            final match = RegExp(
              r'\(([-+0-9.]+\s*Puan)\)',
              caseSensitive: false,
            ).firstMatch(yazi);
            if (match != null) {
              puan = match.group(1)!;
              yazi = yazi
                  .replaceAll(match.group(0)!, "")
                  .replaceAll(RegExp(r'\[.*?\] '), "")
                  .trim();
            }
            return Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (puan.isNotEmpty)
                    Container(
                      margin: const EdgeInsets.only(top: 2, right: 10),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 6,
                        vertical: 2,
                      ),
                      decoration: BoxDecoration(
                        color: renk,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        puan,
                        style: const TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                          color: Colors.white,
                        ),
                      ),
                    )
                  else
                    Text(
                      "• ",
                      style: TextStyle(
                        color: ikonMetinRengi,
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  Expanded(
                    child: Text(
                      yazi,
                      style: TextStyle(
                        fontSize: 13.5,
                        color: metinRengi,
                        height: 1.4,
                      ),
                    ),
                  ),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }
}

class _SkorGostergesi extends StatelessWidget {
  final double skor;
  final Color renk;
  final String etiket;

  /// Etiketin altında görünen küçük not (ör. veri kapsamı yüzdesi).
  /// Skor eksik veriyle hesaplandığında kullanıcıyı sessizce uyarır.
  final String? altNot;

  const _SkorGostergesi({
    required this.skor,
    required this.renk,
    this.etiket = "",
    this.altNot,
  });

  @override
  Widget build(BuildContext context) {
    if (skor == 0.0) {
      return Container(
        width: 54,
        height: 54,
        decoration: BoxDecoration(
          color: renk.withValues(alpha: 0.1),
          shape: BoxShape.circle,
          border: Border.all(color: renk.withValues(alpha: 0.4)),
        ),
        child: Icon(Icons.hourglass_empty_rounded, color: renk, size: 20),
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          width: 54,
          height: 54,
          child: Stack(
            alignment: Alignment.center,
            children: [
              SizedBox(
                width: 54,
                height: 54,
                child: CircularProgressIndicator(
                  value: 1,
                  strokeWidth: 4,
                  color: AppColors.border,
                ),
              ),
              SizedBox(
                width: 54,
                height: 54,
                child: CircularProgressIndicator(
                  value: (skor / 100).clamp(0.0, 1.0),
                  strokeWidth: 4,
                  strokeCap: StrokeCap.round,
                  backgroundColor: Colors.transparent,
                  valueColor: AlwaysStoppedAnimation<Color>(renk),
                ),
              ),
              Text(
                skor.toStringAsFixed(0),
                style: TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w800,
                  color: renk,
                ),
              ),
            ],
          ),
        ),
        if (etiket.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(
            etiket,
            style: const TextStyle(
              fontSize: 10,
              color: AppColors.textTertiary,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
        if (altNot != null) ...[
          const SizedBox(height: 2),
          Text(
            altNot!,
            style: const TextStyle(
              fontSize: 8.5,
              color: AppColors.neutral,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ],
    );
  }
}

class _Rozet extends StatelessWidget {
  final String yazi;
  final Color renk;
  final IconData ikon;
  const _Rozet(this.yazi, this.renk, this.ikon);
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: renk.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: renk.withValues(alpha: 0.3)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(ikon, size: 14, color: renk),
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              yazi,
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.bold,
                color: renk,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _BilgiKarti extends StatelessWidget {
  final String baslik;
  final dynamic deger;
  final IconData ikon;
  const _BilgiKarti(this.baslik, this.deger, this.ikon);
  @override
  Widget build(BuildContext context) {
    // DÜZELTME: `deger` parametresi API'den `String` olarak geldiği
    // varsayılmıştı; null gelirse (`arz['fiyat']` gibi alanlar Map'te
    // yoksa) doğrudan Text widget'ına null vermek hataya yol açardı.
    final String gosterilecekDeger = (deger ?? "-").toString();
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Row(
              children: [
                Icon(ikon, size: 14, color: AppColors.textTertiary),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    baslik,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 10.5,
                      color: AppColors.textSecondary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              gosterilecekDeger,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.bold,
                color: AppColors.textPrimary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// DEĞİŞİKLİK: Finansal tablo eskiden düz metin olarak alt alta
/// yazılıyordu ve okunmuyordu. Artık sunucudan gelen yapısal veriyle
/// gerçek bir tablo çiziliyor (kalem | dönem | dönem | dönem).
/// Yapısal veri gelmezse eski düz metne geri düşer.
class _FinansalTabloPaneli extends StatelessWidget {
  final dynamic yapisal;
  final String yedekMetin;
  const _FinansalTabloPaneli({required this.yapisal, required this.yedekMetin});

  @override
  Widget build(BuildContext context) {
    List<dynamic> donemler = [];
    List<dynamic> satirlar = [];

    if (yapisal is Map) {
      donemler = (yapisal['donemler'] as List?) ?? [];
      satirlar = (yapisal['satirlar'] as List?) ?? [];
    }

    if (satirlar.isEmpty) {
      return _Kutu(
        child: Text(
          yedekMetin.trim().isEmpty ? "Açıklanmadı." : yedekMetin.trim(),
          style: const TextStyle(
            fontSize: 13.5,
            height: 1.6,
            color: AppColors.textSecondary,
          ),
        ),
      );
    }

    const double kalemGenisligi = 132;
    const double hucreGenisligi = 92;

    return _Kutu(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            physics: const BouncingScrollPhysics(),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Başlık satırı
                Container(
                  decoration: const BoxDecoration(
                    color: AppColors.surfaceElevated,
                    borderRadius: BorderRadius.only(
                      topLeft: Radius.circular(15),
                      topRight: Radius.circular(15),
                    ),
                  ),
                  padding: const EdgeInsets.symmetric(
                    vertical: 11,
                    horizontal: 14,
                  ),
                  child: Row(
                    children: [
                      const SizedBox(
                        width: kalemGenisligi,
                        child: Text(
                          "KALEM",
                          style: TextStyle(
                            fontSize: 10.5,
                            fontWeight: FontWeight.w800,
                            letterSpacing: 0.5,
                            color: AppColors.textTertiary,
                          ),
                        ),
                      ),
                      ...donemler.map(
                        (d) => SizedBox(
                          width: hucreGenisligi,
                          child: Text(
                            d.toString(),
                            textAlign: TextAlign.right,
                            style: const TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w800,
                              color: AppColors.accent,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                // Veri satırları
                ...satirlar.asMap().entries.map((entry) {
                  final int i = entry.key;
                  final Map row = entry.value as Map;
                  final List degerler = (row['degerler'] as List?) ?? [];
                  return Container(
                    padding: const EdgeInsets.symmetric(
                      vertical: 10,
                      horizontal: 14,
                    ),
                    decoration: BoxDecoration(
                      color: i.isEven
                          ? Colors.transparent
                          : AppColors.bg.withValues(alpha: 0.35),
                      border: const Border(
                        top: BorderSide(color: AppColors.border, width: 0.6),
                      ),
                    ),
                    child: Row(
                      children: [
                        SizedBox(
                          width: kalemGenisligi,
                          child: Text(
                            row['kalem']?.toString() ?? "-",
                            style: const TextStyle(
                              fontSize: 12.5,
                              fontWeight: FontWeight.w600,
                              color: AppColors.textSecondary,
                            ),
                          ),
                        ),
                        ...degerler.map((d) {
                          final String metin = d.toString();
                          final bool negatif =
                              metin.startsWith("-") && metin != "-";
                          return SizedBox(
                            width: hucreGenisligi,
                            child: Text(
                              metin,
                              textAlign: TextAlign.right,
                              style: TextStyle(
                                fontSize: 12.5,
                                fontWeight: FontWeight.w700,
                                fontFeatures: const [
                                  FontFeature.tabularFigures(),
                                ],
                                color: negatif
                                    ? AppColors.danger
                                    : AppColors.textPrimary,
                              ),
                            ),
                          );
                        }),
                      ],
                    ),
                  );
                }),
              ],
            ),
          ),
          if (donemler.length > 2)
            const Padding(
              padding: EdgeInsets.fromLTRB(14, 8, 14, 12),
              child: Text(
                "Tabloyu yana kaydırarak tüm dönemleri görebilirsiniz.",
                style: TextStyle(
                  fontSize: 10.5,
                  fontStyle: FontStyle.italic,
                  color: AppColors.textTertiary,
                ),
              ),
            )
          else
            const SizedBox(height: 6),
        ],
      ),
    );
  }
}

/// Ortak kutu sarmalayıcı — tekrar eden Container kodunu azaltır.
class _Kutu extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  const _Kutu({required this.child, this.padding = const EdgeInsets.all(16)});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: child,
    );
  }
}

class _DetayHucresiYorumlu extends StatelessWidget {
  final String baslik, deger, yorum;
  const _DetayHucresiYorumlu({
    required this.baslik,
    required this.deger,
    required this.yorum,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            baslik.toUpperCase(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 10,
              color: AppColors.textTertiary,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.4,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            deger,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 13.5,
              color: AppColors.textPrimary,
              fontWeight: FontWeight.w700,
            ),
          ),
          if (yorum.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              yorum,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 10,
                color: AppColors.textTertiary,
                fontStyle: FontStyle.italic,
                height: 1.2,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class LotHesaplayici extends StatefulWidget {
  /// DEĞİŞİKLİK: Artık sunucudan gelen HAZIR SAYISAL değerleri kullanıyor.
  /// Eski hâlde pay miktarı metinden ayıklanıyordu ve
  /// "243.243.243 Lot (ek satış dahil 279.729.729 Lot)" gibi bir ifadede
  /// tüm rakamlar birleştirilip 243243243279729729 gibi anlamsız bir sayı
  /// çıkıyordu — hesaplayıcının çalışmamasının sebebi buydu.
  /// Sayısal alan gelmezse metinden İLK sayıyı ayıklayan güvenli bir
  /// yönteme geri düşer.
  final num? payMiktariSayi;
  final num? fiyatSayi;
  final String? payMiktariStr;
  final String? fiyatStr;

  /// Bireysel yatırımcıya ayrılan lot miktarı. Hesap ÖNCELİKLE bunu
  /// kullanır; yoksa toplam paya düşer ve bunu kullanıcıya bildirir.
  final num? bireyselLot;
  final num? bireyselOran;

  /// Sunucunun son arzların gerçek katılımından hesapladığı beklenti.
  /// Eski sabit 2.500.000 değeri 2026'daki gerçek katılımın (~500-750 bin)
  /// 3-4 katıydı ve kişi başı lotu o oranda düşük gösteriyordu.
  final int? beklenenKatilimci;

  const LotHesaplayici({
    super.key,
    this.beklenenKatilimci,
    this.payMiktariSayi,
    this.fiyatSayi,
    this.payMiktariStr,
    this.fiyatStr,
    this.bireyselLot,
    this.bireyselOran,
  });

  @override
  State<LotHesaplayici> createState() => _LotHesaplayiciState();
}

class _LotHesaplayiciState extends State<LotHesaplayici> {
  double pay = 0.0;
  double fiyat = 0.0;
  bool bireyselKullanildi = false;
  late int _hesaplananKisi = (widget.beklenenKatilimci ?? 0) > 0
      ? widget.beklenenKatilimci!
      : 650000;
  late final TextEditingController _kisiController = TextEditingController(
    text: _hesaplananKisi.toString(),
  );

  /// Metinden İLK sayıyı güvenli şekilde ayıklar.
  /// "1.234.567,89 TL" -> 1234567.89 ; "15,00 - 18,00 TL" -> 15.0
  static double? _ilkSayi(String? metin) {
    if (metin == null || metin.trim().isEmpty) return null;
    // Binlik ayraçlı + ondalıklı: 1.234.567,89
    final m1 = RegExp(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?').firstMatch(metin);
    if (m1 != null) {
      return double.tryParse(
        m1.group(0)!.replaceAll('.', '').replaceAll(',', '.'),
      );
    }
    // Ondalıklı: 18,50
    final m2 = RegExp(r'\d+,\d+').firstMatch(metin);
    if (m2 != null) {
      return double.tryParse(m2.group(0)!.replaceAll(',', '.'));
    }
    // Nokta ondalıklı: 18.50
    final m3 = RegExp(r'\d+\.\d+').firstMatch(metin);
    if (m3 != null) return double.tryParse(m3.group(0)!);
    // Düz tam sayı
    final m4 = RegExp(r'\d+').firstMatch(metin);
    if (m4 != null) return double.tryParse(m4.group(0)!);
    return null;
  }

  @override
  void initState() {
    super.initState();
    // Öncelik: bireysel tahsisat -> toplam pay
    final double? bireysel = widget.bireyselLot?.toDouble();
    if (bireysel != null && bireysel > 0) {
      pay = bireysel;
      bireyselKullanildi = true;
    } else {
      pay =
          (widget.payMiktariSayi?.toDouble()) ??
          _ilkSayi(widget.payMiktariStr) ??
          0.0;
      bireyselKullanildi = false;
    }
    fiyat = (widget.fiyatSayi?.toDouble()) ?? _ilkSayi(widget.fiyatStr) ?? 0.0;
  }

  @override
  void dispose() {
    _kisiController.dispose();
    super.dispose();
  }

  void _hesapla() {
    FocusScope.of(context).unfocus();
    setState(() {
      final int? girilen = int.tryParse(
        _kisiController.text.replaceAll(RegExp(r'[^0-9]'), ''),
      );
      if (girilen != null && girilen > 0) _hesaplananKisi = girilen;
    });
  }

  @override
  Widget build(BuildContext context) {
    int tahminiLot = (pay > 0 && _hesaplananKisi > 0)
        ? (pay / _hesaplananKisi).floor()
        : 0;
    double tahminiTutar = tahminiLot * fiyat;

    // YENİ: Veri yoksa sessizce 0 göstermek yerine sebebini söyle.
    // Eskiden hesaplayıcı "çalışmıyor" görünüyordu çünkü pay miktarı
    // ayrıştırılamadığında hiçbir açıklama olmadan 0 yazıyordu.
    if (pay <= 0) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          children: [
            const Icon(
              Icons.info_outline_rounded,
              color: AppColors.textTertiary,
              size: 20,
            ),
            const SizedBox(width: 10),
            const Expanded(
              child: Text(
                "Halka arz edilecek pay miktarı henüz açıklanmadığı için "
                "lot hesabı yapılamıyor.",
                style: TextStyle(
                  fontSize: 13,
                  height: 1.5,
                  color: AppColors.textSecondary,
                ),
              ),
            ),
          ],
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            (widget.beklenenKatilimci ?? 0) > 0
                ? "Tahmini Katılımcı Sayısı (son arzların medyanı)"
                : "Tahmini Katılımcı Sayısı",
            style: const TextStyle(
              fontSize: 13,
              color: AppColors.textSecondary,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Container(
                  height: 50,
                  decoration: BoxDecoration(
                    color: AppColors.bg,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: TextField(
                    controller: _kisiController,
                    keyboardType: TextInputType.number,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: AppColors.textPrimary,
                    ),
                    decoration: const InputDecoration(
                      prefixIcon: Icon(
                        Icons.people_alt_outlined,
                        color: AppColors.textTertiary,
                        size: 20,
                      ),
                      border: InputBorder.none,
                      contentPadding: EdgeInsets.symmetric(vertical: 14),
                    ),
                    onSubmitted: (_) => _hesapla(),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                height: 50,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.positive,
                    foregroundColor: AppColors.bg,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                    elevation: 0,
                  ),
                  onPressed: _hesapla,
                  child: const Text(
                    "Hesapla",
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                bireyselKullanildi
                    ? Icons.check_circle_outline
                    : Icons.info_outline_rounded,
                size: 13,
                color: bireyselKullanildi
                    ? AppColors.positive
                    : AppColors.warning,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  bireyselKullanildi
                      ? "Hesap, bireysel yatırımcıya ayrılan "
                            "${_binlikAyrac(pay)} lot"
                            "${widget.bireyselOran != null ? " (%${widget.bireyselOran!.toStringAsFixed(0)})" : ""}"
                            " üzerinden yapıldı."
                      : "Bireysel tahsisat açıklanmadığı için toplam pay "
                            "miktarı kullanıldı; gerçek dağıtım daha düşük olur.",
                  style: TextStyle(
                    fontSize: 10.5,
                    height: 1.35,
                    color: bireyselKullanildi
                        ? AppColors.textTertiary
                        : AppColors.warning,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.symmetric(vertical: 20),
            decoration: BoxDecoration(
              color: AppColors.bg,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    children: [
                      const Text(
                        "Tahmini Lot",
                        style: TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        "$tahminiLot",
                        style: const TextStyle(
                          fontSize: 24,
                          fontWeight: FontWeight.bold,
                          color: AppColors.positive,
                        ),
                      ),
                    ],
                  ),
                ),
                Container(width: 1, height: 40, color: AppColors.border),
                Expanded(
                  child: Column(
                    children: [
                      const Text(
                        "Gerekli Tutar",
                        style: TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        "₺${_binlikAyrac(tahminiTutar)}",
                        style: const TextStyle(
                          fontSize: 24,
                          fontWeight: FontWeight.bold,
                          color: AppColors.info,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Tavan serisi hesaplayıcı: halka arz fiyatından başlayarak arka arkaya
/// kaç gün tavan yapılırsa elindeki lotun ne kadar edeceğini gösterir.
/// Rakip uygulamalarda en çok kullanılan araçlardan biri.
///
/// BIST kuralı: günlük fiyat değişim sınırı %10'dur ve tavan fiyatı,
/// o fiyat aralığının fiyat adımına (tick) aşağı yuvarlanır. Komisyon ve
/// vergi dahil değildir.
class TavanHesaplayici extends StatefulWidget {
  final double fiyat;
  final int? varsayilanLot;

  /// Göstergesi benzer olan geçmiş arzların gerçek sonuçları; senaryo
  /// tablosunun bir beklenti gibi okunmaması için altında gösterilir.
  final Map<String, dynamic>? benzer;
  const TavanHesaplayici({
    super.key,
    required this.fiyat,
    this.varsayilanLot,
    this.benzer,
  });

  @override
  State<TavanHesaplayici> createState() => _TavanHesaplayiciState();
}

class _TavanHesaplayiciState extends State<TavanHesaplayici> {
  late final TextEditingController _lot = TextEditingController(
    text: ((widget.varsayilanLot ?? 0) > 0 ? widget.varsayilanLot! : 100)
        .toString(),
  );

  @override
  void dispose() {
    _lot.dispose();
    super.dispose();
  }

  /// Borsa İstanbul pay piyasası fiyat adımları.
  static double _fiyatAdimi(double f) {
    if (f < 20) return 0.01;
    if (f < 50) return 0.02;
    if (f < 100) return 0.05;
    if (f < 250) return 0.10;
    if (f < 500) return 0.25;
    if (f < 1000) return 0.50;
    if (f < 2500) return 1.00;
    return 2.50;
  }

  static double _tavan(double onceki) {
    final double ham = onceki * 1.10;
    final double adim = _fiyatAdimi(ham);
    // Kayan nokta hatasına karşı küçük tolerans
    return ((ham / adim) + 1e-9).floor() * adim;
  }

  String _tl(double d) {
    final bool neg = d < 0;
    final int kurusToplam = (d.abs() * 100).round();
    final String tam = _binlikAyrac(kurusToplam ~/ 100);
    final String kurus = (kurusToplam % 100).toString().padLeft(2, '0');
    return "${neg ? '-' : ''}₺$tam,$kurus";
  }

  @override
  Widget build(BuildContext context) {
    final int lot = int.tryParse(_lot.text.replaceAll('.', '')) ?? 0;
    final double maliyet = lot * widget.fiyat;
    final List<_TavanSatiri> satirlar = [];
    double f = widget.fiyat;
    for (int gun = 1; gun <= 10; gun++) {
      f = _tavan(f);
      satirlar.add(_TavanSatiri(gun, f, lot * f - maliyet));
    }

    const TextStyle baslikStil = TextStyle(
      fontSize: 11,
      fontWeight: FontWeight.w700,
      color: AppColors.textTertiary,
    );
    const TextStyle hucre = TextStyle(
      fontSize: 13,
      color: AppColors.textPrimary,
      fontFeatures: [FontFeature.tabularFigures()],
    );

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  "Elinizdeki lot",
                  style: TextStyle(
                    fontSize: 13,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
              SizedBox(
                width: 110,
                child: TextField(
                  controller: _lot,
                  keyboardType: TextInputType.number,
                  textAlign: TextAlign.right,
                  onChanged: (_) => setState(() {}),
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: AppColors.textPrimary,
                  ),
                  decoration: InputDecoration(
                    isDense: true,
                    suffixText: " lot",
                    filled: true,
                    fillColor: AppColors.bg,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: const BorderSide(color: AppColors.border),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            "Maliyet: ${_tl(maliyet)}  ·  Arz fiyatı ${_tl(widget.fiyat)}",
            style: const TextStyle(fontSize: 12, color: AppColors.textTertiary),
          ),
          const SizedBox(height: 14),
          const Row(
            children: [
              SizedBox(width: 56, child: Text("Tavan", style: baslikStil)),
              Expanded(child: Text("Fiyat", style: baslikStil)),
              Expanded(
                child: Text(
                  "Kâr",
                  style: baslikStil,
                  textAlign: TextAlign.right,
                ),
              ),
            ],
          ),
          const Divider(color: AppColors.border, height: 16),
          ...satirlar.map(
            (s) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  SizedBox(
                    width: 56,
                    child: Text("${s.gun}. gün", style: hucre),
                  ),
                  Expanded(child: Text(_tl(s.fiyat), style: hucre)),
                  Expanded(
                    child: Text(
                      "+${_tl(s.kar)}",
                      textAlign: TextAlign.right,
                      style: hucre.copyWith(
                        color: AppColors.positive,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 10),
          if (widget.benzer != null) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: AppColors.warning.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                "Geçmişte göstergesi benzer olan "
                "${widget.benzer!['ornek_sayisi']} arzda tavan serisinin "
                "medyanı ${_sayiKisa(widget.benzer!['tavan_medyan'])} gün; "
                "%${widget.benzer!['tavansiz_yuzde']} oranında hiç tavan "
                "görülmedi.",
                style: const TextStyle(
                  fontSize: 11.5,
                  height: 1.4,
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            const SizedBox(height: 8),
          ],
          const Text(
            "Her gün %10 tavan ve BIST fiyat adımına yuvarlama varsayılır. "
            "Komisyon ve vergi dahil değildir. Tavan serisi garanti değildir; "
            "fiyat arz fiyatının altına da inebilir. Bu tablo yalnızca bir "
            "senaryo hesabıdır.",
            style: TextStyle(
              fontSize: 11,
              fontStyle: FontStyle.italic,
              height: 1.4,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

class _TavanSatiri {
  final int gun;
  final double fiyat;
  final double kar;
  const _TavanSatiri(this.gun, this.fiyat, this.kar);
}
