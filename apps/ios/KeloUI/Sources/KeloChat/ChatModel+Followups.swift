import Foundation
import KeloCore
import KeloShared

// The follow-up buttons under an agent message: loading them and running one.

extension ChatModel {
    public func loadFollowups() {
        Task { [weak self] in await self?.loadFollowupsNow() }
    }

    func loadFollowupsNow() async {
        guard let api = api(), let list = try? await FollowupCalls(api: api).list() else { return }
        followups = FollowupRules.byMessage(list.items)
    }

    public func runAction(_ item: Followup, step: FollowupStep) {
        if acting.contains(item.id) || outcomes[item.id] != nil { return }
        if case .openLogin = step { return }   // the screen opens the sign-in sheet
        guard let api = api() else { return }
        acting.insert(item.id)
        Task { [weak self] in
            let (result, detail): (StepResult, String)
            do { (result, detail) = try await FollowupCalls(api: api).run(step) } catch { (result, detail) = (.failed, "") }
            guard let self else { return }
            self.acting.remove(item.id)
            if let o = ChatReducer.outcome(step, result) { self.outcomes[item.id] = o }
            if result == .failed { self.addError(detail.isEmpty ? .network : .message(detail)) }
            else { await self.loadFollowupsNow() }
        }
    }

    public func actionSignedIn(_ item: Followup) { outcomes[item.id] = .signedIn }
}

extension ChatModel {
    /// The learned message hours on the briefing card: accepted or kept as they are (best effort; the card is refreshed with the
    /// next briefing read).
    func answerHours(_ h: BriefingHours, accept: Bool) {
        guard let api = api() else { return }
        Task { _ = try? await api.request("POST", accept ? ContactHoursRules.acceptPath(h.id) : ContactHoursRules.keepPath(h.id)) }
    }
}
