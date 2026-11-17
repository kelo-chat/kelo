import SwiftUI
import KeloCore
import KeloShared

// The timeline's cards (Android work/WorkParts.kt): running work, questions waiting, the agenda and finished work.

// ---- 지금 하는 일

/// A running task: what it is doing now, the jobs it handed off (nested), and its live browser and Stop.
struct NowTaskCard: View {
    let model: WorkModel
    let node: TaskNode
    let tabs: [BrowserTab]
    let workers: [Worker]
    let onOpen: () -> Void
    let onOpenJob: (String) -> Void
    let onOpenTask: (Int64) -> Void
    let onOpenLive: (String) -> Void
    @State private var confirmStop = false
    @Environment(\.kelo) private var c

    var body: some View {
        let t = node.task
        EntryBox(action: onOpen) {
            HStack(alignment: .top, spacing: 8) {
                Text(t.title.isEmpty ? L(R.string.agent_task_untitled) : t.title).font(.system(.callout, weight: .semibold))
                    .foregroundStyle(c.ink).lineLimit(3).frame(maxWidth: .infinity, alignment: .leading)
                if node.inBackground {
                    StatusChip(L(R.string.work_task_in_background), c.accent)
                } else {
                    StatusChip(WorkText.taskStatus(t.status), c.task(t.status))
                }
            }
            if t.running && (t.liveStatus != nil || t.status == AgentTask.statusRunning) {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.mini).tint(c.accent)
                    Text(t.liveStatus ?? WorkText.taskStatus(t.status)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3)
                }
            }
            if let src = WorkText.source(t.source) { Text(src).font(KeloFont.label).foregroundStyle(c.muted) }
            ForEach(node.jobs, id: \.worker.id) { j in
                NestedJob(model: model, worker: j.worker, onOpen: { onOpenJob(j.worker.id) }, onOpenLive: onOpenLive)
            }
            ForEach(node.children, id: \.id) { ch in
                Button { onOpenTask(ch.id) } label: {
                    HStack(spacing: 8) {
                        Circle().fill(c.task(ch.status)).frame(width: 6, height: 6)
                        Text(ch.title.isEmpty ? L(R.string.agent_task_untitled) : ch.title).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(WorkText.taskStatus(ch.status)).font(KeloFont.label).foregroundStyle(c.task(ch.status))
                    }
                    .padding(.leading, 12).padding(.top, 2).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if t.running {
                FlowLayout(spacing: 0, lineSpacing: 0) {
                    if let tab = Timeline.tabFor(t, tabs: tabs, workers: workers) {
                        CardAction(L(R.string.work_live_view), icon: .eye) { onOpenLive(tab) }
                    }
                    CardAction(L(R.string.agent_task_stop), icon: .stop, danger: true) { confirmStop = true }
                }
            }
        }
        .keloConfirm(L(R.string.agent_task_stop_confirm), isPresented: $confirmStop, yes: L(R.string.agent_task_stop)) {
            Haptics.warning()
            model.stopTask(t.id)
        }
    }
}

/// A job inside its parent's card: a thin line on its left, its state, its last step and its step budget.
struct NestedJob: View {
    let model: WorkModel
    let worker: Worker
    let onOpen: () -> Void
    let onOpenLive: (String) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        JobBody(model: model, worker: worker, onOpenLive: onOpenLive)
            .padding(.leading, 12).padding(.top, 4)
            .overlay(alignment: .leading) { Rectangle().fill(c.job(worker.status).opacity(0.5)).frame(width: 2) }
            .contentShape(Rectangle())
            .onTapGesture(perform: onOpen)
    }
}

/// A job's title, kind and state, its last step, its step budget, and its actions.
struct JobBody: View {
    let model: WorkModel
    let worker: Worker
    let onOpenLive: (String) -> Void
    @State private var confirmStop = false
    @Environment(\.kelo) private var c

