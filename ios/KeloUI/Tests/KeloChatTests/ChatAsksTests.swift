import Testing
import Foundation
import KeloCore
@testable import KeloChat

private func pa(_ id: String, at: Double = 0, source: String = "", worker: String? = nil, unattended: Bool = false, kind: String = "",
                canRemember: Bool = false, schedule: Bool = false) -> PendingApproval {
    PendingApproval(event: TurnEvent.ApprovalRequest(approvalId: id, name: "n", detail: nil, label: "", payment: nil, card: nil,
                                                     canRememberSchedule: schedule, lines: [], kind: kind),
                    site: "s.example", canRemember: canRemember, askedAt: at, unattended: unattended, canRememberSchedule: schedule,
                    source: source, workerId: worker)
}

@Suite struct ChatAsksTests {
    @Test func origin() {
        #expect(ChatAsks.origin(pa("1", worker: "w")) == .job)
        #expect(ChatAsks.origin(pa("1", source: "worker")) == .job)
        #expect(ChatAsks.origin(pa("1", source: "schedule:4")) == .schedule)
        #expect(ChatAsks.origin(pa("1", unattended: true)) == .auto)
        #expect(ChatAsks.origin(pa("1", source: "chat")) == .chat)
    }

    @Test func mergeJoinsLeavesAndFolds() {
        var asks = ChatAsks.merge([], pending: [pa("b", at: 2), pa("a", at: 1)], newestKey: "k1")
        #expect(asks.map(\.id) == ["a", "b"])   // oldest first
        // "a" answered elsewhere: gone; "b" answered here: its line stays while the chat has not moved on
        asks = ChatAsks.ended(asks, "b", .allowed, newestKey: "k1")
        asks = ChatAsks.merge(asks, pending: [], newestKey: "k1")
        #expect(asks.map(\.id) == ["b"])
        #expect(ChatAsks.merge(asks, pending: [], newestKey: "k2").isEmpty)
        // one being answered stays until that settles
        let working = ChatAsks.working(ChatAsks.merge([], pending: [pa("c")], newestKey: nil), "c", on: true)
        #expect(ChatAsks.merge(working, pending: [], newestKey: nil).count == 1)
    }

    @Test func handOverFoldsToItsOutcome() {
        let asks = ChatAsks.merge([], pending: [pa("h", kind: PendingApproval.humanCheck)], newestKey: "k")
        let folded = ChatAsks.merge(asks, pending: [], newestKey: "k", resolved: ["h": "solved"])
        #expect(folded.first?.end == .solved)
        #expect(ChatAsks.resolvedEnd("progressed") == .progressed)
        #expect(ChatAsks.resolvedEnd("skipped") == .skipped)
        #expect(ChatAsks.resolvedEnd("expired") == .gone)
    }

    @Test func visibleSkipsTheFollowedReplysOwnCard() {
        let asks = ChatAsks.merge([], pending: [pa("x"), pa("y")], newestKey: nil)
        let items: [ChatItem] = [.approval(.init(key: "k1", id: "x", name: "n", detail: nil))]
        #expect(ChatAsks.visible(asks, items: items).map(\.id) == ["y"])
    }

    @Test func checkInfoApproveStep() throws {
        let check = CheckInfo.parse(try JSON.parse(#"{"kind":"verification","step":"approve"}"#))
        #expect(check?.isApproveStep == true)
        let codeCheck = CheckInfo.parse(try JSON.parse(#"{"kind":"verification","step":"code"}"#))
        #expect(codeCheck?.isApproveStep == false)
        let nonVerifyCheck = CheckInfo.parse(try JSON.parse(#"{"kind":"","step":"approve"}"#))
        #expect(nonVerifyCheck?.isApproveStep == false)
    }

    @Test func answerEnds() {
        let h = pa("h", kind: PendingApproval.humanCheck)
        #expect(ChatAsks.answerEnd(h, approve: false, rememberSite: false, rememberSchedule: false) == .skipped)
        #expect(ChatAsks.answerEnd(h, approve: true, rememberSite: false, rememberSchedule: false) == .solved)
        let v = PendingApproval(
            event: TurnEvent.ApprovalRequest(
                approvalId: "v", name: "n", detail: nil, label: "", payment: nil, card: nil,
                canRememberSchedule: false, lines: [], kind: PendingApproval.humanCheck,
                check: CheckInfo(name: "Approve sign-in", kind: "verification", step: "approve")
            ),
            site: "s.example"
        )
        #expect(ChatAsks.answerEnd(v, approve: true, rememberSite: false, rememberSchedule: false) == .progressed)
        #expect(ChatAsks.answerEnd(pa("a", canRemember: true), approve: true, rememberSite: true, rememberSchedule: false) == .always)
        #expect(ChatAsks.answerEnd(pa("a"), approve: true, rememberSite: true, rememberSchedule: false) == .allowed)
        #expect(ChatAsks.answerEnd(pa("a", schedule: true), approve: true, rememberSite: false, rememberSchedule: true) == .keptForSchedule)
        #expect(ChatAsks.answerEnd(pa("a"), approve: false, rememberSite: false, rememberSchedule: false) == .denied)
    }

    @Test func skipFetchAndNotifyAsk() {
        #expect(ChatAsks.skipFetch(nil, []) == false)
        #expect(ChatAsks.skipFetch(AgentStatus(pendingApprovals: 0), []) == true)
        #expect(ChatAsks.skipFetch(AgentStatus(pendingApprovals: 1), []) == false)
        #expect(NotifyAsk.shouldAsk(askedBefore: false, enabled: false))
        #expect(!NotifyAsk.shouldAsk(askedBefore: true, enabled: false))
        #expect(!NotifyAsk.shouldAsk(askedBefore: false, enabled: true))
    }
}
