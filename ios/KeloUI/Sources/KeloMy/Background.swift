import Foundation
import KeloCore
import KeloShared
#if canImport(UserNotifications)
import UserNotifications
#endif
#if os(iOS)
import BackgroundTasks
import UIKit
#endif

/// Whether this phone lets Kelo show notifications.
@MainActor enum NotifyPermission {
    /// The last known answer (the My row shows it at once).
    static var cached: Bool { Prefs.shared.bool("notify.allowed", default: false) }

    static func isOn() async -> Bool {
        #if canImport(UserNotifications)
        let s = await UNUserNotificationCenter.current().notificationSettings()
        #if os(iOS)
        let on = s.authorizationStatus == .authorized || s.authorizationStatus == .provisional || s.authorizationStatus == .ephemeral
        #else
        let on = s.authorizationStatus == .authorized || s.authorizationStatus == .provisional
        #endif
        Prefs.shared.set("notify.allowed", on)
        return on
        #else
        return false
        #endif
    }

    /// Asks once; after a "no" only iPhone Settings can turn it on.
    static func request() async -> Bool {
        #if canImport(UserNotifications)
        let s = await UNUserNotificationCenter.current().notificationSettings()
        if s.authorizationStatus == .notDetermined {
            let ok = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])) ?? false
            Prefs.shared.set("notify.allowed", ok)
            if ok { await PushRegistrar.shared.sync() }   // allowed now: ask iOS for the push token
            return ok
        }
        if s.authorizationStatus == .denied { DeviceInfo.openAppSettings() }
        return await isOn()
        #else
        return false
        #endif
    }
}

/// Checks GET /api/notifications and posts what is new as local notifications (Android notify/Background.kt NotificationPoller).
/// The server pushes to iPhones through APNs when the operator's key is set (ios/README.md "Push"): a generic banner that carries
/// only ids, and a silent wake-up that runs this check when iOS allows. This check also runs while the app is open and in iOS
/// background refreshes, which iOS schedules as it sees fit (often every 15–60 minutes, less on low battery): the fallback path.
@MainActor enum NotificationCheck {
    private static let cursorKey = "notify.last_posted"

    /// One check. `foreground`: the app is on screen (then nothing is posted, only the cursor moves).
    @discardableResult
    static func run(api: KeloApi, foreground: Bool) async -> Int {
        let last = Int64(Prefs.shared.int(cursorKey, default: 0))
        guard let page = try? await api.notifications(after: last > 0 ? last : nil, limit: 50) else { return 0 }
        let fresh = NotifyRules.fresh(page.items, lastPosted: last)
        var posted = 0
        if last > 0 && !foreground {
            for n in fresh { await post(n); posted += 1 }
        }
        if posted > 0 || foreground { await takeDownBarePushes() }   // the detailed ones replace the push's generic banner
        Prefs.shared.set(cursorKey, Int(NotifyRules.nextCursor(last, serverLast: page.lastId, items: page.items)))
        // the icon's number: only what waits for the owner, not unseen notifications (WorkBadge)
        if let s = try? await api.agentStatus() { AppIconBadge.set(WorkBadge.waiting(s)) }
        return posted
    }

    static func reset() { Prefs.shared.set(cursorKey, 0) }

    /// Takes down the notifications already shown and the badge (after the chat history was cleared: they name tasks that are gone).
    static func clearShown() {
        #if canImport(UserNotifications)
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
        #endif
        AppIconBadge.set(0)
    }

    /// Takes down the push banners that carry only ids ("Kelo has an update"): the details were fetched and posted, or the app is open.
    private static func takeDownBarePushes() async {
        #if canImport(UserNotifications)
        let center = UNUserNotificationCenter.current()
        let ids = await center.deliveredNotifications().filter { PushRules.isBarePush(userInfo: $0.request.content.userInfo) }.map(\.request.identifier)
        if !ids.isEmpty { center.removeDeliveredNotifications(withIdentifiers: ids) }
        #endif
    }

    private static func post(_ n: ServerNotification) async {
        #if canImport(UserNotifications)
        let content = UNMutableNotificationContent()
        content.title = n.title.isEmpty ? "Kelo" : n.title
        if n.type == NoteTypes.approvalWaiting && n.urgent { content.title = L(R.string.notify_approval_urgent) }
        if n.type == NoteTypes.briefing { content.title = L(R.string.notify_briefing) }
        content.body = NotifyRules.firstLine(n.body.isEmpty ? n.title : n.body, max: 180)
        content.sound = .default
        content.threadIdentifier = "kelo." + NotifyRules.channelOf(n.type, scheduleId: n.scheduleId, urgent: n.urgent).rawValue
        #if os(iOS)
        // a card of a task marked urgent at setup (a cancelled seat): time matters, so it may break through a Focus
        if n.urgent { content.interruptionLevel = .timeSensitive }
        #endif
        var info: [String: String] = ["open": NotifyRules.target(n).encoded]
        if let action = NotifyRules.actions(n.type).first {   // a storage warning: 정리하기 under it opens the cleanup screen
            content.categoryIdentifier = NotificationTaps.cleanCategory
            info[NotificationTaps.cleanAction] = action.encoded
        }
        content.userInfo = info
        let req = UNNotificationRequest(identifier: NotifyRules.identifier(n), content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(req)
        #endif
    }

}

/// Opens what a tapped local notification points at (its userInfo "open" is an `OpenTarget.encoded`).
@MainActor final class NotificationTaps: NSObject {
    static let shared = NotificationTaps()
    weak var app: AppModel?
    /// The storage warning's category and its 정리하기 button (the userInfo key holds where the button goes).
    nonisolated static let cleanCategory = "kelo.storage"
    nonisolated static let cleanAction = "kelo.clean"

