import Foundation
import KeloCore
import KeloShared

/// The calls a phone notification's buttons make (the app's API, or a test's fake).
@MainActor protocol NotifyApi {
    func pendingApprovals() async throws -> [PendingApproval]
    func answerApproval(_ id: String, approve: Bool) async throws
    func startTurn(text: String, timezone: String) async throws
}

@MainActor struct KeloNotifyApi: NotifyApi {
    let api: KeloApi
    func pendingApprovals() async throws -> [PendingApproval] { try await api.pendingApprovals() }
    /// A notification's button: the server records the answer as given from the phone's notification (`via`).
    func answerApproval(_ id: String, approve: Bool) async throws { _ = try await api.answerApproval(id, approve: approve, via: "notification") }
    func startTurn(text: String, timezone: String) async throws { _ = try await KeloTurnApi(api: api).start(text: text, timezone: timezone) }
}

/// Answers from a phone notification: Allow / Deny and Reply (Android notify/ApprovalActionReceiver, ReplyActionReceiver). The rules are
/// NotifyActionRules (KeloCore); this only does the calls and says what happens to the notification after.
@MainActor enum NotifyAnswers {
    /// `.answered` and `.gone` take the notification down; `.refused` (no answer is allowed for this item) and `.failed` say "open Kelo".
    enum Approval: Equatable { case answered, gone, refused, failed }
    /// `.blank` sends nothing and leaves the notification as it is; `.sent` takes it down (the answer comes as a normal reply notification).
    enum Reply: Equatable { case blank, sent, failed }

    /// Allow or Deny for one waiting item. The server's pending list decides what may be sent: an Allow only when the item offers "allow"
    /// (a payment never does), so a notification that is stale or from another item cannot approve by accident. Not waiting any more = gone.
    static func approve(id: String, approve: Bool, api: NotifyApi) async -> Approval {
        let items: [PendingApproval]
        do { items = try await api.pendingApprovals() } catch { return .failed }
        guard let item = items.first(where: { $0.id == id }) else { return .gone }
        guard NotifyActionRules.mayAnswer(approve: approve, offered: item.notifyActions) else { return .refused }
        do {
            try await api.answerApproval(id, approve: approve)
            return .answered
        } catch let e as ApiError {
            return NotifyActionRules.isDone(status: e.status) ? .answered : .failed
        } catch {
            return .failed
        }
    }

    /// A typed reply from its notification: blank text sends nothing; the turn goes to the server as a normal chat message.
    static func reply(text: String?, timezone: String, api: NotifyApi) async -> Reply {
        guard let clean = NotifyActionRules.cleanReply(text) else { return .blank }
        do {
            try await api.startTurn(text: clean, timezone: timezone)
            return .sent
        } catch {
            return .failed
        }
    }
}