    var body: some View {
        let w = worker
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top, spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(w.title.isEmpty ? L(R.string.agent_task_untitled) : w.title).font(.system(.subheadline, weight: .medium))
                        .foregroundStyle(c.ink).lineLimit(3)
                    Text(L(R.string.work_job_line, WorkText.jobKind(w.kind))).font(KeloFont.label).foregroundStyle(c.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                StatusChip(WorkText.jobStatus(w.status, wakeAt: w.wakeAt), c.job(w.status))
            }
            if let st = w.statusText, !st.isEmpty { Text(st).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3) }
            if let p = Timeline.progress(w) {
                ThinProgress(value: p, tone: c.job(w.status))
                Text(L(R.string.work_job_steps, w.steps, w.maxSteps)).font(KeloFont.label).foregroundStyle(c.muted)
            }
            FlowLayout(spacing: 0, lineSpacing: 0) {
                if let tab = Timeline.tabFor(w) { CardAction(L(R.string.work_live_view), icon: .eye) { onOpenLive(tab) } }
                if w.running { CardAction(L(R.string.agent_task_stop), icon: .stop, danger: true) { confirmStop = true } }
            }
        }
        .keloConfirm(L(R.string.work_job_stop_confirm), isPresented: $confirmStop, yes: L(R.string.agent_task_stop)) {
            model.stopWorker(w.id)
        }
    }
}

/// A job of its own (its parent task is not in the list) running now.
struct JobCard: View {
    let model: WorkModel
    let job: JobNode
    let onOpen: () -> Void
    let onOpenLive: (String) -> Void
    var body: some View { EntryBox(action: onOpen) { JobBody(model: model, worker: job.worker, onOpenLive: onOpenLive) } }
}

/// What the user allowed for a running task as a whole: the site, the step, how many are used; one tap stops it (Android
/// work/WorkParts.kt TaskGrantCard).
struct TaskGrantCard: View {
    let grant: TaskPermission
    let onRevoke: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        EntryBox {
            HStack(spacing: 8) {
                KeloIconView(.shield, size: 18, tint: c.accent)
                Text(L(R.string.work_grant_title)).font(KeloFont.small).foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            }
            Text(L(R.string.work_grant_line, grant.site, grant.action, grant.used, grant.maxCount)).font(KeloFont.body).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
            CardAction(L(R.string.work_grant_revoke), icon: .close, danger: true, action: onRevoke)
        }
        .accessibilityIdentifier("kelo.grant")
    }
}

// ---- 답이 필요해요

/// An approval waiting: a payment that needs this phone's card says so on top; one asked while running alone says that.
struct ApprovalAsk: View {
    let model: WorkModel
    let approval: PendingApproval
    let deliver: Bool
    let onOpenChat: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if deliver { StatusChip(L(R.string.work_ask_card), c.warn) } else if approval.unattended { StatusChip(L(R.string.work_ask_unattended), c.warn) }
            if approval.isHumanCheck {
                KeloCard {
                    HumanCheckBody(model: model, a: approval, working: model.answering.contains(approval.id), onDone: {
                        Haptics.success()
                        model.answer(approval, approve: true)
                    })
                }
            } else {
                PendingApprovalCard(model: model, a: approval, working: model.answering.contains(approval.id), onOpenChat: onOpenChat)
            }
        }
    }
}

/// A site's check whether a person is there, handed to the user (Android agent/ApprovalParts.kt HumanCheckBody). "Solve it" opens
/// that tab live and takes it over (the server ends the question by itself once the check is gone); "Skip this site" answers no.
/// A sign-in approved on the phone (`isApproveStep`): "I approved it" first (`onDone`: done, go on), the live view second.
struct HumanCheckBody: View {
    let model: WorkModel
    let a: PendingApproval
    let working: Bool
    let onDone: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c

    var body: some View {
        if a.isDevice { deviceBody } else { personBody }
    }

