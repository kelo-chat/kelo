import Foundation
import Testing
@testable import KeloCore

@Suite struct FollowupRulesTests {
    func a(_ kind: String, name: String = "", approval: String = "", worker: String = "", fid: String = "", minutes: Int? = nil) -> FollowupAction {
        FollowupAction(kind: kind, name: name, approvalId: approval, workerId: worker, followupId: fid, minutes: minutes)
    }

    @Test func eachActionKindMapsToItsStep() {
        #expect(FollowupRules.step(a("open_login", name: "login-naver")) == .openLogin(name: "login-naver"))
        #expect(FollowupRules.step(a("approve", approval: "ap1")) == .answer(approvalId: "ap1", approve: true))
        #expect(FollowupRules.step(a("deny", approval: "ap1")) == .answer(approvalId: "ap1", approve: false))
        #expect(FollowupRules.step(a("resume", worker: "w9", fid: "4")) == .resumeWorker(workerId: "w9"))
        #expect(FollowupRules.step(a("resume", fid: "4")) == nil)   // resume is the job's own call
        #expect(FollowupRules.step(a("drop", fid: "4")) == .drop(followupId: "4"))
        #expect(FollowupRules.step(a("snooze", fid: "4", minutes: 30)) == .snooze(followupId: "4", minutes: 30))
        #expect(FollowupRules.step(a("snooze", fid: "4")) == .snooze(followupId: "4", minutes: FollowupRules.defaultSnoozeMinutes))
    }

    @Test func actionsMissingWhatTheyNeedOrUnknownShowNoButton() {
        #expect(FollowupRules.step(a("open_login")) == nil)
        #expect(FollowupRules.step(a("approve")) == nil)
        #expect(FollowupRules.step(a("drop")) == nil)
        #expect(FollowupRules.step(a("resume")) == nil)
        #expect(FollowupRules.step(a("launch_rocket", fid: "1")) == nil)
        #expect(FollowupRules.step(a("drop"), ownId: "7") == .drop(followupId: "7"))
    }

    @Test func buttonsAreUniqueAndCapped() {
        let list = [a("drop", fid: "1"), a("drop", fid: "1"), a("snooze", fid: "1", minutes: 10), a("snooze", fid: "1", minutes: 60),
                    a("resume", worker: "w"), a("approve", approval: "x"), a("nope")]
        let b = FollowupRules.buttons(list).map(\.step)
        #expect(b.count == FollowupRules.maxButtons)
        #expect(Set(b).count == b.count)
    }

    @Test func onHoldCardsShowExactlyWhatTheServerSent() {
        let f = Followup(id: "5", kind: "worker", title: "도서관 책 연장", stage: Followup.onHoldStage, nextAt: nil, createdAt: 1,
                         actions: [a("resume", worker: "w1"), a("snooze", fid: "5", minutes: 60), a("drop", fid: "5")])
        #expect(FollowupRules.holdButtons(f).map(\.step) == [.resumeWorker(workerId: "w1"), .snooze(followupId: "5", minutes: 60), .drop(followupId: "5")])
        var g = f; g.actions = [a("drop")]
        #expect(FollowupRules.holdButtons(g).map(\.step) == [.drop(followupId: "5")])
    }

    @Test func onHoldListAndDelays() {
        let items = [
            Followup(id: "1", kind: "reply", title: "", stage: Followup.first, nextAt: nil, createdAt: 5, actions: []),
            Followup(id: "2", kind: "reply", title: "", stage: Followup.onHoldStage, nextAt: nil, createdAt: 9, actions: []),
            Followup(id: "3", kind: "reply", title: "", stage: Followup.onHoldStage, nextAt: nil, createdAt: 2, actions: []),
            Followup(id: "4", kind: "reply", title: "", stage: Followup.onHoldStage, nextAt: nil, createdAt: 1, actions: [], status: Followup.statusDropped),
        ]
        #expect(FollowupRules.onHold(items).map(\.id) == ["3", "2"])
        #expect(FollowupRules.delays(first: 30, second: 10) == (30, 30))
        #expect(FollowupRules.delays(first: 30, second: 180) == (30, 180))
        #expect(FollowupRules.delays(first: 0, second: 5) == (1, 5))
        #expect(FollowupRules.choices([10, 15, 30, 60], current: 45) == [10, 15, 30, 45, 60])
    }

    @Test func serverCallsPerStep() {
        #expect(FollowupRules.call(.openLogin(name: "x")) == nil)
        #expect(FollowupRules.call(.answer(approvalId: "a b", approve: true))?.path == "/api/approvals/a%20b")
        #expect(FollowupRules.call(.answer(approvalId: "a", approve: false))?.body?.compactString == #"{"approve":false}"#)
        #expect(FollowupRules.call(.resumeWorker(workerId: "w1"))?.path == "/api/workers/w1/resume")
        #expect(FollowupRules.call(.retry(followupId: "3"))?.path == "/api/followups/3/retry")
        #expect(FollowupRules.call(.drop(followupId: "3"))?.path == "/api/followups/3/drop")
        let snooze = FollowupRules.call(.snooze(followupId: "3", minutes: 45))
        #expect(snooze?.path == "/api/followups/3/snooze" && snooze?.body?.compactString == #"{"minutes":45}"#)
        #expect(FollowupRules.gone(404) && FollowupRules.gone(409) && FollowupRules.gone(410) && !FollowupRules.gone(500))
    }

