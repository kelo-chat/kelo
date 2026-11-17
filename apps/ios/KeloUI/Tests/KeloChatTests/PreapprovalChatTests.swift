import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// Android chat/ChatCards (order_preapprove summary, forget plan list) and the payment_auto cover note: what the chat items carry.
@Suite struct PreapprovalChatTests {
    private func reduce(_ ev: TurnEvent) -> [ChatItem] {
        var n = 0
        return ChatReducer.apply([], ev, newKey: { n += 1; return "k\(n)" })
    }

    private func event(_ json: String) throws -> TurnEvent {
        TurnEvent.parse(try JSON.parse(json))
    }

    @Test func theOrderPreapproveCardCarriesItsSummary() throws {
        let ev = try event(#"{"type":"approval_request","approval_id":"o1","name":"order_preapprove","preapproval":{"intent":"Two pairs of shoes","items":[{"description":"Shoes","qty":2}],"max_total":150,"currency":"usd","max_total_text":"$150.00","expires_at":1893456000,"owner_kind":"task"}}"#)
        guard case .approval(let a)? = reduce(ev).first else { Issue.record("no approval card"); return }
        #expect(a.preapproval?.intent == "Two pairs of shoes")
        #expect(a.preapproval.map(PaymentCoverRules.capText) == "$150.00")
        #expect(a.preapproval?.forSchedule == false && a.forgetPlan.isEmpty)
    }

    @Test func theForgetCardCarriesItsPlanId() throws {
        let ev = try event(#"{"type":"approval_request","approval_id":"f1","name":"forget_confirm","arguments":{"plan_id":" p-7 "}}"#)
        guard case .approval(let a)? = reduce(ev).first else { Issue.record("no approval card"); return }
        #expect(a.forgetPlan == "p-7" && a.preapproval == nil)
        let other = try event(#"{"type":"approval_request","approval_id":"s1","name":"send","arguments":{"plan_id":"x"}}"#)
        guard case .approval(let b)? = reduce(other).first else { Issue.record("no approval card"); return }
        #expect(b.forgetPlan.isEmpty)
    }

    @Test func thePaymentLineSaysItWasCovered() throws {
        let ev = try event(#"{"type":"payment_auto","id":"1","payment":{"site":"shop.example","amount_text":"$9.99"},"preapproval":{"kind":"schedule_allow","via":"checkout"}}"#)
        guard case .paymentAuto(let p)? = reduce(ev).first else { Issue.record("no payment line"); return }
        #expect(p.cover?.bySchedule == true)
        let plain = try event(#"{"type":"payment_auto","id":"2","payment":{"site":"shop.example"}}"#)
        guard case .paymentAuto(let q)? = reduce(plain).first else { Issue.record("no payment line"); return }
        #expect(q.cover == nil)
    }
}