    /// The phone's screen handed to the owner: the server's own question and reason, "Open the phone screen" and "Done" (no skip).
    private var deviceBody: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 8) {
                KeloIconView(.user, size: 22, tint: c.accent)
                Text(a.event.ask.isEmpty ? L(R.string.handover_device_open) : a.event.ask).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            }
            if let detail = a.event.detail, !detail.isEmpty {
                Text(detail).font(KeloFont.body).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            }
            if a.askedAt > 0 { Text(L(R.string.agent_perm_asked, WorkText.rel(a.askedAt))).font(KeloFont.label).foregroundStyle(c.muted) }
            PrimaryButton(L(R.string.handover_device_open), enabled: !working) { Haptics.tick(); app.router.openLive(a.tab, takeControl: true) }
            SecondaryButton(L(R.string.live_done), enabled: !working, fill: true, action: onDone)
        }
    }

    @ViewBuilder private var personBody: some View {
        let site = a.site.isEmpty ? L(R.string.handover_this_site) : a.site
        // a sign-in's second step (approve on the phone, or a code) is not a person check: its own words, the server's step first
        let verify = a.isVerification
        let approveStep = a.event.check?.isApproveStep == true
        let step = a.event.check?.name ?? ""
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 8) {
                KeloIconView(verify ? .shield : .user, size: 22, tint: c.accent)
                Text(L(verify ? R.string.handover_verify_title : R.string.handover_title, site)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            }
            if verify && !step.isEmpty {
                Text(step).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            Text(L(approveStep ? R.string.handover_verify_approve_body : (verify ? R.string.handover_verify_body : R.string.handover_body))).font(KeloFont.body).foregroundStyle(c.muted)
                .fixedSize(horizontal: false, vertical: true)
            if a.askedAt > 0 { Text(L(R.string.agent_perm_asked, WorkText.rel(a.askedAt))).font(KeloFont.label).foregroundStyle(c.muted) }
            if approveStep {
                PrimaryButton(L(R.string.handover_verify_approved), enabled: !working, action: onDone)
                SecondaryButton(L(R.string.handover_verify_open), enabled: !working, fill: true) { Haptics.tick(); app.router.openLive(a.tab, takeControl: true) }
            } else {
                PrimaryButton(L(verify ? R.string.handover_verify_open : R.string.handover_solve), enabled: !working) { Haptics.tick(); app.router.openLive(a.tab, takeControl: true) }
            }
            SecondaryButton(L(R.string.handover_skip), enabled: !working, fill: true) { Haptics.warning(); model.answer(a, approve: false) }
        }
    }
}

/// One approval waiting for an answer (Android agent/ApprovalParts.kt PendingCard). A payment that needs a card kept only on this
/// phone is allowed from the chat, where the card is sent after the owner's check.
struct PendingApprovalCard: View {
    let model: WorkModel
    let a: PendingApproval
    let working: Bool
    let onOpenChat: () -> Void
    @Environment(\.kelo) private var c
    @State private var rememberSite = false

