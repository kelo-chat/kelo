import SwiftUI
import KeloCore
import KeloShared

/// A question from work the chat is not following (a background job, a scheduled run), live at the bottom of the chat (Android
/// chat/ChatAskCard.kt): a line saying what asked, then the same card and answers as on the 작업 page. Once answered here it folds
/// into one line. `onAnswer`: (allow, remember the site, remember for the scheduled job, always for the recipient).
struct ChatAskCard: View {
    let ask: ChatAsk
    let onSolve: () -> Void
    let onAnswer: (Bool, Bool, Bool, Bool) -> Void
    @Environment(\.kelo) private var c
    @State private var rememberSite = false

    var body: some View {
        let a = ask.approval
        CardBox {
            if let end = ask.end {
                let what = a.event.label.isEmpty ? a.event.name : a.event.label
                let site = a.site.isEmpty ? L(R.string.handover_this_site) : a.site
                ResultLine(
                    icon: end == .denied || end == .skipped ? .close : (end == .gone ? .clock : .check),
                    text: end == .progressed ? L(R.string.handover_progressed)
                        : end == .solved ? L(a.isVerification ? R.string.handover_verify_done : R.string.handover_solved)
                        : end == .skipped ? L(R.string.handover_skipped, site)
                        : L(R.string.chat_ask_resolved, what, Self.endText(end)),
                    muted: end == .gone || end == .denied || end == .skipped)
            } else {
                originLine(a)
                if a.isHumanCheck {
                    humanCheck(a)
                } else {
                    approvalBody(a)
                }
            }
        }
        .accessibilityIdentifier("kelo.ask")
    }

    static func endText(_ e: AskEnd) -> String {
        switch e {
        case .allowed, .solved, .progressed: L(R.string.agent_approval_allowed)
        case .always: L(R.string.agent_approval_always)
        case .alwaysRecipient: L(R.string.recipient_approval_always)
        case .keptForSchedule: L(R.string.approval_kept_for_schedule)
        case .denied, .skipped: L(R.string.agent_approval_denied)
        case .gone: L(R.string.agent_approval_gone)
        }
    }

    /// "따로 맡긴 일 · <the job's title>" over the card.
    private func originLine(_ a: PendingApproval) -> some View {
        let origin = ChatAsks.origin(a)
        let head = switch origin {
        case .job: L(R.string.chat_ask_from_job)
        case .schedule: L(R.string.chat_ask_from_schedule)
        case .auto: L(R.string.work_ask_unattended)
        case .chat: L(R.string.chat_ask_from_reply)
        }
        let title = a.title.trimmingCharacters(in: .whitespacesAndNewlines)
        let text = !title.isEmpty && origin != .chat ? L(R.string.chat_ask_titled, head, title) : head
        return HStack(alignment: .firstTextBaseline, spacing: 6) {
            KeloIconView(origin == .schedule ? .calendar : .work, size: 14, tint: c.muted)
            Text(text).font(KeloFont.label).foregroundStyle(c.muted).lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .combine)
    }

    private func asked(_ a: PendingApproval) -> some View {
        Text(L(R.string.agent_perm_asked, RelWords.of(a.askedAt))).font(KeloFont.label).foregroundStyle(c.muted)
    }

    /// A site's check whether a person is there: "Solve it" (the tab live with control taken) and "Skip this site", one under the other.
    /// A sign-in's second step (`isVerification`) has its own words, the server's step first, and a shield.
    /// A sign-in approved on the phone (`isApproveStep`): "I approved it" first, the live view second.
    @ViewBuilder private func humanCheck(_ a: PendingApproval) -> some View {
        if a.isDevice { deviceCheck(a) } else { personCheck(a) }
    }

