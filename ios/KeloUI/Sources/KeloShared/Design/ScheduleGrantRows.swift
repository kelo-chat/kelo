import SwiftUI
import KeloCore

// What a schedule may do without asking, allowed once when it was set up (Android agent/ScheduleGrants.kt ScheduleGrantRows): shown
// on the schedule's own page (작업), on the wallet list and on a login's keychain page (마이), so it lives here.

/// "30,000원", "25.00 USD": digits and separators in the app's language (the wallet's own wording).
@MainActor public func keloMoney(_ amount: Double, _ currency: String) -> String {
    let n = Money.number(amount, currency, locale: Strings.shared.locale)
    return currency.uppercased() == "KRW" ? L(R.string.wallet_money_krw, n) : L(R.string.wallet_money, n, currency.uppercased())
}

/// The grants as one bordered list: what each allows, where, the cap, and Revoke (asks first). `showSchedule`: name the schedule
/// (on the wallet and the login pages; the schedule's own page leaves it out). `onRevoke` runs after the owner confirmed.
public struct ScheduleGrantRows: View {
    let grants: [ScheduleGrant]
    let showSchedule: Bool
    let onRevoke: (ScheduleGrant) -> Void
    @Environment(\.kelo) private var c
    @State private var asking: ScheduleGrant?

    public init(_ grants: [ScheduleGrant], showSchedule: Bool, onRevoke: @escaping (ScheduleGrant) -> Void) {
        self.grants = grants; self.showSchedule = showSchedule; self.onRevoke = onRevoke
    }

    public var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(grants.enumerated()), id: \.element.id) { i, g in
                if i > 0 { Hairline() }
                row(g)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        .alert(L(R.string.grant_revoke_confirm), isPresented: Binding(get: { asking != nil }, set: { if !$0 { asking = nil } })) {
            Button(L(R.string.grant_revoke), role: .destructive) {
                if let g = asking { asking = nil; onRevoke(g) }
            }
            Button(L(R.string.cancel), role: .cancel) { asking = nil }
        }
    }

    private func row(_ g: ScheduleGrant) -> some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title(g)).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                if !g.trigger.isBlank { line(L(R.string.task_grant_row_when, g.trigger), muted: false) }
                let sub = subtitle(g)
                if !sub.isBlank { line(sub) }
                if g.isPurchase, let card = g.cardLabel, !card.isBlank {
                    line(L(R.string.task_grant_row_card, card + (g.cardLast4.map { " ••\($0)" } ?? "")))
                }
                if !g.isLogin, let n = g.left {
                    line(L(R.string.task_grant_row_left, n, g.maxCount) + (g.once ? " · " + L(R.string.task_grant_row_once) : ""))
                }
                if g.isPurchase && g.spentThisMonth > 0 { line(L(R.string.grant_spent, keloMoney(g.spentThisMonth, g.currency))) }
                if showSchedule && !g.ownerName.isBlank { line(L(R.string.grant_for_schedule, g.ownerName), muted: false) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            LinkButton(L(R.string.grant_revoke), danger: true) { asking = g }
                .frame(minHeight: 44)
                .layoutPriority(1)
                .accessibilityIdentifier("kelo.grant.revoke.\(g.id)")
        }
        .padding(.leading, 14).padding(.trailing, 8).padding(.vertical, 10)
    }

    private func title(_ g: ScheduleGrant) -> String {
        if g.isPurchase { return L(R.string.grant_purchase_title, g.what) }
        if g.isSend { return L(R.string.task_grant_row_send, g.placesText) }
        if g.isChange { return L(R.string.task_grant_row_change, g.what) }
        return L(R.string.grant_login_title, g.loginName)
    }

    private func subtitle(_ g: ScheduleGrant) -> String {
        if g.isPurchase { return L(R.string.grant_purchase_cap, g.site, g.maxAmountText, g.monthlyCapText) }
        if g.isChange { return g.placesText + (g.allowDelete ? " · " + L(R.string.task_grant_delete_yes) : "") }
        if g.isSend { return g.template }
        return L(R.string.grant_login_site, g.site)
    }

    private func line(_ text: String, muted: Bool = true) -> some View {
        Text(text).font(KeloFont.small).foregroundStyle(muted ? c.muted : c.ink).fixedSize(horizontal: false, vertical: true)
    }
}

/// A task page's "allowed without asking" block (a watcher's or a background job's page; the schedule page has its own): the grants
/// allowed at setup, each with Revoke, and the note that anything else still asks. Nothing shows while there are none.
public struct TaskGrantsBlock: View {
    let key: String
    let load: () async -> [ScheduleGrant]
    let revoke: (ScheduleGrant) async -> Bool
    @Environment(\.kelo) private var c
    @State private var grants: [ScheduleGrant] = []

    public init(key: String, load: @escaping () async -> [ScheduleGrant], revoke: @escaping (ScheduleGrant) async -> Bool) {
        self.key = key; self.load = load; self.revoke = revoke
    }

    public var body: some View {
        Group {
            if !grants.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L(R.string.grant_section_title)).font(KeloFont.titleSmall).foregroundStyle(c.accent)
                    ScheduleGrantRows(grants, showSchedule: false) { g in
                        Task { if await revoke(g) { grants.removeAll { $0.id == g.id } } }
                    }
                    Text(L(R.string.task_grant_section_note)).font(KeloFont.small).foregroundStyle(c.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityIdentifier("kelo.taskGrants")
            } else {
                // a view of no height, so the load below runs (an empty group never appears)
                Color.clear.frame(height: 0)
            }
        }
        .task(id: key) { grants = await load() }
    }
}