    var body: some View {
        KeloCard {
            if a.event.urgent && a.event.grant?.urgent != true { UrgentLine(missed: a.event.missed) }
            if let grant = a.event.grant {
                // the setup card of a "when X happens, do Y" task, the same as in the chat
                TaskGrantApproval(grant: grant, open: !working) { ok in
                    if ok { Haptics.success() } else { Haptics.warning() }
                    model.answer(a, approve: ok)
                }
            } else if let p = a.event.payment {
                payment(p)
            } else {
                HStack(alignment: .top, spacing: 8) {
                    KeloIconView(.shield, size: 22, tint: c.accent)
                    Text(a.event.question ?? WorkText.approvalAsk(a.event.label.isEmpty ? a.event.name : a.event.label)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !a.site.isEmpty && a.event.lines.isEmpty { MutedText(a.site, small: true) }
                if let pre = a.event.preapproval { PreapprovalFacts(pre) } else if let e = a.event.editable {
                    ApprovalFactsView(lines: ApprovalEditRules.withoutMessage(a.event.lines, e), detail: nil)
                    EditableMessageBlock(approvalId: a.id, editable: e, enabled: !working)
                } else { ApprovalFactsView(lines: a.event.lines, detail: a.event.detail) }
                if !a.event.forgetPlan.isEmpty { ForgetPlanList(planId: a.event.forgetPlan) }
                if !a.event.items.isEmpty { ApprovalItems(items: a.event.items) }
                if a.askedAt > 0 { Text(L(R.string.agent_perm_asked, WorkText.rel(a.askedAt))).font(KeloFont.label).foregroundStyle(c.muted) }
                if a.canRemember && !a.site.isEmpty && a.event.payment == nil {
                    RememberSiteCheck(site: a.site, checked: rememberSite, enabled: !working) { rememberSite = $0 }
                }
                if RecipientRules.canAlways(a.event.offers) {
                    RecipientChoices(offers: a.event.offers, enabled: !working) { answer in
                        if answer.approve { Haptics.success() } else { Haptics.warning() }
                        model.answer(a, approve: answer.approve, rememberRecipient: answer == .always)
                    }
                } else {
                    answerButtons(allow: { Haptics.success(); model.answer(a, approve: true, remember: rememberSite) })
                }
                if a.canRememberSchedule {
                    LinkButton(L(R.string.approval_keep_for_schedule)) { Haptics.success(); model.answer(a, approve: true, rememberSchedule: true) }
                }
            }
        }
    }

    @ViewBuilder private func payment(_ p: PaymentInfo) -> some View {
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(.card, size: 22, tint: c.accent)
            Text(L(R.string.chat_purchase_question)).font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
        // the order as one card: site, items or summary, where it goes, the card, the coupon, the total
        OrderDetails(payment: p, card: a.event.card)
        if let card = a.event.card {
            if card.autoPay && !card.reasonText.isEmpty { MutedText(L(R.string.chat_purchase_not_auto, card.reasonText), small: true) }
            if card.needsCard { MutedText(L(R.string.chat_purchase_needs_card), small: true) }
        }
        let needsCard = a.event.card?.needsCard == true
        answerButtons(allow: {
            if needsCard { onOpenChat() } else { Haptics.success(); model.answer(a, approve: true) }
        })
        if needsCard { MutedText(L(R.string.work_ask_card_in_chat), small: true) }
    }

    private func answerButtons(allow: @escaping () -> Void) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) {
                SecondaryButton(L(R.string.chat_approval_deny), enabled: !working, fill: true) { Haptics.warning(); model.answer(a, approve: false) }
                PrimaryButton(L(R.string.chat_approval_allow), enabled: !working, action: allow)
            }
            VStack(spacing: 8) {
                PrimaryButton(L(R.string.chat_approval_allow), enabled: !working, action: allow)
                SecondaryButton(L(R.string.chat_approval_deny), enabled: !working, fill: true) { Haptics.warning(); model.answer(a, approve: false) }
            }
        }
    }
}

/// What an approval will do, line by line (Android chat ApprovalFacts).
struct ApprovalFactsView: View {
    let lines: [ApprovalLine]
    let detail: String?
    @Environment(\.kelo) private var c

