import SwiftUI
import KeloCore
import KeloShared

// Sub pages of the work page: one task, one background job, one watcher, all schedules, the archive, and the open
// browser tabs (Android agent/TaskPages.kt, work/JobPages.kt, work/WatchPages.kt).

/// A task in full: the answer, its state, Stop and its live browser while it runs, the approvals it asked. Seen once opened.
struct TaskPage: View {
    let model: WorkModel
    let id: Int64
    let onBack: () -> Void
    let onOpenLive: (String) -> Void
    @State private var read: TaskDetailModel
    @State private var confirmStop = false
    @State private var tick = 0
    @Environment(\.kelo) private var c

    init(model: WorkModel, id: Int64, onBack: @escaping () -> Void, onOpenLive: @escaping (String) -> Void) {
        self.model = model; self.id = id; self.onBack = onBack; self.onOpenLive = onOpenLive
        _read = State(initialValue: TaskDetailModel(work: model, id: id))
    }

    private var detail: TaskDetail? { read.detail }
    private var failed: Bool { read.failed }

    var body: some View {
        SubPage(detail.flatMap { $0.title.isEmpty ? nil : $0.title } ?? L(R.string.work_task_title), onBack: onBack) {
            if let d = detail {
                HStack(spacing: 8) {
                    statusIcon(d.status)
                    Text(d.running ? (d.liveStatus ?? WorkText.taskStatus(d.status)) : WorkText.taskStatus(d.status)).font(KeloFont.body)
                        .foregroundStyle(d.status == AgentTask.statusFailed ? c.danger : c.ink).frame(maxWidth: .infinity, alignment: .leading)
                    if d.startedAt > 0 { Text(WorkText.dayClock(d.startedAt)).font(KeloFont.label).foregroundStyle(c.muted) }
                }
                if let src = WorkText.source(d.source) { MutedText(src, small: true) }
                if d.running {
                    let tab = Timeline.tabFor(taskBrief(d), tabs: model.browser.data?.items ?? [], workers: model.workers.data?.items ?? [])
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) { stop; if let tab { live(tab) } }
                        VStack(spacing: 8) { stop; if let tab { live(tab) } }
                    }
                }
                if !d.answer.isEmpty {
                    Panel { MarkdownBlock(d.answer) }
                } else if !d.running && !d.result.isEmpty {
                    MutedText(d.result)
                }
                if !d.error.isEmpty { ErrorText(d.error, small: true) }
                if SkillRules.canSave(d.status) { SaveAsSkillSection(api: model.api, taskId: d.id, onMessage: { model.say($0) }) }
                if !d.approvals.isEmpty {
                    Text(L(R.string.agent_task_approvals)).font(KeloFont.titleSmall).foregroundStyle(c.ink).padding(.top, 6)
                    VStack(spacing: 0) {
                        ForEach(Array(d.approvals.enumerated()), id: \.offset) { i, a in
                            if i > 0 { Hairline().padding(.leading, 16) }
                            ApprovalRecordRow(a: a, when: a.askedAt > 0 ? WorkText.dayClock(a.askedAt) : "", padded: true)
                        }
                    }
                    .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                }
            } else if failed {
                ErrorText(L(R.string.connect_failed))
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task(id: tick) { await read.follow() }
        .onAppear { model.markSeen(id) }
        .keloConfirm(L(R.string.agent_task_stop_confirm), isPresented: $confirmStop, yes: L(R.string.agent_task_stop)) {
            Haptics.warning()
            model.stopTask(id) { tick += 1 }
        }
    }

    private var stop: some View { SecondaryButton(L(R.string.agent_task_stop), fill: true) { confirmStop = true } }
    private func live(_ tab: String) -> some View { SecondaryButton(L(R.string.work_live_view), icon: .eye, fill: true) { onOpenLive(tab) } }

    @ViewBuilder private func statusIcon(_ status: String) -> some View {
        Group {
            switch status {
            case AgentTask.statusRunning: ProgressView().controlSize(.small).tint(c.accent)
            case AgentTask.statusWaiting: KeloIconView(.hourglass, size: 22, tint: c.accent)
            case AgentTask.statusFailed: KeloIconView(.alert, size: 22, tint: c.danger)
            case AgentTask.statusStopped, "interrupted": KeloIconView(.close, size: 20, tint: c.muted)
            case "waiting_model": KeloIconView(.hourglass, size: 22, tint: c.muted)
            default: KeloIconView(.check, size: 22, tint: c.success)
            }
        }
        .frame(width: 28, height: 28)
    }
}

