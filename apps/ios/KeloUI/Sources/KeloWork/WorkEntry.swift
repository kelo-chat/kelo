import SwiftUI
import KeloCore
import KeloShared

/// The work page's sub pages.
enum WorkPage: Hashable {
    case task(Int64)
    case job(String)
    /// nil: a new schedule
    case schedule(Int64?)
    case browsers
    case schedules
    /// 보관한 예약
    case archived
    case watch(Int64)
}

/// The work page over everything, opened from the floating header (Android work/WorkScreen.kt): one timeline of everything the
/// agent does. 지금 하는 일 (what runs now, jobs nested under the task that handed them off, each with its live browser one tap away),
/// 답이 필요해요 (approvals, jobs stopped for the user, follow-ups on hold), 지켜보는 중 (watchers), 다가오는 일 (the schedules by
/// day and time) and 지난 일 (finished work by day, with a filter). Reads `app.router.workStart` once. Entry point the shell uses.
public struct WorkScreen: View {
    let onClose: () -> Void
    /// Sitting in the second pane beside the chat: a title, no back arrow (there is nothing to go back to).
    let docked: Bool
    @Environment(AppModel.self) private var app
    private var activity: AgentActivityModel { app.activity }
    @Environment(\.scenePhase) private var phase
    @State private var model: WorkModel
    @State private var offersModel: OffersModel
    @State private var followModel: FollowModel
    @State private var path: [WorkPage] = []
    @State private var jumpTo: String?
    @Environment(\.kelo) private var c

    /// `api`: where the signed-in API comes from (the app model's, read when a call is made).
    public init(api: @escaping () -> KeloApi?, docked: Bool = false, onClose: @escaping () -> Void) {
        self.onClose = onClose
        self.docked = docked
        _model = State(initialValue: WorkModel(api: api))
        _offersModel = State(initialValue: OffersModel(api: api))
        _followModel = State(initialValue: FollowModel(api: api))
    }

    public var body: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                if docked {
                    Text(L(R.string.tab_work)).font(KeloFont.titleLarge).foregroundStyle(c.ink).lineLimit(2)
                        .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading).padding(.horizontal, 16)
                        .accessibilityAddTraits(.isHeader)
                } else {
                    BackTitleBar(L(R.string.tab_work), onBack: onClose)
                }
                WorkTimeline(model: model, offersModel: offersModel, followModel: followModel, jumpTo: $jumpTo, push: { path.append($0) }, onOpenLive: openLive, onOpenChat: openChat,
                             onOpenEntry: { id in onClose(); app.router.open(.entry(id)) })
            }
            .background(c.bg.ignoresSafeArea())
            .keloHideNavigationBar()
            .navigationDestination(for: WorkPage.self) { page in
                destination(page).environment(app)
            }
        }
        .workToast($model.toast)
        .onAppear {
            useStart()
            model.loadWork()
            Task { await offersModel.load(); await followModel.load() }
        }
        .onChange(of: app.router.workStart) { _, _ in useStart() }
        .onChange(of: phase) { _, p in if p == .active { model.loadWork(); Task { await offersModel.load(); await followModel.load() } } }
        // new things show up while the page is open: often while the agent works, now and then otherwise
        .task(id: busy) {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: busy ? 4_000_000_000 : 20_000_000_000)
                if Task.isCancelled { break }
                model.loadWork()
                await offersModel.load()
                await followModel.load()
            }
        }
        .onChange(of: activity.workChanged) { _, _ in model.refreshLive() }
    }

    private var busy: Bool {
        let s = app.activity.status
        return s?.busy == true || app.activity.following || (s?.pendingApprovals ?? 0) > 0
    }

    private func openLive(_ id: String) { app.router.openLive(id) }

    private func openChat() {
        onClose()
        app.router.open(.chat)
    }

    /// A place a notification asked for, used once.
    private func useStart() {
        guard let start = app.router.workStart else { return }
        app.router.workStart = nil
        switch start {
        case .task(let id): path = [.task(id)]
        case .schedule(let id): path = [.schedule(id)]
        case .approvals: path = []; jumpTo = WorkTimeline.asks
        case .schedules: path = []; jumpTo = WorkTimeline.upcoming
        case .activity: path = []; jumpTo = WorkTimeline.now
        case .watchers: path = []; jumpTo = WorkTimeline.watch
        }
    }

    @ViewBuilder private func destination(_ page: WorkPage) -> some View {
        let back = { if !path.isEmpty { path.removeLast() } }
        switch page {
        case .task(let id): TaskPage(model: model, id: id, onBack: back, onOpenLive: openLive)
        case .job(let id): JobPage(model: model, id: id, onBack: back, onOpenLive: openLive, onOpenTask: { path.append(.task($0)) })
        case .schedule(let id): ScheduleEditPage(model: model, id: id, onBack: back)
        case .browsers: OpenBrowsersPage(model: model, onBack: back, onOpenLive: openLive)
        case .schedules: SchedulesPage(model: model, onBack: back, onNew: { path.append(.schedule(nil)) }, onOpen: { path.append(.schedule($0)) },
                                       onArchive: { path.append(.archived) })
        case .archived: ArchivedSchedulesPage(model: model, onBack: back)
        case .watch(let id): WatcherPage(model: model, id: id, onBack: back)
        }
    }
}

