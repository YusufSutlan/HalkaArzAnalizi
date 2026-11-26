// /api/halkarzlar yanıtını çözer.
//
// Sunucu `status` alanıyla üç durumu ayırır:
//   "ok"            (200) halka_arzlar dolu
//   "veri_yok"      (200) aktif arz yok; son_tamamlananlar dolu
//   "kaynak_hatasi" (503) kaynak site okunamadı / sayfa yapısı değişti
// `status` alanı olmayan eski sunucu yanıtı da desteklenir (listeye bakılır).
//
// Saf fonksiyon: ağ ve arayüzden bağımsız, birim testle doğrulanabilir.

import 'dart:convert';

enum YanitDurumu { arzVar, veriYok, kaynakHatasi }

class ArzYaniti {
  final YanitDurumu durum;
  final List<dynamic> arzlar;
  final List<dynamic> sonTamamlananlar;
  final String? hataDetayi;

  const ArzYaniti(
    this.durum, {
    this.arzlar = const [],
    this.sonTamamlananlar = const [],
    this.hataDetayi,
  });
}

/// Sunucu yanıtını çözer. Tanınmayan yanıtta (ör. Render'ın uyanırken
/// döndürdüğü HTML hata sayfası, bozuk JSON, beklenmeyen kod) null döner;
/// bu durumda çağıran genel hata/tekrar deneme yoluna gider.
ArzYaniti? arzYanitiCoz(int kod, String govde) {
  Object? cozulen;
  try {
    cozulen = json.decode(govde);
  } catch (_) {
    return null;
  }
  if (cozulen is! Map) return null;
  final String? durum = cozulen['status']?.toString();

  if (kod == 200) {
    final List<dynamic> arzlar =
        (cozulen['halka_arzlar'] as List?) ?? const <dynamic>[];
    final List<dynamic> son =
        (cozulen['son_tamamlananlar'] as List?) ?? const <dynamic>[];
    final bool bos = durum == 'veri_yok' || (durum == null && arzlar.isEmpty);
    return ArzYaniti(
      bos && arzlar.isEmpty ? YanitDurumu.veriYok : YanitDurumu.arzVar,
      arzlar: arzlar,
      sonTamamlananlar: son,
    );
  }
  if (durum == 'kaynak_hatasi') {
    return ArzYaniti(
      YanitDurumu.kaynakHatasi,
      hataDetayi: cozulen['detail']?.toString(),
    );
  }
  return null;
}
