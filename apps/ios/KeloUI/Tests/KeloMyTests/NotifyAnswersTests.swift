import Foundation
import Testing
import KeloCore
@testable import KeloMy

/// Allow / Deny / Reply from a notification, through a fake API (Android ApprovalActionReceiverTest / ReplyActionReceiverTest).
@MainActor @Suite struct NotifyAnswersTests {
    final class FakeNotifyApi: NotifyApi {
        var items: [PendingApproval] = []
        var pendingError: Error?
        var answerError: Error?
        var startError: Error?
        var answers: [(id: String, approve: Bool)] = []
        var turns: [String] = []
        func pendingApprovals() async throws -> [PendingApproval] {
            if let pendingError { throw pendingError }
            return items
        }
        func answerApproval(_ id: String, approve: Bool) async throws {
            answers.append((id, approve))
            if let answerError { throw answerError }
        }
        func startTurn(text: String, timezone: String) async throws {
            turns.append(text)
            if let startError { throw startError }
        }
    }

    private func item(_ id: String, _ actions: [String]) throws -> PendingApproval {
        let list = try JSON.parse(#"{"items":[{"type":"approval_request","approval_id":"\#(id)","name":"browser_click","label":"x","asked_at":1,"notify_actions":\#(try actionsJSON(actions))}]}"#)
        return try #require(PendingApproval.parseList(list).first)
    }

    private func actionsJSON(_ a: [String]) throws -> String {
        String(decoding: try JSONSerialization.data(withJSONObject: a), as: UTF8.self)
    }

    @Test func anAllowIsSentWhenTheItemOffersIt() async throws {
        let api = FakeNotifyApi()
        api.items = [try item("a1", ["allow", "deny"])]
        #expect(await NotifyAnswers.approve(id: "a1", approve: true, api: api) == .answered)
        #expect(api.answers.count == 1 && api.answers[0].id == "a1" && api.answers[0].approve)
    }

    @Test func aPaymentAllowIsNeverSentAndNothingIsAnswered() async throws {
        let api = FakeNotifyApi()
        api.items = [try item("pay1", ["deny"])]
        #expect(await NotifyAnswers.approve(id: "pay1", approve: true, api: api) == .refused)
        #expect(api.answers.isEmpty)
        #expect(await NotifyAnswers.approve(id: "pay1", approve: false, api: api) == .answered)   // its Deny is fine
        #expect(api.answers.count == 1 && !api.answers[0].approve)
    }

    @Test func anItemThatNeedsInputGetsNoAnswerFromHere() async throws {
        let api = FakeNotifyApi()
        api.items = [try item("pin1", [])]
        #expect(await NotifyAnswers.approve(id: "pin1", approve: false, api: api) == .refused)
        #expect(api.answers.isEmpty)
    }

    @Test func anItemThatNoLongerWaitsIsGoneAndNothingIsSent() async throws {
        let api = FakeNotifyApi()
        api.items = [try item("other", ["allow", "deny"])]
        #expect(await NotifyAnswers.approve(id: "a1", approve: true, api: api) == .gone)
        #expect(api.answers.isEmpty)
    }

    @Test func theServersOutcomesDecideTheNotification() async throws {
        let api = FakeNotifyApi()
        api.items = [try item("a1", ["allow", "deny"])]
        api.answerError = ApiError(status: 404, detail: "gone")
        #expect(await NotifyAnswers.approve(id: "a1", approve: true, api: api) == .answered)
        api.answerError = ApiError(status: 500, detail: "boom")
        #expect(await NotifyAnswers.approve(id: "a1", approve: true, api: api) == .failed)
        api.answerError = ApiError(status: 0, detail: "offline")
        #expect(await NotifyAnswers.approve(id: "a1", approve: false, api: api) == .failed)
    }

    @Test func aFailedAskSendsNothing() async throws {
        let api = FakeNotifyApi()
        api.pendingError = ApiError(status: 0, detail: "offline")
        #expect(await NotifyAnswers.approve(id: "a1", approve: true, api: api) == .failed)
        #expect(api.answers.isEmpty)
    }

    @Test func aBlankReplyIsIgnored() async throws {
        let api = FakeNotifyApi()
        #expect(await NotifyAnswers.reply(text: "   ", timezone: "Asia/Seoul", api: api) == .blank)
        #expect(await NotifyAnswers.reply(text: nil, timezone: "Asia/Seoul", api: api) == .blank)
        #expect(api.turns.isEmpty)
    }

    @Test func aReplyIsSentAsATrimmedTurn() async throws {
        let api = FakeNotifyApi()
        #expect(await NotifyAnswers.reply(text: " 내일 날씨 ", timezone: "Asia/Seoul", api: api) == .sent)
        #expect(api.turns == ["내일 날씨"])
        api.startError = ApiError(status: 0, detail: "offline")
        #expect(await NotifyAnswers.reply(text: "again", timezone: "Asia/Seoul", api: api) == .failed)
    }

    @Test func theCategoryComesFromTheItemsOffers() {
        #expect(NotifyActionRules.category(offered: ["allow", "deny"]) == NotifyActionRules.approvalCategory)
        #expect(NotifyActionRules.category(offered: ["deny"]) == NotifyActionRules.denyOnlyCategory)
        #expect(NotifyActionRules.category(offered: []) == nil)
    }

    @Test func aPushCarriesTheItemAsItsApprovalId() {
        #expect(NotifyRules.approvalItem(userInfo: ["approval_id": "a9"]) == "a9")
        #expect(NotifyRules.approvalItem(userInfo: ["item": "a8", "approval_id": "a9"]) == "a8")
        #expect(NotifyRules.approvalItem(userInfo: ["approval_id": "  "]) == nil)
        #expect(NotifyRules.approvalItem(userInfo: ["type": "task_done"]) == nil)
    }
}
