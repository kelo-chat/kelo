import SwiftUI
import KeloCore

// The setup card of a "when X happens, do Y" task (Android agent/TaskGrantCard.kt), its urgent line, and the "see the list" of a
// permission for many items (Android chat/ChatScreen.kt ApprovalItems): the same in the chat and on the Work page, so they live here.

/// The setup card: the question, the trigger and the action, then one block per thing allowed (purchase, message, change, login) with
/// its limits, the card's own auto-pay limit next to the amount asked for, "ends by itself" for a one-shot task, and when time matters
/// what a late answer would miss. Deny / Allow while it is open; `trailing` is the outcome chip once answered.
public struct TaskGrantApproval<Trailing: View>: View {
    let grant: TaskGrantAsk
    let open: Bool
    let trailing: Trailing
    let onAnswer: (Bool) -> Void
    @Environment(\.kelo) private var c

    public init(grant: TaskGrantAsk, open: Bool, @ViewBuilder trailing: () -> Trailing = { EmptyView() }, onAnswer: @escaping (Bool) -> Void) {
        self.grant = grant; self.open = open; self.trailing = trailing(); self.onAnswer = onAnswer
    }

    public var body: some View {
        let g = grant
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 8) {
                Text(L(R.string.task_grant_question)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                trailing
            }
            VStack(alignment: .leading, spacing: 12) {
                let head = [g.taskName, g.taskWhen].filter { !$0.isBlank }.joined(separator: " · ")
                if !g.taskName.isBlank { small(head, muted: true) }
                part(.bell, L(R.string.task_grant_when)) { body(g.trigger) }
                part(.spark, L(R.string.task_grant_does)) { body(g.action, bold: true) }
                if let p = g.purchase {
                    Hairline()
                    part(.package, L(R.string.task_grant_buy)) {
                        body(p.what)
                        if !p.site.isBlank { small(p.site, muted: true) }
                    }
                    part(.receipt, L(R.string.task_grant_limits)) {
                        body(L(R.string.task_grant_per_time, p.maxAmountText), bold: true)
                        small(timesText(p.maxCount))
                        if !p.monthlyCapText.isBlank { small(L(R.string.task_grant_monthly, p.monthlyCapText), muted: true) }
                    }
                    if let card = p.card {
                        part(.card, L(R.string.task_grant_card)) {
                            body([card.label, card.last4.isBlank ? "" : "••" + card.last4].filter { !$0.isEmpty }.joined(separator: " "))
                            let line: String = switch TaskGrantRules.cardLine(p) {
                            case .autoLimit: L(R.string.task_grant_card_limit, card.autoLimitText, p.maxAmountText)
                            case .notAuto: L(R.string.task_grant_card_not_auto, p.maxAmountText)
                            case .phoneOnly: L(R.string.task_grant_card_phone_only)
                            case .none: ""
                            }
                            if !line.isEmpty {
                                Text(line).font(KeloFont.small)
                                    .foregroundStyle(TaskGrantRules.aboveCardLimit(p) || card.phoneOnly ? c.warn : c.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    if !p.shipping.isBlank { part(.mapPin, L(R.string.task_grant_ship_to)) { small(p.shipping) } }
                }
                if let s = g.send {
                    Hairline()
                    part(.send, L(R.string.task_grant_send_to)) {
                        body(TaskGrantRules.recipients(s))
                        small(timesText(s.maxCount), muted: true)
                    }
                    if !s.template.isBlank {
                        part(.chat, L(R.string.task_grant_message)) {
                            body(s.template)
                            if !s.varies.isBlank { small(L(R.string.task_grant_varies, s.varies), muted: true) }
                        }
                    }
                }
                if let ch = g.change {
                    Hairline()
                    part(.edit, L(R.string.task_grant_change)) {
                        body(ch.what)
                        small(TaskGrantRules.places(ch), muted: true)
                        small(timesText(ch.maxCount), muted: true)
                        Text(L(ch.allowDelete ? R.string.task_grant_delete_yes : R.string.task_grant_delete_no)).font(KeloFont.small)
                            .foregroundStyle(ch.allowDelete ? c.warn : c.muted).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if let l = g.login {
                    Hairline()
                    part(.key, L(R.string.task_grant_login)) { body(L(R.string.task_grant_login_line, l.label, l.site)) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
            if g.once { small(L(R.string.task_grant_once), muted: true) }
            small(L(R.string.task_grant_after), muted: true)
            if g.urgent { UrgentLine(missed: g.missed) }
            if open {
                HStack(spacing: 10) {
                    SecondaryButton(L(R.string.chat_approval_deny), fill: true) { onAnswer(false) }
                    PrimaryButton(L(R.string.chat_approval_allow)) { onAnswer(true) }
                }
            }
        }
    }

    private func timesText(_ n: Int) -> String {
        n == 1 ? L(R.string.task_grant_times_one) : n > 1 ? L(R.string.task_grant_times, n) : L(R.string.task_grant_no_count)
    }

    private func body(_ text: String, bold: Bool = false) -> some View {
        Text(text).font(bold ? KeloFont.body.weight(.semibold) : KeloFont.body).foregroundStyle(c.ink)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func small(_ text: String, muted: Bool = false) -> some View {
        Text(text).font(KeloFont.small).foregroundStyle(muted ? c.muted : c.ink).fixedSize(horizontal: false, vertical: true)
    }

    private func part<Content: View>(_ icon: KeloIcon, _ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                KeloIconView(icon, size: 16, tint: c.muted)
                Text(title).font(KeloFont.label).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
            }
            VStack(alignment: .leading, spacing: 2) { content() }.padding(.leading, 22)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// "급해요 · 답이 늦으면 자리가 다른 사람에게 가요": a card of a task marked urgent at setup.
public struct UrgentLine: View {
    let missed: String
    @Environment(\.kelo) private var c

    public init(missed: String) { self.missed = missed }

    public var body: some View {
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(.alert, size: 16, tint: c.dangerInk)
            Text(missed.isBlank ? L(R.string.task_grant_urgent) : L(R.string.task_grant_urgent_missed, missed))
                .font(KeloFont.small).foregroundStyle(c.dangerInk).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10).fill(c.dangerSoft))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("kelo.urgent")
    }
}

/// The items a permission for many covers, behind "목록 보기 (37)": the user sees exactly what the one Allow is for.
public struct ApprovalItems: View {
    let items: [String]
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce
    @State private var open = false

    public init(items: [String]) { self.items = items }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LinkButton(L(open ? R.string.chat_approval_list_hide : R.string.chat_approval_list_show, items.count)) {
                withAnimation(reduce ? nil : .easeInOut(duration: 0.2)) { open.toggle() }
            }
            .accessibilityIdentifier("kelo.approval.list")
            if open {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(items.enumerated()), id: \.offset) { i, line in
                            Text("\(i + 1). \(line)").font(KeloFont.small).foregroundStyle(c.ink)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(.horizontal, 12).padding(.vertical, 8)
                }
                .frame(maxHeight: 280)
                .fixedSize(horizontal: false, vertical: items.count <= 10)
                .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

public extension String {
    /// Empty or only spaces and line breaks.
    var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}