    /// The phone's screen handed to the owner: the server's own (already translated) question and reason, open it or say you are done.
    @ViewBuilder private func deviceCheck(_ a: PendingApproval) -> some View {
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(.user, size: 22, tint: c.accent)
            Text(a.event.ask.isEmpty ? L(R.string.handover_device_open) : a.event.ask).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
        }
        if let detail = a.event.detail, !detail.isEmpty {
            Text(detail).font(KeloFont.body).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
        }
        if a.askedAt > 0 { asked(a) }
        PrimaryButton(L(R.string.handover_device_open), enabled: !ask.working, action: onSolve)
        SecondaryButton(L(R.string.live_done), enabled: !ask.working, fill: true) { onAnswer(true, false, false, false) }
    }

    @ViewBuilder private func personCheck(_ a: PendingApproval) -> some View {
        let site = a.site.isEmpty ? L(R.string.handover_this_site) : a.site
        let verify = a.isVerification
        let approveStep = a.event.check?.isApproveStep == true
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(verify ? .shield : .user, size: 22, tint: c.accent)
            Text(L(verify ? R.string.handover_verify_title : R.string.handover_title, site)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
        }
        let step = a.event.check?.name ?? ""
        if verify && !step.isEmpty {
            Text(step).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
        Text(L(approveStep ? R.string.handover_verify_approve_body : (verify ? R.string.handover_verify_body : R.string.handover_body))).font(KeloFont.body).foregroundStyle(c.muted)
            .fixedSize(horizontal: false, vertical: true)
        if a.askedAt > 0 { asked(a) }
        if approveStep {
            PrimaryButton(L(R.string.handover_verify_approved), enabled: !ask.working) { onAnswer(true, false, false, false) }
            SecondaryButton(L(R.string.handover_verify_open), enabled: !ask.working, fill: true, action: onSolve)
        } else {
            PrimaryButton(L(verify ? R.string.handover_verify_open : R.string.handover_solve), enabled: !ask.working, action: onSolve)
        }
        SecondaryButton(L(R.string.handover_skip), enabled: !ask.working, fill: true) { onAnswer(false, false, false, false) }
    }

    @ViewBuilder private func approvalBody(_ a: PendingApproval) -> some View {
        if a.event.urgent && a.event.grant?.urgent != true { UrgentLine(missed: a.event.missed) }
        if let grant = a.event.grant {
            // the setup card of a "when X happens, do Y" task, the same as in the chat
            TaskGrantApproval(grant: grant, open: !ask.working) { ok in onAnswer(ok, false, false, false) }
        } else if let payment = a.event.payment {
            // the chat's own purchase card: where, what, how much, which card
            let item = ChatItem.Approval(key: a.id, id: a.id, name: a.event.name, detail: a.event.detail, label: a.event.label,
                                         payment: payment, card: a.event.card,
                                         send: ask.working ? .working : (ask.cardError.map { .failed($0) } ?? .idle))
            PurchaseApproval(approval: item, payment: payment) { ok in onAnswer(ok, false, false, false) }
        } else {
            HStack(alignment: .top, spacing: 8) {
                KeloIconView(.shield, size: 22, tint: c.accent)
                let w = a.event.label.isEmpty ? a.event.name : a.event.label
                Text(TurnEvent.ApprovalRequest.question(a.event.ask) ?? L(R.string.chat_approval_ask,
                       AskWords.askWord(w, language: appLanguageCode()), AskWords.objectParticle(w)))
                    .font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            }
            if !a.site.isEmpty && a.event.lines.isEmpty { MutedText(a.site, small: true) }
            if let pre = a.event.preapproval { PreapprovalFacts(pre) } else if let e = a.event.editable {
                ApprovalFacts(lines: ApprovalEditRules.withoutMessage(a.event.lines, e), detail: nil)
                EditableMessageBlock(approvalId: a.id, editable: e, enabled: !ask.working)
            } else { ApprovalFacts(lines: a.event.lines, detail: a.event.detail) }
            if !a.event.forgetPlan.isEmpty { ForgetPlanList(planId: a.event.forgetPlan) }
            if !a.event.items.isEmpty { ApprovalItems(items: a.event.items) }
            if a.askedAt > 0 { asked(a) }
            if a.canRemember && !a.site.isEmpty && a.event.payment == nil {
                RememberSiteCheck(site: a.site, checked: rememberSite, enabled: !ask.working) { rememberSite = $0 }
            }
            if RecipientRules.canAlways(a.event.offers) {
                RecipientChoices(offers: a.event.offers, enabled: !ask.working) { answer in onAnswer(answer.approve, false, false, answer == .always) }
            } else {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) {
                        SecondaryButton(L(R.string.chat_approval_deny), enabled: !ask.working, fill: true) { onAnswer(false, false, false, false) }
                        PrimaryButton(L(R.string.chat_approval_allow), enabled: !ask.working) { onAnswer(true, rememberSite, false, false) }
                    }
                    VStack(spacing: 8) {
                        PrimaryButton(L(R.string.chat_approval_allow), enabled: !ask.working) { onAnswer(true, rememberSite, false, false) }
                        SecondaryButton(L(R.string.chat_approval_deny), enabled: !ask.working, fill: true) { onAnswer(false, false, false, false) }
                    }
                }
            }
            if a.canRememberSchedule {
                LinkButton(L(R.string.approval_keep_for_schedule)) { onAnswer(true, false, true, false) }
            }
        }
    }
}
