import Foundation
import Testing
@testable import KeloCore

/// Port of android chat/HandoverTest.kt: a human check handed to the user (the card's data, the live view taking the tab over) and
/// approval titles. Its ChatAsks folding has no iPhone counterpart yet (the chat shows no pending cards).
@Suite struct HandoverTests {
    private let list = """
        {"items":[{"type":"approval_request","kind":"human_check","approval_id":"h1","name":"browser_check","label":"Human check",
           "ask":"shop.example.com wants to confirm a person is there","site":"shop.example.com","tab":"task:ab12","asked_at":10,
           "unattended":true,"source":"worker","worker_id":"w1","title":"Find tickets","check":{"vendor":"cloudflare"}},
           {"type":"approval_request","approval_id":"a1","name":"browser_login","label":"Signing in","ask":"Sign in?","asked_at":5}],
           "resolved":[{"approval_id":"h0","kind":"human_check","outcome":"solved"},{"approval_id":"","outcome":"x"}]}
        """

    @Test func aHumanCheckIsReadFromThePendingList() throws {
        let json = try JSON.parse(list)
        let items = PendingApproval.parseList(json)
        let h = try #require(items.first { $0.id == "h1" })
        #expect(h.isHumanCheck && h.tab == "task:ab12" && h.site == "shop.example.com" && h.unattended)
        let a = try #require(items.first { $0.id == "a1" })
        #expect(!a.isHumanCheck && a.tab == "owner" && a.event.ask == "Sign in?")
        #expect(PendingApproval.parseResolved(json) == ["h0": "solved"])
        #expect(PendingApproval.parseResolved(try JSON.parse(#"{"items":[]}"#)).isEmpty)   // an older server
    }

    /// Android HandoverTest.aSignInVerificationIsToldApartFromAPersonCheck.
    @Test func aSignInVerificationIsToldApartFromAPersonCheck() throws {
        let items = PendingApproval.parseList(try JSON.parse("""
            {"items":[{"type":"approval_request","kind":"human_check","approval_id":"v1","name":"browser_check","site":"accounts.kakao.com",
               "tab":"owner","check":{"vendor":"kakao","name":"휴대폰에서 로그인을 승인해 주세요","kind":"verification"}},
               {"type":"approval_request","kind":"human_check","approval_id":"h1","name":"browser_check","site":"shop.example.com",
               "check":{"vendor":"cloudflare","name":"Cloudflare","kind":"interstitial"}},
               {"type":"approval_request","kind":"human_check","approval_id":"h2","name":"browser_check"}]}
            """))
        let v = try #require(items.first { $0.id == "v1" })
        #expect(v.isHumanCheck && v.isVerification)
        #expect(v.event.check?.name == "휴대폰에서 로그인을 승인해 주세요")
        #expect(items.first { $0.id == "h1" }?.isVerification == false)
        #expect(items.first { $0.id == "h2" }?.isVerification == false)   // an older server sends no check
        // a plain approval with a "check" object is still no human check
        let a = PendingApproval.parseList(try JSON.parse(
            #"{"items":[{"type":"approval_request","approval_id":"a1","name":"browser_login","check":{"kind":"verification"}}]}"#))
        #expect(a.count == 1 && a.first?.isVerification == false)
        // the stream's human_check event carries it too
        let ev = TurnEvent.parse(try JSON.parse("""
            {"type":"human_check","approval_id":"v1","state":"waiting","site":"accounts.kakao.com","tab":"owner",
             "check":{"name":"로그인 인증 코드를 화면에 입력해 주세요","kind":"verification"}}
            """))
        guard case .humanCheck(let hc) = ev else { Issue.record("not a human check"); return }
        #expect(hc.check?.isVerification == true)
    }

    @Test func aSignInVerificationApproveStepIsToldApart() throws {
        let check = CheckInfo.parse(try JSON.parse(#"{"kind":"verification","step":"approve"}"#))
        #expect(check?.isApproveStep == true)
        let codeCheck = CheckInfo.parse(try JSON.parse(#"{"kind":"verification","step":"code"}"#))
        #expect(codeCheck?.isApproveStep == false)
        let otherCheck = CheckInfo.parse(try JSON.parse(#"{"kind":"","step":"approve"}"#))
        #expect(otherCheck?.isApproveStep == false)
    }

    @Test func theStreamsHandOverEventIsKnown() throws {
        let ev = TurnEvent.parse(try JSON.parse(#"{"type":"human_check","approval_id":"h1","state":"solved","site":"s.example","tab":"owner"}"#))
        #expect(ev == .humanCheck(.init(approvalId: "h1", state: "solved", site: "s.example", tab: "owner")))
    }

    @Test func theLiveViewTakesTheTabOverOnceWhenAskedTo() {
        #expect(LiveRules.autoTake(asked: true, done: false, hasFrame: true, controlling: false, someoneElse: false, busy: false))
        #expect(!LiveRules.autoTake(asked: true, done: true, hasFrame: true, controlling: false, someoneElse: false, busy: false))    // once only
        #expect(!LiveRules.autoTake(asked: true, done: false, hasFrame: false, controlling: false, someoneElse: false, busy: false))  // no picture yet
        #expect(!LiveRules.autoTake(asked: true, done: false, hasFrame: true, controlling: false, someoneElse: true, busy: false))    // another phone holds it
        #expect(!LiveRules.autoTake(asked: true, done: false, hasFrame: true, controlling: true, someoneElse: false, busy: false))    // already held here
        #expect(!LiveRules.autoTake(asked: true, done: false, hasFrame: true, controlling: false, someoneElse: false, busy: true))    // a take-over on its way
        #expect(!LiveRules.autoTake(asked: false, done: false, hasFrame: true, controlling: false, someoneElse: false, busy: false))
    }

    @Test func anApprovalTitleIsTheServersQuestionWhenItSendsOne() throws {
        #expect(TurnEvent.ApprovalRequest.question(" 로그인할까요? ") == "로그인할까요?")
        #expect(TurnEvent.ApprovalRequest.question("") == nil)
        #expect(TurnEvent.ApprovalRequest.question("  ") == nil)
        guard case .approvalRequest(let ev) = TurnEvent.parse(try JSON.parse(
            #"{"type":"approval_request","approval_id":"a","name":"browser_click","ask":"버튼을 누를까요?"}"#)) else {
            Issue.record("not an approval"); return
        }
        #expect(ev.ask == "버튼을 누를까요?" && ev.question == "버튼을 누를까요?")
        guard case .approvalRequest(let old) = TurnEvent.parse(try JSON.parse(#"{"type":"approval_request","approval_id":"a","name":"browser_click"}"#)) else {
            Issue.record("not an approval"); return
        }
        #expect(old.ask == "" && old.kind == "" && old.tab == "" && old.question == nil)
    }
}
