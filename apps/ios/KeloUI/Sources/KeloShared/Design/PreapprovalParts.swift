import SwiftUI
import KeloCore

/// The everyday category in the user's words ("Memory", "Notes"…); a category this app does not know reads as "Other"
/// (Android agent/ForgetParts.kt).
@MainActor public func forgetCategoryWord(_ category: String) -> String {
    switch category {
    case "memory": L(R.string.forget_cat_memory)
    case "notes": L(R.string.forget_cat_notes)
    case "reminders": L(R.string.forget_cat_reminders)
    case "skills": L(R.string.forget_cat_skills)
    case "history": L(R.string.forget_cat_history)
    default: L(R.string.forget_cat_other)
    }
}

/// Where the schedule or task an order was approved for is named: "For the schedule “Weekly shop”", or "For one task".
@MainActor public func preapprovalFor(schedule: Bool, name: String) -> String {
    if !schedule { return L(R.string.paycover_for_task) }
    let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
    return n.isEmpty ? L(R.string.paycover_for_a_schedule) : L(R.string.paycover_for_schedule, TokenText.fit(n, max: 30))
}

/// The approval card of `order_preapprove`, in words: what is approved, the most it may cost all in, until when, and that it covers
/// one order only; the must-haves, delivery, shops and what Kelo may decide when the order has them. Nothing is paid by this card
/// (Android agent/PaymentCoverParts.kt PreapprovalFacts).
public struct PreapprovalFacts: View {
    let p: PreapprovalAsk
    @Environment(\.kelo) private var c

    public init(_ p: PreapprovalAsk) { self.p = p }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            fact(L(R.string.preapprove_what), PaymentCoverRules.whatLines(p).map { TokenText.fit($0, max: 40) })
            fact(L(R.string.preapprove_cap), [L(R.string.preapprove_cap_value, PaymentCoverRules.capText(p))], strong: true)
            if p.expiresAt > 0 { fact(L(R.string.preapprove_until), [MyFormat.dayTime(Double(p.expiresAt))]) }
            fact(L(R.string.preapprove_covers), [L(R.string.preapprove_covers_value), preapprovalFor(schedule: p.forSchedule, name: "")])
            if !p.requirements.isEmpty { fact(L(R.string.preapprove_requirements), p.requirements.map { TokenText.fit($0, max: 40) }) }
            if !p.shipping.isEmpty { fact(L(R.string.preapprove_shipping), [TokenText.fit(p.shipping, max: 40)]) }
            if !p.merchantPolicy.isEmpty { fact(L(R.string.preapprove_shops), [TokenText.fit(p.merchantPolicy, max: 40)]) }
            if !p.delegatedChoices.isEmpty { fact(L(R.string.preapprove_delegated), p.delegatedChoices.map { TokenText.fit($0, max: 40) }) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
        .accessibilityIdentifier("kelo.preapproval")
    }

    private func fact(_ label: String, _ lines: [String], strong: Bool = false) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(label).font(KeloFont.small).foregroundStyle(c.muted).frame(minWidth: 56, maxWidth: 100, alignment: .leading)
                values(lines, strong: strong)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                values(lines, strong: strong)
            }
        }
    }

    private func values(_ lines: [String], strong: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, l in
                Text(l).font(strong ? KeloFont.small.weight(.semibold) : KeloFont.small).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Under the forget card (tool `forget_confirm`): "See the list (N)", each place as its category and a preview of at most 60
/// characters. Shown only when the server still has the plan; anything else leaves the card as it is.
public struct ForgetPlanList: View {
    let planId: String
    @Environment(\.keloApi) private var api
    @State private var lines: [String] = []

    public init(planId: String) { self.planId = planId }

    public var body: some View {
        VStack(spacing: 0) {
            Color.clear.frame(height: 0)   // always something for the task to hang on, also while the list is empty
            if !lines.isEmpty { ApprovalItems(items: lines) }
        }
            .task(id: planId) {
                guard let api, let plan = try? await api.forgetPlan(planId) else { return }
                lines = ForgottenRules.lines(plan) { forgetCategoryWord($0) }
            }
    }
}