/// One background job: what it was asked, its state, step budget, steps so far and report; Stop / go on / a few words / live.
struct JobPage: View {
    let model: WorkModel
    let id: String
    let onBack: () -> Void
    let onOpenLive: (String) -> Void
    let onOpenTask: (Int64) -> Void
    @State private var read: WorkerDetailModel
    @State private var tick = 0
    @State private var confirmStop = false
    @State private var answering = false
    @Environment(\.kelo) private var c
    @Environment(AppModel.self) private var app
    private var activity: AgentActivityModel { app.activity }

    init(model: WorkModel, id: String, onBack: @escaping () -> Void, onOpenLive: @escaping (String) -> Void, onOpenTask: @escaping (Int64) -> Void) {
        self.model = model; self.id = id; self.onBack = onBack; self.onOpenLive = onOpenLive; self.onOpenTask = onOpenTask
        _read = State(initialValue: WorkerDetailModel(work: model, id: id))
    }

    private var detail: WorkerDetail? { read.detail }
    private var failed: Bool { read.failed }
    private func reload() { tick += 1 }

    var body: some View {
        SubPage(detail.flatMap { $0.title.isEmpty ? nil : $0.title } ?? L(R.string.work_job_title), onBack: onBack) {
            if let d = detail {
                let w = d.brief()
                FlowLayout(spacing: 8, lineSpacing: 4) {
                    StatusChip(WorkText.jobStatus(d.status, wakeAt: d.wakeAt), c.job(d.status))
                    Text(L(R.string.work_job_line, WorkText.jobKind(d.kind))).font(KeloFont.small).foregroundStyle(c.muted)
                    if d.startedAt > 0 { Text(WorkText.dayClock(d.startedAt)).font(KeloFont.label).foregroundStyle(c.muted) }
                }
                if w.needsYou || d.status == Worker.statusInterrupted {
                    Text(WorkText.waiting(w)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                if let p = Timeline.progress(w) {
                    ThinProgress(value: p, tone: c.job(d.status), height: 6)
                    Text(L(R.string.work_job_steps, d.steps, d.maxSteps)).font(KeloFont.label).foregroundStyle(c.muted)
                }
                if w.active { SecondaryButton(L(R.string.agent_task_stop), fill: true) { confirmStop = true } }
                if w.canResume { PrimaryButton(L(R.string.work_job_resume)) { model.resumeWorker(id) { tick += 1 } } }
                SecondaryButton(L(R.string.work_job_answer), icon: .chat, fill: true) { answering = true }
                if let tab = Timeline.tabFor(w) { SecondaryButton(L(R.string.work_live_view), icon: .eye, fill: true) { onOpenLive(tab) } }
                if let r = d.report, !r.summary.isEmpty || !r.details.isEmpty {
                    PanelLabel(L(R.string.work_job_report))
                    Panel {
                        if !r.summary.isEmpty { Text(r.summary).font(.system(.callout, weight: .medium)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true) }
                        if !r.details.isEmpty { MarkdownBlock(r.details) }
                    }
                }
                if let e = d.error, !e.isEmpty { ErrorText(e, small: true) }
                TaskGrantsBlock(key: "job:" + id, load: { await model.grants { try await $0.jobGrants(id) } }, revoke: model.revokeGrant)
                if SkillRules.canSave(d.status) { SaveAsSkillSection(api: model.api, workerId: id, onMessage: { model.say($0) }) }
                if !d.task.isEmpty {
                    PanelLabel(L(R.string.work_job_asked))
                    Panel {
                        Text(d.task).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        if let u = d.url, !u.isEmpty { MutedText(TokenText.fit(u), small: true) }
                    }
                }
                if !d.lines.isEmpty {
                    PanelLabel(L(R.string.work_job_steps_title))
                    Panel {
                        ForEach(Array(d.lines.reversed().enumerated()), id: \.offset) { i, l in
                            if i > 0 { Hairline() }
                            HStack(alignment: .top, spacing: 10) {
                                Text(l.at > 0 ? WorkText.clock(l.at) : "").font(KeloFont.label).foregroundStyle(c.muted).padding(.top, 2)
                                Text(l.text).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .padding(.vertical, 6)
                        }
                    }
                }
                if let pid = d.parentTaskId { LinkButton(L(R.string.work_job_parent)) { onOpenTask(pid) } }
            } else if failed {
                ErrorText(L(R.string.connect_failed))
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task(id: tick) { await read.follow() }
        .onChange(of: activity.workChanged) { _, _ in reload() }
        .keloConfirm(L(R.string.work_job_stop_confirm), isPresented: $confirmStop, yes: L(R.string.agent_task_stop)) {
            model.stopWorker(id) { tick += 1 }
        }
        .keloSheet(isPresented: $answering, detents: [.medium, .large]) {
            MessageSheet(onSend: { model.messageWorker(id, text: $0) { tick += 1 } }, onClose: { answering = false })
        }
    }
}

/// 열린 브라우저: every tab the agent keeps; a tap opens it live.
struct OpenBrowsersPage: View {
    let model: WorkModel
    let onBack: () -> Void
    let onOpenLive: (String) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        SubPage(L(R.string.work_open_browsers), onBack: onBack) {
            if let data = model.browser.data {
                if !data.available {
                    EmptyNote(L(R.string.agent_browser_none))
                } else if data.items.filter(\.isBrowserTab).isEmpty {
                    EmptyNote(L(R.string.agent_browser_empty))
                } else {
                    VStack(spacing: 0) {
                        // the Android device is not a browser: it has its own row in the work page, not here
                        ForEach(Array(data.items.filter(\.isBrowserTab).enumerated()), id: \.element.id) { i, t in
                            if i > 0 { Hairline() }
                            TabRow(tab: t) { onOpenLive(t.id) }
                        }
                    }
                    .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                }
            } else if model.browser.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }
            MutedText(L(R.string.work_open_browsers_note), small: true)
        }
        .onAppear { model.loadBrowser() }
    }
}

/// One watcher: what it checks and how often, its last check, a check now, its switch, its recent events, and removing it.
struct WatcherPage: View {
    let model: WorkModel
    let id: Int64
    let onBack: () -> Void
    @State private var read: WatcherEventsModel
    @State private var checking = false
    @State private var confirmDelete = false
    @Environment(\.kelo) private var c

    init(model: WorkModel, id: Int64, onBack: @escaping () -> Void) {
        self.model = model; self.id = id; self.onBack = onBack
        _read = State(initialValue: WatcherEventsModel(work: model, id: id))
    }

    private var events: [WatcherEvent]? { read.events }

    var body: some View {
        let w = model.watchers.data?.items.first { $0.id == id }
        SubPage(w?.name ?? L(R.string.work_watching), onBack: onBack) {
            if let w {
                Panel {
                    WatcherRow(model: model, watcher: w, onOpen: nil)
                    if !w.enabled && !w.canEnable { MutedText(L(R.string.work_watch_needs_check), small: true) }
                    if let r = w.disabledReason, !r.isEmpty, !w.enabled { Text(r).font(KeloFont.small).foregroundStyle(c.danger) }
                }
                if let b = w.buy, b.armed {
                    BuyReadyPanel(model: model, id: w.id, start: b)
                }
                Panel {
                    if let u = w.url, !u.isEmpty { FactRow(label: L(R.string.work_watch_url), value: TokenText.fit(u)) }
                    if w.everyMinutes > 0 { FactRow(label: L(R.string.work_watch_every), value: WorkText.interval(w.everyMinutes)) }
                    if let t = w.lastCheckedAt { FactRow(label: L(R.string.work_watch_checked), value: WorkText.dayClock(t)) }
                    if let t = w.nextCheckAt { FactRow(label: L(R.string.work_watch_next), value: WorkText.rel(t)) }
                    if w.once { FactRow(label: L(R.string.work_watch_once), value: L(R.string.work_watch_once_yes)) }
                    if let n = w.note, !n.isEmpty { FactRow(label: L(R.string.work_watch_note), value: n) }
                }
                if let d = w.dryRun {
                    PanelLabel(L(R.string.work_watch_last_check))
                    Panel {
                        Text(firstNonEmpty(d.detail, d.error, d.value)).font(KeloFont.body).foregroundStyle(d.ok ? c.ink : c.danger)
                            .fixedSize(horizontal: false, vertical: true)
                        if let at = d.at { Text(WorkText.dayClock(at)).font(KeloFont.label).foregroundStyle(c.muted) }
                    }
                }
                PrimaryButton(L(checking ? R.string.work_watch_checking : R.string.work_watch_check_now), enabled: !checking) {
                    checking = true
                    model.dryRunWatcher(w) { _ in checking = false }
                }
                PanelLabel(L(R.string.work_watch_events))
                if let ev = events {
                    if ev.isEmpty {
                        EmptyNote(L(R.string.work_watch_events_empty))
                    } else {
                        Panel {
                            ForEach(Array(ev.enumerated()), id: \.element.id) { i, e in
                                if i > 0 { Hairline() }
                                VStack(alignment: .leading, spacing: 2) {
                                    FlowLayout(spacing: 8, lineSpacing: 2) {
                                        Text(WorkText.watchEvent(e.kind)).font(.system(.caption2, weight: .semibold))
                                            .foregroundStyle(e.kind == "fired" ? c.success : e.kind == "error" ? c.danger : c.muted)
                                        Text(e.at > 0 ? WorkText.dayClock(e.at) : "").font(KeloFont.label).foregroundStyle(c.muted)
                                    }
                                    let line = (e.detail ?? "").isEmpty ? (e.value ?? "") : e.detail!
                                    if !line.isEmpty { Text(line).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(4) }
                                }
                                .padding(.vertical, 6)
                            }
                        }
                    }
                } else {
                    SkeletonRows(rows: 2)
                }
                TaskGrantsBlock(key: "watcher:\(id)", load: { await model.grants { try await $0.watcherGrants(id) } }, revoke: model.revokeGrant)
                LinkButton(L(R.string.work_watch_delete), danger: true) { confirmDelete = true }
            } else if model.watchers.failed {
                ErrorText(L(R.string.connect_failed))
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .onAppear { if model.watchers.data == nil { model.loadWatchers() } }
        .task(id: "\(id)-\(w?.dryRun?.at ?? 0)-\(w?.lastCheckedAt ?? 0)") { await read.load() }
        .keloConfirm(L(R.string.work_watch_delete_confirm, w?.name ?? ""), isPresented: $confirmDelete, yes: L(R.string.delete)) {
            if let w { model.deleteWatcher(w, then: onBack) }
        }
    }

    private func firstNonEmpty(_ xs: String?...) -> String { xs.compactMap { $0 }.first { !$0.isEmpty } ?? "" }
}

/// "바로 구매 준비됨": the state of a watcher that buys at its hit, what is not ready in the server's words, and a check now.
private struct BuyReadyPanel: View {
    let model: WorkModel
    let id: Int64
    let start: BuyReady
    @State private var b: BuyReady
    @State private var busy = false
    @Environment(\.kelo) private var c

    init(model: WorkModel, id: Int64, start: BuyReady) {
        self.model = model
        self.id = id
        self.start = start
        _b = State(initialValue: start)
    }

    var body: some View {
        PanelLabel(L(R.string.buy_ready_title))
        Panel {
            Text(L(b.problems.isEmpty ? R.string.buy_ready_ok : R.string.buy_ready_not_ok))
                .font(.system(.body, weight: .semibold))
                .foregroundStyle(b.problems.isEmpty ? c.success : c.danger)
            ForEach(b.problems, id: \.self) { p in
                Text("· " + p.text).font(KeloFont.small).foregroundStyle(c.ink)
            }
            if let signedIn = b.signedIn {
                FactRow(label: L(R.string.buy_ready_login), value: L(signedIn ? R.string.buy_ready_login_kept : R.string.buy_ready_login_out))
            }
            if let lastKeptAt = b.lastKeptAt {
                FactRow(label: L(R.string.buy_ready_checked), value: WorkText.rel(lastKeptAt))
            }
            if b.remembered {
                MutedText(L(R.string.buy_ready_remembered), small: true)
            }
            MutedText(L(R.string.buy_ready_about), small: true)
            SecondaryButton(L(busy ? R.string.work_watch_checking : R.string.buy_ready_check), enabled: !busy) {
                busy = true
                Task {
                    if let res = try? await model.buyCheck(id: id) {
                        b = res
                    }
                    busy = false
                }
            }
        }
        .onChange(of: start) { _, nw in b = nw }
    }
}

/// 작업 → 예약: a 보관함 entry at the top (with how many are kept there), a button to add one, and every schedule (tap to change it;
/// swipe it to the left to put it in the archive at once, with 실행 취소 on the message that follows).
struct SchedulesPage: View {
    let model: WorkModel
    let onBack: () -> Void
    let onNew: () -> Void
    let onOpen: (Int64) -> Void
    var onArchive: () -> Void = {}
    @Environment(\.kelo) private var c

    var body: some View {
        SubPage(L(R.string.work_schedules), onBack: onBack) {
            // 보관함: easy to find, at the top
            Button(action: onArchive) {
                HStack(spacing: 8) {
                    Text(L(R.string.schedules_archive_entry)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let kept = model.archive.data?.items.count, kept > 0 {
                        Text("\(kept)").font(KeloFont.body).foregroundStyle(c.muted)
                    }
                    KeloIconView(.chevronRight, size: 16, tint: c.muted)
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                .frame(minHeight: 48)
                .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            PrimaryButton(L(R.string.work_schedule_add), action: onNew)
            if let book = model.schedules.data {
                if book.items.isEmpty {
                    EmptyNote(L(R.string.work_schedules_empty))
                } else {
                    Panel {
                        ForEach(Array(book.items.enumerated()), id: \.element.id) { i, s in
                            if i > 0 { Hairline() }
                            SwipeToTrash(onSwiped: { model.archiveSchedule(s.id) }) {
                                Button { onOpen(s.id) } label: {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(s.name).font(KeloFont.bodyLarge).foregroundStyle(s.enabled ? c.ink : c.muted).lineLimit(2)
                                        let sub = s.enabled ? WorkText.schedule(s, days: book.spans[s.id]) : L(R.string.work_schedule_off)
                                        if !sub.isEmpty { Text(sub).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3) }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.vertical, 8).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            } else if model.schedules.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .onAppear { model.loadArchive(); if model.schedules.data == nil { model.loadSchedules() } }
    }
}

/// 보관한 예약: schedules put away, each restorable until the server removes it. Swipe one to the left: it is deleted for good at
/// once (the message that follows has 실행 취소 for a few seconds; only then does the server delete it).
struct ArchivedSchedulesPage: View {
    let model: WorkModel
    let onBack: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        SubPage(L(R.string.work_archive), onBack: onBack) {
            if let data = model.archive.data {
                if data.items.isEmpty {
                    EmptyNote(L(R.string.work_archive_empty))
                } else {
                    MutedText(L(R.string.work_archive_note, data.keepDays), small: true)
                    Panel {
                        ForEach(Array(data.items.enumerated()), id: \.element.id) { i, s in
                            if i > 0 { Hairline() }
                            SwipeToTrash(onSwiped: { model.deleteArchived(s.id) }) {
                                ViewThatFits(in: .horizontal) {
                                    HStack(spacing: 8) { archived(s); restore(s) }
                                    VStack(alignment: .leading, spacing: 6) { archived(s); restore(s) }
                                }
                                .padding(.vertical, 6)
                            }
                        }
                    }
                }
            } else if model.archive.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .onAppear { model.loadArchive() }
        .onDisappear { model.commitArchivedErases() }
    }

    private func archived(_ s: Schedule) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(s.name).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
            Text(WorkText.schedule(s)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            if let p = s.purgeAt { Text(L(R.string.work_archive_until, WorkText.rel(p))).font(KeloFont.label).foregroundStyle(c.muted) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func restore(_ s: Schedule) -> some View { SecondaryButton(L(R.string.work_archive_restore)) { model.restoreSchedule(s.id) } }
}

extension WorkModel {
    /// A task's grants (allowed at setup), or none when they cannot be read.
    func grants(_ call: (KeloApi) async throws -> ScheduleGrantList) async -> [ScheduleGrant] {
        guard let api = api() else { return [] }
        return (try? await call(api).items) ?? []
    }

    /// Takes a grant back under its own task; true when it went.
    func revokeGrant(_ g: ScheduleGrant) async -> Bool {
        guard let api = api() else { return false }
        do { try await api.revokeScheduleGrant(g); Haptics.success(); return true } catch { Haptics.warning(); return false }
    }
}
