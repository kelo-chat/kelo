#if canImport(ActivityKit) && os(iOS)
import ActivityKit
import Foundation
import KeloCore
import KeloLive

/// The running reply's Live Activity (backlog 64b): started when the followed reply begins real work (its first visible tool step),
/// its push token and a fresh key go to the server, which keeps it up to date with sealed pushes; ended when the reply ends.
@MainActor
public final class ReplyLiveActivity {
    public static let shared = ReplyLiveActivity()
    private var activity: Activity<KeloLiveAttributes>?
    private var keyId = ""
    private var turnId = ""
    private var cancelFollow: (() -> Void)?
    private var tokenTask: Task<Void, Never>?
    /// The push environment of this build (development / production); the launch sets it from the push registration's own rule.
    public static var environment: () -> String = { "development" }

    func stepStarted(turnId: String, label: String, api: KeloApi, cancel: @escaping () -> Void) {
        guard activity == nil, ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        LiveKeys.accessGroup = Bundle.main.object(forInfoDictionaryKey: "KeloLiveKeychainGroup") as? String
        guard let made = LiveKeys.make() else { return }
        let first = LiveSeal.seal(LivePlain(step: label, site: "", state: "working"), key: made.key) ?? ""
        do {
            let a = try Activity.request(attributes: KeloLiveAttributes(turnId: turnId, keyId: made.id),
                                         content: .init(state: .init(v: 1, n: 1, sealed: first), staleDate: Date().addingTimeInterval(300)),
                                         pushType: .token)
            activity = a; keyId = made.id; self.turnId = turnId; cancelFollow = cancel
            let keyB64 = made.key.base64EncodedString()
            let environment = ReplyLiveActivity.environment()
            tokenTask = Task {
                for await token in a.pushTokenUpdates {
                    _ = try? await api.request("POST", "/api/live-activity", body: .object([
                        "turn_id": .string(turnId), "token": .string(LiveToken.hex(token)),
                        "environment": .string(environment), "key": .string(keyB64)]))
                }
            }
        } catch {
            LiveKeys.remove(made.id)
        }
    }

    /// The reply is over here (done, failed or stopped): the server's last push may already have ended it.
    func finished(state: String) {
        guard let a = activity else { return }
        tokenTask?.cancel(); tokenTask = nil
        let id = keyId
        activity = nil; keyId = ""; turnId = ""; cancelFollow = nil
        Task {
            await a.end(nil, dismissalPolicy: .after(Date().addingTimeInterval(state == "done" ? 60 : 300)))
            LiveKeys.remove(id)
        }
    }

    /// The activity's Stop button: the chat following this reply stops it (its Stop), else the server is told directly.
    public static func stop(turnId: String, api: KeloApi?) async {
        let me = shared
        if me.turnId == turnId, let cancel = me.cancelFollow { cancel(); return }
        _ = try? await api?.request("DELETE", "/api/turns/\(KeloApi.segment(turnId))")
    }

    /// At launch: keys without an activity go (an activity left from before stays; the server's last push ends it).
    public static func tidy() {
        LiveKeys.accessGroup = Bundle.main.object(forInfoDictionaryKey: "KeloLiveKeychainGroup") as? String
        let alive = Activity<KeloLiveAttributes>.activities
        LiveKeys.removeAll(except: Set(alive.map(\.attributes.keyId)))
    }
}
#endif