/// The timeline itself.
struct WorkTimeline: View {
    static let now = "sec:now"
    static let asks = "sec:asks"
    static let upcoming = "sec:upcoming"
    static let watch = "sec:watch"
    static let past = "sec:past"
    static let openTabs = "sec:opentabs"

    let model: WorkModel
    let offersModel: OffersModel
    let followModel: FollowModel
    @Binding var jumpTo: String?
    let push: (WorkPage) -> Void
    let onOpenLive: (String) -> Void
    let onOpenChat: () -> Void
    var onOpenEntry: (String) -> Void = { _ in }
    @Environment(AppModel.self) private var app
    @State private var filter: PastFilter = .all
    @State private var showPaused = false
    @State private var showStoppedFollow = false
    @State private var openFollow: String?
    @Environment(\.kelo) private var c

    /// 답이 필요해요: approvals, secure entries waiting for a value, stopped jobs and follow-ups on hold.
    @ViewBuilder
    private func asksSection(_ asks: [Ask]) -> some View {
        SectionTitle(L(R.string.work_asks), count: asks.count, attention: !asks.isEmpty).id(WorkTimeline.asks)
        if model.pending.data != nil && asks.isEmpty { RailNote { EmptyLine(L(R.string.work_asks_empty)) } }
        ForEach(asks, id: \.key) { a in
            RailItem(.ask) {
                switch a {
                case .approval(let p): ApprovalAsk(model: model, approval: p, deliver: a.deliver, onOpenChat: onOpenChat)
                case .job(let w): JobAsk(model: model, worker: w, onOpen: { push(.job(w.id)) }, onOpenLive: onOpenLive)
                case .hold(let f): HoldAsk(model: model, followup: f, busy: model.followupBusy.contains(f.id), onOpenChat: onOpenChat)
                case .entry(let r):
                    EntryAsk(request: r, onOpen: a.chatCard ? { onOpenEntry(r.id) } : nil) {
                        Haptics.tick()
                        model.cancelEntry(r) { kind in app.activity.status = WorkBadge.entryClosed(app.activity.status, kind: kind) }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var offersSection: some View {
        SectionTitle(L(R.string.offers_title), count: offersModel.items.count).id("sec:offers")
        ForEach(offersModel.items) { o in
            RailItem(.ask) {
                OfferCard(
                    offer: o,
                    starting: offersModel.starting == o.id,
                    problem: offersModel.problem?.id == o.id ? offersModel.problem?.start : nil,
                    onStart: {
                        offersModel.start(o) { turnId in
                            app.router.chatFollowTurn = turnId
                            app.router.workOpen = false
                            app.router.go(.chat)
                        }
                    },
                    onDismiss: { reason in
                        offersModel.dismiss(o, reason: reason)
                    }
                )
            }
        }
    }

    /// Kelo가 챙기는 일: hidden while nothing is followed. The stopped ones are folded under one row (Android WorkRows).
    @ViewBuilder
    private var followSection: some View {
        let split = FollowRules.splitStopped(followModel.items)
        SectionTitle(L(R.string.follow_section_title), count: split.live.count).id("sec:follow")
        ForEach(split.live, id: \.key) { item in
            FollowItemRow(item: item) { openFollow = item.key }
        }
        if !split.stopped.isEmpty {
            FollowStoppedToggle(count: split.stopped.count, open: showStoppedFollow) { showStoppedFollow.toggle() }
            if showStoppedFollow {
                ForEach(split.stopped, id: \.key) { item in
                    FollowItemRow(item: item) { openFollow = item.key }
                }
            }
        }
    }

    var body: some View {
        let today = LocalDay.today()
        let taskItems = model.tasks.data?.items ?? []
        let workerItems = model.workers.data?.items ?? []
        let nested = Timeline.nest(taskItems, workerItems)
        let now = Timeline.now(nested)
        let asks = Timeline.asks(pending: model.pending.data ?? [], workers: workerItems, followups: model.followups.data?.items ?? [],
                                 entries: model.entries.data ?? [])
        let book = model.schedules.data
        let plan = Agenda.build(book?.items ?? [])
        let past = Timeline.past(nested, filter: filter, today: today)
        let tabs = model.browser.data?.items ?? []
        let openTabs = Timeline.openTabs(tabs, now: now, workers: workerItems)
        let watching = Watching.order(model.watchers.data?.items ?? [])

        ScrollViewReader { proxy in
            ScrollView {
                // the sections above "지난 일" are short (what runs, waits, watches and comes next) and sit in a plain stack; only the
                // past, which pages in as it is read, is lazy. One lazy stack over all of it re-placed every row on each pass and,
                // with the page scrolled to its end, kept the main thread busy for minutes.
                VStack(alignment: .leading, spacing: 0) {
                    shortcuts
                    if !asks.isEmpty { asksSection(asks) }
                    if !offersModel.items.isEmpty { offersSection }
                    if !followModel.items.isEmpty { followSection }
                    // ---- 지금 하는 일
                    SectionTitle(L(R.string.work_now), count: now.count).id(WorkTimeline.now)
                    if model.tasks.data == nil && model.tasks.failed {
                        RailNote { ErrorText(L(R.string.connect_failed), small: true) }
                    } else if model.tasks.data == nil {
                        RailNote { SkeletonRows(rows: 2) }
                    } else if now.empty {
                        RailNote { EmptyLine(L(R.string.work_now_empty)) }
                    }
                    ForEach(now.tasks, id: \.key) { node in
                        RailItem(.live) {
                            NowTaskCard(model: model, node: node, tabs: tabs, workers: workerItems, onOpen: { push(.task(node.task.id)) },
                                        onOpenJob: { push(.job($0)) }, onOpenTask: { push(.task($0)) }, onOpenLive: onOpenLive)
                        }
                    }
                    ForEach(now.jobs, id: \.key) { job in
                        RailItem(.live) { JobCard(model: model, job: job, onOpen: { push(.job(job.worker.id)) }, onOpenLive: onOpenLive) }
                    }
                    // one identity per row, prefixed like the other sections' (an `.id` on a ForEach row gave the lazy stack two)
                    ForEach(model.grants.data ?? [], id: \.keyedId) { g in
                        RailItem(.live) { TaskGrantCard(grant: g) { Haptics.warning(); model.revokeGrant(g) } }
                    }

                    // ---- 열린 브라우저: the tabs no running work shows (after what runs, before the asks, as on Android)
                    if !openTabs.isEmpty {
                        SectionTitle(L(R.string.work_open_browsers), count: openTabs.count).id(WorkTimeline.openTabs)
                        ForEach(openTabs, id: \.keyedTabId) { t in
                            RailItem(.live) { OpenTabCard(tab: t) { onOpenLive(t.id) } }
                        }
                    }

                    // ---- 답이 필요해요 (here while nothing waits; at the top, above 지금 하는 일, while something does)
                    if asks.isEmpty { asksSection(asks) }

                    // ---- 지켜보는 중 (only when there are some)
                    if !watching.isEmpty {
                        SectionTitle(L(R.string.work_watching), count: watching.filter(\.enabled).count).id(WorkTimeline.watch)
                        // ids carry a prefix: a LazyVStack drops rows whose ids repeat across its sections (watcher 1, schedule 1)
                        ForEach(watching, id: \.keyedId) { w in
                            RailItem(w.enabled ? .live : .paused) { WatcherRow(model: model, watcher: w, onOpen: { push(.watch(w.id)) }) }
                        }
                    }

                    // ---- 다가오는 일
                    SectionTitle(L(R.string.work_upcoming), count: plan.days.reduce(0) { $0 + $1.entries.count } + plan.unscheduled.count)
                        .id(WorkTimeline.upcoming)
                    if book == nil && model.schedules.failed {
                        RailNote { ErrorText(L(R.string.connect_failed), small: true) }
                    } else if book == nil {
                        RailNote { SkeletonRows(rows: 2) }
                    } else if plan.empty {
                        RailNote { EmptyLine(L(R.string.agent_schedules_empty)) }
                    }
                    agenda(plan, spans: book?.spans ?? [:], today: today)

                    // ---- 지난 일
                    SectionTitle(L(R.string.work_past), count: nil).id(WorkTimeline.past)
                    FlowLayout {
                        ForEach([PastFilter.all, .done, .problems, .automatic], id: \.self) { f in
                            ChoiceChip(filterLabel(f), selected: filter == f) { filter = f }
                        }
                    }
                    .padding(.leading, railWidth).padding(.bottom, 8)
                    if model.tasks.data != nil && past.isEmpty { RailNote { EmptyLine(L(R.string.agent_activity_empty)) } }
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(past.enumerated()), id: \.offset) { _, g in
                            DayCaption(WorkText.dayLabel(g.label, today: today))
                            ForEach(g.items, id: \.key) { node in
                                RailItem(node.problem ? .failed : .done, label: node.task.startedAt > 0 ? WorkText.clock(node.task.startedAt) : nil) {
                                    PastRow(node: node, onOpen: { push(.task(node.task.id)) }, onOpenJob: { push(.job($0)) })
                                }
                                .onAppear { if node.key == past.last?.items.last?.key { model.loadMoreTasks() } }
                            }
                        }
                        if model.tasks.data?.loadingMore == true {
                            ProgressView().tint(c.muted).frame(maxWidth: .infinity).padding(16)
                        }
                    }
                }
                .padding(.leading, 12).padding(.trailing, 16).padding(.top, 4).padding(.bottom, 32)
                .frame(maxWidth: 720).frame(maxWidth: .infinity)
            }
            .refreshable { model.loadWork(); await followModel.load() }
            .keloSheet(isPresented: Binding(get: { openFollow != nil }, set: { if !$0 { openFollow = nil } }), detents: [.large]) {
                if let item = followModel.items.first(where: { $0.key == openFollow }) {
                    FollowPlanSheet(
                        item: item,
                        onClose: { openFollow = nil },
                        onStop: { let k = item.key; Task { await followModel.stop(k) } },
                        onReplan: { let k = item.key; Task { await followModel.replan(k) } },
                        onRemove: { let k = item.key; Task { await followModel.remove(k) } }
                    )
                }
            }
            .onChange(of: jumpTo) { _, key in jump(proxy, key) }
            .onChange(of: jumpReady) { _, _ in jump(proxy, jumpTo) }
            .onAppear { jump(proxy, jumpTo) }
            // looked at: the finished ones count as seen (their dots stay for this visit)
            .task(id: taskItems.first(where: { !$0.running })?.id) {
                guard let top = taskItems.first(where: { !$0.running })?.id else { return }
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                if !Task.isCancelled { model.markSeen(top) }
            }
        }
    }

    /// The section a jump waits for has answered (SectionJump): then it happens once, never earlier and never dropped.
    private var jumpReady: Bool {
        guard let key = jumpTo else { return false }
        func answered<T>(_ l: Loadable<T>) -> Bool { l.data != nil || l.failed }
        return SectionJump.ready(key: key, tasks: answered(model.tasks), perms: answered(model.pending),
                                 schedules: answered(model.schedules), watchers: answered(model.watchers))
    }

    private func jump(_ proxy: ScrollViewProxy, _ key: String?) {
        guard let key, jumpReady else { return }
        withAnimation { proxy.scrollTo(key, anchor: .top) }
        jumpTo = nil
    }

    /// The shortcuts under the title (open browsers, schedules), as chips that wrap in long languages.
    private var shortcuts: some View {
        FlowLayout {
            shortcut(.globe, L(R.string.work_open_browsers)) { push(.browsers) }
            shortcut(.calendar, L(R.string.work_schedules)) { push(.schedules) }
        }
        .padding(.leading, 4).padding(.top, 4)
    }

    private func shortcut(_ icon: KeloIcon, _ text: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 4) {
                KeloIconView(icon, size: 16, tint: c.ink)
                Text(text).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 8).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(c.line, lineWidth: 1))
        }
        .buttonStyle(PressShrinkStyle())
    }

