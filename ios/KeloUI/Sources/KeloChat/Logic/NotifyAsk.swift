import Foundation
import KeloShared
#if canImport(UserNotifications)
import UserNotifications
#endif

/// The question after the user's first message: shall Kelo say when a reply is ready? (Android notify/NotifyUi.kt AskRules and
/// NotificationAskDialog.) Asked once per phone, and only while notifications are off.
@MainActor enum NotifyAsk {
    static let askedKey = "notify.asked_in_chat"

    nonisolated static func shouldAsk(askedBefore: Bool, enabled: Bool) -> Bool { !askedBefore && !enabled }

    /// Whether to show the question now; marks it as asked either way.
    static func takeTurn(prefs: Prefs = .shared) async -> Bool {
        let asked = prefs.bool(askedKey, default: false)
        prefs.set(askedKey, true)
        if asked { return false }
        return shouldAsk(askedBefore: false, enabled: await enabled())
    }

    /// The message was held back (secret sheet), so it was not sent: the question waits for the next message (Android ChatScreen).
    static func giveBack(prefs: Prefs = .shared) { prefs.set(askedKey, false) }

    static func enabled() async -> Bool {
        #if canImport(UserNotifications)
        let s = await UNUserNotificationCenter.current().notificationSettings()
        return s.authorizationStatus == .authorized || s.authorizationStatus == .provisional
        #else
        return false
        #endif
    }

    /// "Turn on notifications": the system's own question while it may still be asked, else iPhone Settings.
    static func enable(prefs: Prefs = .shared) async {
        #if canImport(UserNotifications)
        let s = await UNUserNotificationCenter.current().notificationSettings()
        if s.authorizationStatus == .notDetermined {
            let ok = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])) ?? false
            prefs.set("notify.allowed", ok)
        } else if s.authorizationStatus == .denied {
            DeviceInfo.openAppSettings()
        }
        #endif
    }
}