    var body: some View {
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, l in
                    if l.block {
                        // the whole text (a schedule's task): the label above, every word below, scrolling past 8 lines
                        VStack(alignment: .leading, spacing: 2) {
                            if !l.label.isEmpty { Text(l.label).font(KeloFont.small).foregroundStyle(c.muted) }
                            CappedScrollText(l.value)
                        }
                    } else {
                        FactRow(label: l.label, value: TokenText.fit(l.value, max: 28))
                    }
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
        } else if let d = detail, !d.isEmpty {
            Text(d).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A job stopped for the user: why, and how to let it go on.
struct JobAsk: View {
    let model: WorkModel
    let worker: Worker
    let onOpen: () -> Void
    let onOpenLive: (String) -> Void
    @State private var answering = false
    @Environment(\.kelo) private var c

    var body: some View {
        let w = worker
        EntryBox(action: onOpen) {
            HStack(alignment: .top, spacing: 8) {
                Text(w.title.isEmpty ? L(R.string.agent_task_untitled) : w.title).font(.system(.callout, weight: .semibold)).foregroundStyle(c.ink)
                    .lineLimit(3).frame(maxWidth: .infinity, alignment: .leading)
                StatusChip(WorkText.jobStatus(w.status, wakeAt: w.wakeAt), c.job(w.status))
            }
            Text(WorkText.waiting(w)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            if let r = w.reason, !r.isEmpty { Text(r).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(4) }
            FlowLayout(spacing: 0, lineSpacing: 0) {
                CardAction(L(R.string.work_job_resume), icon: .retry) { model.resumeWorker(w.id) }
                CardAction(L(R.string.work_job_answer), icon: .chat) { answering = true }
                if w.waitingFor == "control", let tab = Timeline.tabFor(w) { CardAction(L(R.string.work_live_view), icon: .eye) { onOpenLive(tab) } }
            }
        }
        .keloSheet(isPresented: $answering, detents: [.medium, .large]) {
            MessageSheet(onSend: { model.messageWorker(w.id, text: $0) }, onClose: { answering = false })
        }
    }
}

/// A follow-up on hold: what it was about, since when, and its buttons (다시 시작 and the rest).
struct HoldAsk: View {
    let model: WorkModel
    let followup: Followup
    let busy: Bool
    let onOpenChat: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let f = followup
        EntryBox {
            HStack(alignment: .top, spacing: 8) {
                Text(f.title.isEmpty ? L(R.string.agent_task_untitled) : f.title).font(.system(.callout, weight: .semibold)).foregroundStyle(c.ink)
                    .lineLimit(4).frame(maxWidth: .infinity, alignment: .leading)
                if f.onHold { StatusChip(L(R.string.followup_on_hold), c.muted) }
            }
            Text(WorkText.followupKind(f.kind)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            if f.onHold && f.createdAt > 0 {
                Text(L(R.string.followup_hold_since, WorkText.rel(f.createdAt))).font(KeloFont.small).foregroundStyle(c.muted)
            }
            FollowupButtonsView(buttons: FollowupRules.holdButtons(f).map(\.step), busy: busy) { step in
                if case .openLogin = step { onOpenChat() } else { model.runFollowup(f, step) }
            }
            .padding(.top, 2).padding(.bottom, 10)
        }
    }
}

/// A secure entry waiting for its value, as one short row: what it is ("텔레그램 봇 토큰 입력을 기다려요") and when it was asked. A tap
/// goes to its card in the chat (scrolled to it and marked for a moment); the card is where the value goes in. 그만두기 cancels it.
struct EntryAsk: View {
    let request: SecretRequest
    let onOpen: (() -> Void)?
    let onCancel: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let r = request
        EntryBox(action: onOpen) {
            Text(WorkText.entry(r)).font(.system(.callout, weight: .semibold)).foregroundStyle(c.ink)
                .lineLimit(4).fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 8) {
                if r.createdAt > 0 { Text(WorkText.rel(r.createdAt)).font(KeloFont.small).foregroundStyle(c.muted) }
                Spacer(minLength: 4)
                Button(action: onCancel) {
                    Text(L(R.string.work_entry_cancel)).font(KeloFont.small.weight(.medium)).foregroundStyle(c.muted).frame(minHeight: 44).padding(.horizontal, 6)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("kelo.work.entry.cancel")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.work.entry.\(r.id)")
    }
}

/// A follow-up's buttons; they wrap rather than squeeze. One filled button at most (the first main step).
struct FollowupButtonsView: View {
    let buttons: [FollowupStep]
    let busy: Bool
    let onPress: (FollowupStep) -> Void
    @Environment(\.kelo) private var c

    static func primary(_ s: FollowupStep) -> Bool {
        switch s {
        case .openLogin, .retry, .resumeWorker: return true
        case .answer(_, let approve): return approve
        default: return false
        }
    }

    var body: some View {
        if busy {
            ProgressView().controlSize(.small).tint(c.muted).padding(.vertical, 6)
        } else if !buttons.isEmpty {
            let main = buttons.firstIndex(where: FollowupButtonsView.primary)
            FlowLayout {
                ForEach(Array(buttons.enumerated()), id: \.offset) { i, step in
                    Button { onPress(step) } label: {
                        Text(WorkText.followupAction(step)).font(KeloFont.button).multilineTextAlignment(.center)
                            .foregroundStyle(i == main ? c.onAccent : c.ink)
                            .padding(.horizontal, 14).padding(.vertical, 8)
                            .background(Capsule().fill(i == main ? c.accent : c.card))
                            .overlay(Capsule().stroke(i == main ? .clear : c.line, lineWidth: 1))
                    }
                    .buttonStyle(PressShrinkStyle())
                }
            }
        }
    }
}

/// A few words to a job: read before its next step, or it goes on with them.
struct MessageSheet: View {
    let onSend: (String) -> Void
    let onClose: () -> Void
    @State private var text = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.work_job_answer), onClose: onClose)
                MutedText(L(R.string.work_job_answer_hint), small: true)
                KeloTextField(L(R.string.work_job_answer_placeholder), text: $text, axis: .vertical, maxLength: 4000)
                PrimaryButton(L(R.string.work_job_answer_send), enabled: !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) {
                    onSend(text); onClose()
                }
            }
            .padding(.horizontal, 20).padding(.bottom, 28)
        }
    }
}