    @ViewBuilder private func agenda(_ plan: Agenda.Plan, spans: [Int64: ScheduleSpan], today: LocalDay) -> some View {
        ForEach(plan.days, id: \.date) { day in
            DayCaption(agendaDay(day.date, today: today))
            ForEach(day.entries, id: \.keyedId) { e in
                RailItem(.planned, label: e.at.map(WorkText.clock)) {
                    AgendaRow(schedule: e.schedule, span: spans[e.schedule.id]) { push(.schedule(e.schedule.id)) }
                }
            }
        }
        if !plan.unscheduled.isEmpty {
            DayCaption(L(R.string.work_upcoming_unscheduled))
            ForEach(plan.unscheduled, id: \.unscheduledId) { s in
                RailItem(.planned) { AgendaRow(schedule: s, span: spans[s.id]) { push(.schedule(s.id)) } }
            }
        }
        // paused by a run: always shown; switched off by hand: folded
        ForEach(plan.paused.filter(\.paused), id: \.pausedId) { s in
            RailItem(.ask) { AgendaRow(schedule: s, span: spans[s.id], onResume: { model.resumeSchedule(s.id) }) { push(.schedule(s.id)) } }
        }
        let off = plan.paused.filter { !$0.paused }
        if !off.isEmpty {
            PausedToggle(count: off.count, open: showPaused) { showPaused.toggle() }
            if showPaused {
                ForEach(off, id: \.offId) { s in
                    RailItem(.paused) { AgendaRow(schedule: s, span: spans[s.id]) { push(.schedule(s.id)) } }
                }
            }
        }
    }

