import Foundation
import KeloCore
import KeloShared
#if canImport(UserNotifications)
import UserNotifications
#endif
#if os(iOS)
import UIKit
#endif

/// Work the owner approved that only this phone can do (Android device/DeviceTasks.kt): open the phone app with a number filled in
/// (iOS then asks before calling; Kelo never places a call), or add, change or delete an event in the calendar the owner picked. The
/// server queues it after the approval (POST /api/phone/tasks/claim hands each one out once); the phone checks right after an
/// approval is answered here, when the app comes to the front and in background refreshes, then reports how it went
/// (POST /api/phone/tasks/{id}/result). The phone checks again that the row is still on before doing anything.
@MainActor final class DeviceTasks {
    private var done: [Int64] = []
    private var running = false
    private weak var app: AppModel?
    private let sync: PhoneDataSync

    init(sync: PhoneDataSync) { self.sync = sync }

    /// Some row that can do work is on: only then is there anything to ask the server about.
    func anyOn() -> Bool {
        sync.on(.dialer) || (sync.on(.calendar) && sync.writeAllowed)
    }

    /// An approval was just allowed in the app: what it allowed may be waiting for this phone now (checked a few times).
    func afterApproval(app: AppModel) {
        self.app = app
        guard anyOn() else { return }
        Task { [self] in
            for wait: UInt64 in [1_500, 4_000, 10_000] {
                try? await Task.sleep(nanoseconds: wait * 1_000_000)
                await check(app: app)
            }
        }
    }

    func check(app: AppModel?) async {
        guard let app, let api = app.api, anyOn(), !running else { return }
        self.app = app
        running = true
        defer { running = false }
        guard let json = try? await api.request("POST", "/api/phone/tasks/claim") else { return }
        for t in DeviceTask.parseList(json) where !done.contains(t.id) {
            done.append(t.id)
            if done.count > 200 { done.removeFirst(done.count - 200) }
            let (ok, outcome, error) = await run(t)
            var body: [String: JSON] = ["ok": .bool(ok)]
            if !outcome.isEmpty { body["outcome"] = .string(outcome) }
            if !error.isEmpty { body["error"] = .string(String(error.prefix(200))) }
            _ = try? await api.request("POST", "/api/phone/tasks/\(t.id)/result", body: .object(body))
        }
    }

    /// (ok, outcome, error)
    private func run(_ t: DeviceTask) async -> (Bool, String, String) {
        let s = sync
        let calWrite = s.writeAllowed && s.permitted(.calendar)
        if let why = DeviceTaskRules.refuse(t, on: { s.on($0) }, calWrite: calWrite, now: Date().timeIntervalSince1970) { return (false, "", why) }
        if t.kind == "dial" { return await dial(t) }
        let (ok, err) = s.runCalendar(t)
        if ok, let app { await s.sync(.calendar, app: app, force: true) }   // the agent sees the change at once
        return (ok, ok ? "done" : "", err)
    }

    private func dial(_ t: DeviceTask) async -> (Bool, String, String) {
        #if os(iOS)
        guard let url = URL(string: "tel:" + DeviceTaskRules.dialNumber(t.s("number"))) else { return (false, "", "no number") }
        let visible = UIApplication.shared.applicationState == .active
        switch DeviceTaskRules.dialWay(visible: visible) {
        case .open:
            let ok = await UIApplication.shared.open(url)
            return ok ? (true, "opened", "") : (false, "", "the phone app could not be opened")
        case .notify:
            // iOS does not let an app in the background open another app; a notification opens the phone app when tapped
            let settings = await UNUserNotificationCenter.current().notificationSettings()
            guard settings.authorizationStatus == .authorized else { return (false, "", "Kelo notifications are off on the phone") }
            let who = t.s("name").isEmpty ? t.s("number") : t.s("name")
            let content = UNMutableNotificationContent()
            content.title = L(R.string.device_dial_title, who)
            content.body = L(R.string.device_dial_body)
            content.sound = .default
            content.threadIdentifier = NotifyRules.thread
            content.userInfo = ["dial": url.absoluteString]
            content.interruptionLevel = .timeSensitive
            let req = UNNotificationRequest(identifier: "dial:\(t.id)", content: content, trigger: nil)
            do { try await UNUserNotificationCenter.current().add(req); return (true, "notified", "") }
            catch { return (false, "", "the notification could not be shown") }
        }
        #else
        return (false, "", "no phone app on this device")
        #endif
    }
}
