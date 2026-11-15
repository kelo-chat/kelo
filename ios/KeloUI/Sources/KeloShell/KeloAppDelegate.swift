#if os(iOS)
import UIKit
import KeloMy

/// The app delegate the SwiftUI app plugs in (`@UIApplicationDelegateAdaptor(KeloAppDelegate.self)`): the three calls iOS makes for
/// remote notifications. Everything they do is in KeloMy (PushRegistrar, KeloBackground), where it can be tested.
public final class KeloAppDelegate: NSObject, UIApplicationDelegate {
    /// iOS gave this phone its APNs device token: it goes to the user's own server (never anywhere else, never logged).
    public func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in KeloBackground.pushToken(deviceToken) }
    }

    /// No token (the simulator without push, the App ID without the Push Notifications capability, no network): quietly nothing;
    /// the polling and background-refresh paths keep working.
    public func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {}

    /// A push with content-available woke the app: fetch the details and post them in place of the generic banner.
    public func application(_ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable: Any]) async -> UIBackgroundFetchResult {
        let payload = PushRules.payload(userInfo)
        return await KeloBackground.pushArrived(payload) ? .newData : .noData
    }
}
#endif