    private func agendaDay(_ d: LocalDay, today: LocalDay) -> String {
        switch Agenda.whenOf(d, today: today) {
        case .today: return L(R.string.work_day_today, WorkText.day(d))
        case .tomorrow: return L(R.string.work_day_tomorrow, WorkText.day(d))
        case .later: return WorkText.day(d)
        }
    }

    private func filterLabel(_ f: PastFilter) -> String {
        switch f {
        case .all: return L(R.string.work_filter_all)
        case .done: return L(R.string.work_filter_done)
        case .problems: return L(R.string.work_filter_problems)
        case .automatic: return L(R.string.work_filter_automatic)
        }
    }
}

/// A browser tab's live view over everything (Android work/LiveOverlay + agent/BrowserPages.kt BrowserTabPage). "owner" means the
/// tab this phone's chat replies work in (found with GET /api/devices). Entry point the shell uses.
public struct LiveOverlay: View {
    let tabId: String
    /// Opened to solve a human check: take the tab over once by itself.
    let takeControl: Bool
    let onClose: () -> Void
    @Environment(AppModel.self) private var app
    @State private var model: LiveOverlayModel
    @Environment(\.kelo) private var c

    public init(api: @escaping () -> KeloApi?, tabId: String, takeControl: Bool = false, onClose: @escaping () -> Void) {
        self.tabId = tabId; self.takeControl = takeControl; self.onClose = onClose
        _model = State(initialValue: LiveOverlayModel(api: api))
    }

    public var body: some View {
        Group {
            if let id = model.resolved, let api = app.api {
                BrowserTabPage(api: api, id: id, takeControl: takeControl, onBack: onClose)
            } else {
                VStack {
                    BackTitleBar(L(R.string.agent_tab_browser), onBack: onClose)
                    Spacer()
                    ProgressView().tint(c.muted)
                    Spacer()
                }
            }
        }
        .background(c.bg.ignoresSafeArea())
        .task(id: tabId) { await model.resolve(tabId) }
    }
}

extension TaskPermission { var keyedId: String { "grant:" + id } }

// Distinct ids for the rows of the work page's one LazyVStack: a repeated id across its sections makes rows vanish.
extension Watcher { var keyedId: String { "watch:\(id)" } }
extension Agenda.Entry { var keyedId: String { "plan:\(at ?? 0):\(schedule.id)" } }
extension BrowserTab { var keyedTabId: String { "tab:" + id } }
extension Schedule {
    var unscheduledId: String { "unscheduled:\(id)" }
    var pausedId: String { "paused:\(id)" }
    var offId: String { "off:\(id)" }
}