// ---- 다가오는 일

/// One planned run: the schedule's name and how often it runs; notes when it runs now, failed last time, or is paused.
struct AgendaRow: View {
    let schedule: Schedule
    let span: ScheduleSpan?
    var onResume: (() -> Void)?
    let onOpen: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let s = schedule
        VStack(alignment: .leading, spacing: 2) {
            FlowLayout(spacing: 6, lineSpacing: 4) {
                Text(s.name).font(.system(.callout, weight: .medium)).foregroundStyle(s.enabled ? c.ink : c.muted).lineLimit(2)
                if s.running { StatusChip(L(R.string.agent_status_running), c.accent) }
                if s.paused { StatusChip(L(R.string.work_run_paused), c.warn) }
                if s.createdBy == "agent" { StatusChip(L(R.string.work_schedule_by_agent), c.muted) }
            }
            Text(WorkText.schedule(s, days: span)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3)
            if let lr = s.lastRun, let at = lr.at, lr.status == "failed" {
                Text(L(R.string.agent_schedule_last, WorkText.run(lr.status), WorkText.rel(at))).font(KeloFont.label).foregroundStyle(c.danger)
            }
            if let r = s.disabledReason, !r.isEmpty, !s.enabled, !s.paused {
                Text(L(R.string.agent_schedule_off_reason, r)).font(KeloFont.label).foregroundStyle(c.danger).lineLimit(3)
            }
            if s.paused {
                Text(L(R.string.work_schedule_paused_reason, (s.pausedReason ?? "").isEmpty ? L(R.string.work_schedule_paused_unknown) : s.pausedReason!))
                    .font(KeloFont.label).foregroundStyle(c.warn).lineLimit(4)
                if let onResume { CardAction(L(R.string.work_schedule_resume), icon: .retry, action: onResume) }
            }
        }
        .padding(.top, 10).padding(.bottom, 4).padding(.trailing, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .accessibilityAddTraits(.isButton)
    }
}

struct PausedToggle: View {
    let count: Int
    let open: Bool
    let onToggle: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button(action: onToggle) {
            HStack(spacing: 6) {
                Text(P(R.plurals.work_paused_count, count, count)).font(KeloFont.small).foregroundStyle(c.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                KeloIconView(open ? .minus : .plus, size: 16, tint: c.muted)
            }
            .padding(.horizontal, 8).padding(.vertical, 10).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.leading, railWidth - 8)
    }
}

// ---- 지난 일

/// A finished task: its title, its one-line result, where it came from, and the jobs it handed off.
struct PastRow: View {
    let node: TaskNode
    let onOpen: () -> Void
    let onOpenJob: (String) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let t = node.task
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(t.title.isEmpty ? L(R.string.agent_task_untitled) : t.title).font(.system(.callout, weight: .medium)).foregroundStyle(c.ink)
                    .lineLimit(3)
                if !t.seen { Circle().fill(c.accent).frame(width: 8, height: 8).accessibilityLabel(L(R.string.agent_news)) }
            }
            Text(t.result.isEmpty ? WorkText.taskStatus(t.status) : t.result).font(KeloFont.small).foregroundStyle(t.failed ? c.danger : c.muted)
                .lineLimit(3)
            FlowLayout(spacing: 6, lineSpacing: 2) {
                if let src = WorkText.source(t.source) { Text(src).font(KeloFont.label).foregroundStyle(c.muted) }
                ForEach(node.jobs, id: \.worker.id) { j in
                    Button { onOpenJob(j.worker.id) } label: {
                        Text(L(R.string.work_past_job, WorkText.jobStatus(j.worker.status, wakeAt: j.worker.wakeAt))).font(KeloFont.label).foregroundStyle(c.job(j.worker.status))
                            .padding(.horizontal, 2).padding(.vertical, 2)
                    }
                    .buttonStyle(.plain)
                }
                if !node.children.isEmpty {
                    Text(P(R.plurals.work_past_children, node.children.count, node.children.count)).font(KeloFont.label).foregroundStyle(c.muted)
                }
            }
        }
        .padding(.top, 10).padding(.bottom, 4).padding(.trailing, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .accessibilityAddTraits(.isButton)
    }
}