    @Test func listsAndActionsParseLeniently() throws {
        let list = Followup.parseList(try JSON.parse(
            #"{"items":[{"id":12,"kind":"login","title":"네이버 로그인","stage":"on_hold","next_at":null,"created_at":100.5,"message_id":88,"status":"open","actions":[{"kind":"open_login","name":"login-naver"},{"kind":"snooze","followup_id":12,"minutes":60},{"bad":1}]},{"kind":"x"}]}"#))
        #expect(list.count == 1)
        let f = list[0]
        #expect(f.id == "12" && f.onHold && f.actions.count == 2)
        #expect(f.actions[1].followupId == "12" && f.actions[1].minutes == 60)
        #expect(f.messageId == 88 && f.open && f.createdAt == 100.5)
        #expect(Followup.parseList(try JSON.parse(#"[{"id":"a"}]"#)).count == 1)
    }

    @Test func eachMessageGetsItsFollowupOpenBeforeClosed() {
        let items = [
            Followup(id: "1", kind: "login", title: "", stage: Followup.first, nextAt: nil, createdAt: 0, actions: [], messageId: 10, status: Followup.statusResolved),
            Followup(id: "2", kind: "login", title: "", stage: Followup.second, nextAt: nil, createdAt: 0, actions: [a("drop", fid: "2")], messageId: 10),
            Followup(id: "3", kind: "reply", title: "", stage: Followup.first, nextAt: nil, createdAt: 0, actions: [], messageId: 11, status: Followup.statusDropped),
            Followup(id: "4", kind: "reply", title: "", stage: Followup.first, nextAt: nil, createdAt: 0, actions: []),
        ]
        let map = FollowupRules.byMessage(items)
        #expect(Set(map.keys) == [10, 11])
        #expect(map[10]?.id == "2" && map[11]?.id == "3")
    }

    @Test func settingsUseTheServersNames() throws {
        let st = try JSON.decode(FollowupSettings.self, from: try JSON.parse(#"{"enabled":false,"first_delay_min":15,"second_delay_min":240,"awake":true}"#))
        #expect(!st.enabled && st.firstMinutes == 15 && st.secondMinutes == 240 && st.awake == true)
    }

    @Test func anExpiredApprovalOffersRetryAndShowsInAsks() throws {
        let f = try #require(Followup.parseList(try JSON.parse(
            #"{"items":[{"id":12,"kind":"approval_expired","title":"장보기 주문","stage":"first","origin":"schedule","schedule_id":4,"created_at":1,"actions":[{"kind":"retry","followup_id":12,"label":"다시 시도"},{"kind":"snooze","followup_id":12,"minutes":60},{"kind":"drop","followup_id":12}]}]}"#)).first)
        #expect(f.origin == "schedule" && f.scheduleId == 4)
        #expect(FollowupRules.onHold([f]) == [f])
        #expect(FollowupRules.holdButtons(f).map(\.step) == [.retry(followupId: "12"), .snooze(followupId: "12", minutes: 60), .drop(followupId: "12")])
        #expect(f.actions[0].label == "다시 시도")
        var g = f; g.actions = [FollowupAction(kind: "drop", followupId: "12")]
        #expect(FollowupRules.holdButtons(g).map(\.step) == [.drop(followupId: "12")])
    }

    @Test func retryResults() async throws {
        let t = FakeTransport(.ok(200, #"{"ok":true,"how":"worker_resumed"}"#), .ok(404, #"{"detail":"no"}"#),
                              .ok(409, #"{"detail":"이 작업은 다시 시작할 수 없어요"}"#), .ok(409, #"{"detail":"answered"}"#), .fail(URLError(.notConnectedToInternet)))
        let calls = FollowupCalls(api: KeloApi(baseUrl: "https://k", token: "t", transport: t))
        #expect(try await calls.run(.retry(followupId: "12")).0 == .done)
        #expect(try await calls.run(.retry(followupId: "12")).0 == .gone)
        let r = try await calls.run(.retry(followupId: "12"))
        #expect(r.0 == .failed && r.1 == "이 작업은 다시 시작할 수 없어요")
        #expect(try await calls.run(.answer(approvalId: "a", approve: true)).0 == .gone)   // settled elsewhere
        let offline = try await calls.run(.drop(followupId: "1"))
        #expect(offline.0 == .failed && offline.1 == "")
        #expect(try await calls.run(.openLogin(name: "x")).0 == .done)   // nothing sent
        #expect(t.requests.value.count == 5)
    }

    @Test func aServerWithoutFollowupsShowsNone() async throws {
        let t = FakeTransport(.ok(404, #"{"detail":"Not Found"}"#), .ok(404, "{}"))
        let calls = FollowupCalls(api: KeloApi(baseUrl: "https://k", token: "t", transport: t))
        #expect(try await calls.list() == FollowupList(items: [], available: false))
        #expect(try await calls.settings() == nil)
    }
}
