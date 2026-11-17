import Foundation
import Testing
@testable import KeloCore

/// The buttons under a phone notification (Android NotifyActionsTest): categories from notify_actions, what may be sent, the outcomes.
@Suite struct NotifyActionsTests {
    @Test func theCategoryFollowsTheServersNotifyActions() {
        #expect(NotifyActionRules.category(offered: ["allow", "deny"]) == "kelo.approval")
        #expect(NotifyActionRules.category(offered: ["deny"]) == "kelo.approval.deny")   // a payment: no Allow button
        #expect(NotifyActionRules.category(offered: []) == nil)                         // needs input in the app: tapping opens it
        #expect(NotifyActionRules.category(offered: ["allow"]) == nil)                  // never an Allow without a Deny
    }

    @Test func aPaymentIsNeverApprovedFromANotification() {
        #expect(!NotifyActionRules.mayAnswer(approve: true, offered: ["deny"]))
        #expect(NotifyActionRules.mayAnswer(approve: false, offered: ["deny"]))
        #expect(NotifyActionRules.mayAnswer(approve: true, offered: ["allow", "deny"]))
        #expect(!NotifyActionRules.mayAnswer(approve: false, offered: []))              // an item that needs input answers nothing here
        #expect(!NotifyActionRules.mayAnswer(approve: true, offered: []))
    }

    @Test func buttonActionsMapToDecisions() {
        #expect(NotifyActionRules.decision(action: "kelo.allow") == true)
        #expect(NotifyActionRules.decision(action: "kelo.deny") == false)
        #expect(NotifyActionRules.decision(action: "com.apple.UNNotificationDefaultActionIdentifier") == nil)
        #expect(NotifyActionRules.decision(action: NotifyActionRules.replyAction) == nil)
    }

    @Test func anAnswerTakesTheNotificationDownOnlyWhenTheServerTookIt() {
        #expect(NotifyActionRules.isDone(status: 200))
        #expect(NotifyActionRules.isDone(status: 204))
        #expect(NotifyActionRules.isDone(status: 404))   // already answered somewhere else
        #expect(!NotifyActionRules.isDone(status: 500))
        #expect(!NotifyActionRules.isDone(status: 423))  // locked: the answer did not go
        #expect(!NotifyActionRules.isDone(status: 0))    // server not reached
    }

    @Test func aBlankReplyIsIgnored() {
        #expect(NotifyActionRules.cleanReply(nil) == nil)
        #expect(NotifyActionRules.cleanReply("  \n\t ") == nil)
        #expect(NotifyActionRules.cleanReply("  ok  ") == "ok")
    }

    @Test func theNotifyActionsOfAPendingItemAreRead() throws {
        let json = try JSON.parse(#"""
            {"items":[{"type":"approval_request","approval_id":"a1","name":"browser_click","label":"Click","asked_at":1,
                       "notify_actions":["allow","deny"]},
                      {"type":"approval_request","approval_id":"a2","name":"confirm_purchase","label":"Buy","asked_at":2,
                       "notify_actions":["deny"]},
                      {"type":"approval_request","approval_id":"a3","name":"browser_login","label":"Sign in","asked_at":3}]}
            """#)
        let items = PendingApproval.parseList(json)
        #expect(items.first { $0.id == "a1" }?.notifyActions == ["allow", "deny"])
        #expect(items.first { $0.id == "a2" }?.notifyActions == ["deny"])
        #expect(items.first { $0.id == "a3" }?.notifyActions == [])   // an older server: no buttons
    }
}
