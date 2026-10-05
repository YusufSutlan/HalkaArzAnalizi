import Flutter
import UIKit
import workmanager_apple

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  // Dart tarafındaki Workmanager().registerPeriodicTask ile aynı kimlik olmalı
  // ve Info.plist > BGTaskSchedulerPermittedIdentifiers içinde bulunmalı.
  static let arzKontrolGorevi = "com.yusufsutlan.halkarzasistan.arzKontrol"

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    // Arka plan görevinde (yeni arz kontrolü) eklentilerin kullanılabilmesi için
    WorkmanagerPlugin.setPluginRegistrantCallback { registry in
      GeneratedPluginRegistrant.register(with: registry)
    }
    // iOS çalıştırma zamanını kendisi seçer; bu yalnızca en erken süre ipucu (6 saat)
    WorkmanagerPlugin.registerPeriodicTask(
      withIdentifier: AppDelegate.arzKontrolGorevi,
      earliestBeginInSeconds: NSNumber(value: 6 * 60 * 60)
    )
    // Uygulama açıkken de bildirim banner'ı gösterilsin
    UNUserNotificationCenter.current().delegate = self
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
  }
}