    #if canImport(UserNotifications)
    /// The notification buttons, named in the app's language (registered again on each start, after a language change too).
    static func registerCategories() {
        let clean = UNNotificationAction(identifier: cleanAction, title: L(R.string.storage_clean_up), options: [.foreground])
        UNUserNotificationCenter.current().setNotificationCategories([
            UNNotificationCategory(identifier: cleanCategory, actions: [clean], intentIdentifiers: [], options: []),
        ])
    }
    #endif
}

#if canImport(UserNotifications)
extension NotificationTaps: UNUserNotificationCenterDelegate {
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        // a local notification carries "open"; a push carries only its kind and ids (PushRules.target maps them to the same places)
        let raw = (response.actionIdentifier == NotificationTaps.cleanAction ? info[NotificationTaps.cleanAction] as? String : nil)
            ?? PushRules.openText(userInfo: info)
        let dial = response.notification.request.content.userInfo["dial"] as? String
        Task { @MainActor in
            if let raw, let target = OpenTarget.decode(raw) { NotificationTaps.shared.app?.router.open(target) }
            #if os(iOS)
            // "Call <name>": the phone app with the number filled in (iOS asks before calling)
            if let dial, let url = URL(string: dial), url.scheme == "tel" { await UIApplication.shared.open(url) }
            #endif
            completionHandler()
        }
    }

    /// On screen: the app shows it itself, so no banner.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([])
    }
}
#endif

/// Notifications while the app is away (Android notify/*): local notifications from GET /api/notifications, checked while the app
/// runs and in iOS background app refreshes. The app target must list "chat.kelo.app.refresh" under
/// BGTaskSchedulerPermittedIdentifiers and have the "fetch" background mode.
public enum KeloBackground {
    public static let refreshId = "chat.kelo.app.refresh"
    @MainActor private static weak var model: AppModel?

    /// Called once from the app's init (BGTaskScheduler registration must happen before launch ends).
    @MainActor public static func register(app: AppModel) {
        model = app
        NotificationTaps.shared.app = app
        PushRegistrar.shared.app = app
        // signing out (or removing this phone) takes its push token back from the server while the session still works
        app.onSignOut = { api in Task { await PushRegistrar.shared.unregister(api: api) } }
        #if canImport(UserNotifications)
        UNUserNotificationCenter.current().delegate = NotificationTaps.shared
        NotificationTaps.registerCategories()
        #endif
        #if os(iOS)
        BGTaskScheduler.shared.register(forTaskWithIdentifier: refreshId, using: nil) { task in
            guard let refresh = task as? BGAppRefreshTask else { task.setTaskCompleted(success: false); return }
            let work = Task { @MainActor in
                scheduleRefresh()
                if let api = model?.api {
                    await NotificationCheck.run(api: api, foreground: false)
                    await PhoneDataSync.shared.syncAll(app: model, force: false)
                    await DeviceTasks.check(app: model)
                }
                refresh.setTaskCompleted(success: true)
            }
            refresh.expirationHandler = { work.cancel() }
        }
        #endif
    }

    /// Runs while the signed-in shell is on screen (from its `.task`); ends when cancelled. Checks every minute in the foreground
    /// (only moving the cursor, the app shows things itself), and keeps the phone's calendar and contacts sent when they are on.
    @MainActor public static func run(app: AppModel) async {
        model = app
        NotificationTaps.shared.app = app
        PushRegistrar.shared.app = app
        await PushRegistrar.shared.sync()   // a push token at every start (iOS may change it); none when notifications are off
        await PhoneDataSync.shared.syncAll(app: app, force: false)
        PhoneDataSync.shared.watch(app: app)
        await DeviceTasks.check(app: app)   // an approved calendar change or call may wait for this phone
        ApprovalHooks.afterAllow = { [weak app] in if let app { DeviceTasks.afterApproval(app: app) } }
        while !Task.isCancelled {
            if let api = app.api { await NotificationCheck.run(api: api, foreground: true) }
            try? await Task.sleep(nanoseconds: 60_000_000_000)
        }
        PhoneDataSync.shared.unwatch()
    }

    /// iOS gave this phone its APNs device token (AppDelegate): send it to the user's own server.
    @MainActor public static func pushToken(_ token: Data) {
        Task { await PushRegistrar.shared.received(token: token, api: model?.api) }
    }

    /// A push woke the app (content-available): fetch the details now and post them in place of the generic banner. Best effort:
    /// iOS may not wake the app at all (low power, force-quit); then the banner stays and a tap opens the right place.
    @MainActor public static func pushArrived(_ p: PushPayload?) async -> Bool {
        guard p != nil, let api = model?.api else { return false }
        let posted = await NotificationCheck.run(api: api, foreground: false)
        return posted > 0
    }

    /// The app went to the background: schedule the next refresh.
    @MainActor public static func scheduleRefresh() {
        #if os(iOS)
        let req = BGAppRefreshTaskRequest(identifier: refreshId)
        req.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(req)
        #endif
    }
}
