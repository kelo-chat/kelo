import SwiftUI
import KeloUI

/// The iPhone app's entry point. Everything else lives in the KeloUI package (apps/ios/KeloUI), so it can be type-checked and tested
/// without Xcode; this target only adds the Info.plist, the icon and the launch screen.
@main
struct KeloApp: App {
    /// Remote notifications (APNs): the device token goes to the user's own server; see KeloAppDelegate.
    @UIApplicationDelegateAdaptor(KeloAppDelegate.self) private var appDelegate
    @State private var app = KeloLaunch.makeApp()

    var body: some Scene {
        WindowGroup {
            KeloRootView(app: app)
        }
    }
}
