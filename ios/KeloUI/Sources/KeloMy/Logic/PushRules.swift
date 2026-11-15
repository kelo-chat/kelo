import Foundation
import KeloCore
import KeloShared
#if os(iOS)
import UIKit
#endif

/// What a remote (APNs) push carries: only the kind and ids, never text (the server's push.py `check_data`). The words on the
/// banner are the app's own ("Kelo has an update"); the details come from GET /api/notifications over the user's own server.
public struct PushPayload: Equatable, Sendable {
    public var type: String
    public var id: Int64
    public var taskId: Int64?
    public var approvalId: String?
    public var scheduleId: Int64?

    public init(type: String, id: Int64, taskId: Int64? = nil, approvalId: String? = nil, scheduleId: Int64? = nil) {
        self.type = type
        self.id = id
        self.taskId = taskId
        self.approvalId = approvalId
        self.scheduleId = scheduleId
    }
}

/// The iPhone's push rules: the token as the server wants it, which Apple host it belongs to, and where a tapped push opens.
public enum PushRules {
    /// `platform` of POST /api/push/register for an APNs token (no Firebase SDK in this app).
    public static let platform = "ios-apns"

    /// The device token as lowercase hex (APNs wants it so in the request path; the server keeps it in that form).
    public static func hex(_ token: Data) -> String { token.map { String(format: "%02x", $0) }.joined() }

    /// The app's `aps-environment`: "development" for an Xcode run (its provisioning profile says so), "production" for TestFlight
    /// and the App Store (their builds carry no embedded profile). `provision` is the file embedded.mobileprovision, if any.
    public static func environment(provision: Data?) -> String {
        guard let provision else { return "production" }
        let text = String(decoding: provision, as: UTF8.self)
        guard let r = text.range(of: #"aps-environment</key>\s*<string>[a-z]+</string>"#, options: .regularExpression) else { return "production" }
        return text[r].contains(">development<") ? "development" : "production"
    }

    /// The environment of this running build.
    public static var currentEnvironment: String {
        #if DEBUG
        return "development"
        #else
        let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision")
        return environment(provision: url.flatMap { try? Data(contentsOf: $0) })
        #endif
    }

    /// The push in a notification's userInfo (strings, as the server sends them), or nil when it is not one of ours.
    public static func payload(_ userInfo: [AnyHashable: Any]) -> PushPayload? {
        func int(_ key: String) -> Int64? {
            if let s = userInfo[key] as? String { return Int64(s) }
            return (userInfo[key] as? NSNumber)?.int64Value
        }
        guard let type = userInfo["type"] as? String, NoteTypes.all.contains(type) || type == "device_task", let id = int("id") else { return nil }
        return PushPayload(type: type, id: id, taskId: int("task_id"), approvalId: userInfo["approval_id"] as? String, scheduleId: int("schedule_id"))
    }

    /// Where a tapped push opens: the same place the local notification of that kind opens (NotifyRules.target); a follow-up's
    /// message id is not in the push, so it opens the chat.
    public static func target(_ p: PushPayload) -> OpenTarget {
        NotifyRules.target(ServerNotification(id: p.id, type: p.type, taskId: p.taskId, approvalId: p.approvalId, scheduleId: p.scheduleId))
    }

    /// What a tapped notification opens: the local one's own "open" text, else the push's kind and ids.
    public static func openText(userInfo: [AnyHashable: Any]) -> String? {
        if let open = userInfo["open"] as? String { return open }
        return payload(userInfo).map { target($0).encoded }
    }

    /// A delivered notification that is a bare push (kind and ids, no local "open" text): replaced by the detailed local
    /// notification once the app has fetched the details, or taken down when the app is on screen.
    public static func isBarePush(userInfo: [AnyHashable: Any]) -> Bool { userInfo["open"] == nil && payload(userInfo) != nil }
}

/// Sends this phone's APNs token to its own server and takes it back (POST / DELETE /api/push/register). One token per device: a
/// newer one replaces the older on the server, so a token refresh is just another register call.
@MainActor public final class PushRegistrar {
    public static let shared = PushRegistrar()
    private static let registeredKey = "push.registered"
    weak var app: AppModel?
    private let prefs: Prefs

    public init(prefs: Prefs = .shared) { self.prefs = prefs }

    /// Whether the server holds this phone's token (so turning notifications off takes it back).
    var registered: Bool { prefs.bool(Self.registeredKey, default: false) }

    /// iOS handed over a device token (on every launch that asked for one, and when Apple changes it).
    @discardableResult
    public func received(token: Data, api: KeloApi?, environment: String = PushRules.currentEnvironment) async -> Bool {
        guard let api, !token.isEmpty else { return false }
        guard (try? await api.registerPush(token: PushRules.hex(token), platform: PushRules.platform, environment: environment)) != nil else { return false }
        prefs.set(Self.registeredKey, true)
        return true
    }

    /// The server forgets this phone's token (sign-out, notifications turned off). Errors are ignored: a removed device loses its
    /// token on the server anyway, and a 401 means the server already dropped the pairing.
    public func unregister(api: KeloApi?) async {
        prefs.set(Self.registeredKey, false)
        guard let api else { return }
        _ = try? await api.unregisterPush()
    }

    /// Asks iOS for a device token when notifications are allowed (the permission ask comes first: NotifyPermission / NotifyAsk), and
    /// takes the token back from the server when they were turned off in iPhone Settings.
    public func sync() async {
        #if os(iOS)
        if await NotifyPermission.isOn() {
            UIApplication.shared.registerForRemoteNotifications()
        } else if registered {
            UIApplication.shared.unregisterForRemoteNotifications()
            await unregister(api: app?.api)
        }
        #endif
    }
}