// ---- 지켜보는 중

/// A watcher on the timeline: its name, what it looks for, the last value it saw, and its switch.
struct WatcherRow: View {
    let model: WorkModel
    let watcher: Watcher
    let onOpen: (() -> Void)?
    @Environment(\.kelo) private var c

    var body: some View {
        let w = watcher
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                FlowLayout(spacing: 6, lineSpacing: 4) {
                    Text(w.name).font(.system(.callout, weight: .medium)).foregroundStyle(c.ink).lineLimit(2)
                    StatusChip(WorkText.watch(w.state), c.watch(w.state))
                }
                if !w.description.isEmpty { Text(w.description).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3) }
                if let b = w.buy, b.armed {
                    Text(b.problems.isEmpty ? L(R.string.buy_ready_short) : L(R.string.buy_ready_problems_short, b.problems.count))
                        .font(KeloFont.label)
                        .foregroundStyle(b.problems.isEmpty ? c.success : c.danger)
                }
                if let v = w.lastValue, !v.isEmpty {
                    Text(L(R.string.work_watch_last_value, v)).font(KeloFont.label).foregroundStyle(c.ink).lineLimit(2)
                }
                if let f = w.lastFiredAt { Text(L(R.string.work_watch_fired_at, WorkText.rel(f))).font(KeloFont.label).foregroundStyle(c.success) }
                if w.state == "error", let e = w.lastError { Text(e).font(KeloFont.label).foregroundStyle(c.danger).lineLimit(3) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture { onOpen?() }
            Toggle("", isOn: Binding(get: { w.enabled }, set: { Haptics.tick(); model.setWatcherEnabled(w, $0) }))
                .labelsHidden().tint(c.accent)
                .accessibilityLabel(L(R.string.work_watch_switch, w.name))
        }
        .padding(.top, 8).padding(.bottom, 4)
    }
}

/// A browser tab in a list: its picture, title, host, state and whose it is.
struct TabRow: View {
    let tab: BrowserTab
    let action: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let t = tab
        Button(action: action) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8).fill(c.bubble)
                    if let img = BrowserImage.decode(t.thumbnail) {
                        Image(platformImage: img).resizable().scaledToFill()
                    } else {
                        KeloIconView(.globe, size: 22, tint: c.muted)
                    }
                }
                .frame(width: 72, height: 48).clipShape(RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 2) {
                    Text(t.title.isEmpty ? (t.host.isEmpty ? WorkText.tabKind(t) : t.host) : t.title).font(.system(.callout, weight: .medium))
                        .foregroundStyle(c.ink).lineLimit(2)
                    if !t.host.isEmpty { Text(t.host).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1) }
                    FlowLayout(spacing: 6, lineSpacing: 4) {
                        tabStatus(t)
                        Text(WorkText.tabKind(t)).font(KeloFont.label).foregroundStyle(c.muted).lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(12).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func tabStatus(_ t: BrowserTab) -> some View {
        let (word, tone): (String, Color) = switch t.status {
        case "working": (L(R.string.agent_tab_working), c.accent)
        case "failed": (L(R.string.agent_tab_failed), c.danger)
        default: (L(R.string.agent_tab_idle), c.muted)
        }
        return Text(word).font(.system(.caption2, weight: .semibold)).foregroundStyle(tone)
            .padding(.horizontal, 7).padding(.vertical, 2).background(Capsule().fill(tone.opacity(0.12)))
    }
}

/// A tab's thumbnail ("data:image/jpeg;base64,…" or bare base64) as an image.
enum BrowserImage {
    static func decode(_ text: String?) -> PlatformImage? {
        guard var t = text, !t.isEmpty else { return nil }
        if let comma = t.firstIndex(of: ","), t.hasPrefix("data:") { t = String(t[t.index(after: comma)...]) }
        guard let data = Data(base64Encoded: t, options: .ignoreUnknownCharacters) else { return nil }
        return PlatformImage(data: data)
    }
}
