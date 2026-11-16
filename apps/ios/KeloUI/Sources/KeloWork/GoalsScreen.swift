import SwiftUI
import KeloCore
import KeloShared

/// A goal's own page in the 다짐 tab's stack.
struct GoalRoute: Hashable { let id: Int64 }

/// 다짐 tab (Android goals/GoalsScreen.kt): 해볼 만한 다짐 (goals Kelo found in the conversations: add / not for me) on top, then
/// 진행 중 (nearest due date first) and 끝남 (folded until opened). Each goal has an optional tag, due date and progress, and shows how
/// far its plan is. The button at the bottom adds one; a tap opens its page; swiping it away removes it with an undo offered for a
/// few seconds before it is deleted on the server. Entry point the shell uses.
public struct GoalsScreen: View {
    @Environment(AppModel.self) private var app

    public init() {}

    public var body: some View { GoalsPage(app: app) }
}

private struct GoalsPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.scenePhase) private var phase
    @State private var model: GoalsModel
    @State private var editing: GoalItem?
    @State private var adding = false
    @State private var showDone = false
    @State private var undo: UndoOffer?
    @State private var added: String?
    /// A goal with schedules on, swiped away: asks whether to turn them off too before it goes (cancel keeps the goal)
    @State private var askSchedules: GoalItem?
    @Environment(\.kelo) private var c

    struct UndoOffer: Equatable, Identifiable {
        let id = UUID()
        let goal: GoalItem
    }

    init(app: AppModel) {
        _model = State(initialValue: GoalsModel(api: { [weak app] in app?.api }, cache: app.cache))
    }

    var body: some View {
        NavigationStack(path: app.router.path(.goals)) {
            list
                .keloShellInsets()
                .navigationDestination(for: GoalRoute.self) { r in
                    if let g = model.state.goals.first(where: { $0.id == r.id }) {
                        GoalDetailPage(goal: g, api: { [weak app] in app?.api }, onBack: { pop() }, onEdit: { editing = $0 }, onOpenChat: {
                            pop()
                            app.router.open(.chat)
                        })
                        .environment(app)
                    } else {
                        SubPage(L(R.string.tab_goals), onBack: { pop() }) { SkeletonRows(rows: 3) }
                    }
                }
                .keloHideNavigationBar()
        }
        .onAppear { model.load() }
        .onChange(of: phase) { _, p in if p == .active { model.load() } else if p == .background { model.commitAll() } }
        .onDisappear { model.commitAll() }
        .goalRefresh(every: app.router.shows(.goals) ? GoalsRules.refreshMs(model.state.goals) : nil) { model.load() }
        .confirmationDialog(askSchedules.map { P(R.plurals.goals_delete_schedules_q, $0.schedulesOn, $0.schedulesOn) } ?? "",
                            isPresented: Binding(get: { askSchedules != nil }, set: { if !$0 { askSchedules = nil } }),
                            titleVisibility: .visible, presenting: askSchedules) { g in
            Button(L(R.string.goals_delete_schedules_off), role: .destructive) { askSchedules = nil; remove(g, pauseSchedules: true, asked: true) }
            Button(L(R.string.goals_delete_schedules_keep)) { askSchedules = nil; remove(g, pauseSchedules: false, asked: true) }
            Button(L(R.string.cancel), role: .cancel) { askSchedules = nil }
        } message: { _ in
            Text(L(R.string.goals_delete_schedules_body))
        }
        .keloSheet(isPresented: $adding) {
            GoalSheet(goal: nil, onClose: { adding = false }) { title, note, tag, done in model.add(title: title, note: note, category: tag, onAdded: done) }
        }
        .keloSheet(item: $editing) { g in
            GoalSheet(goal: g, onClose: { editing = nil }) { title, note, tag, done in
                model.update(g, title: title, note: note, category: tag, onSaved: done)
            }
        }
    }

    private func pop() {
        if app.router.depth(.goals) > 0 { app.router.paths[.goals]?.removeLast() }
        model.load()
    }

    private var list: some View {
        let s = model.state
        let today = LocalDay.today()
        return ScrollViewReader { proxy in
            List {
                Color.clear.frame(height: 0).listRowInsets(EdgeInsets()).listRowBackground(c.bg).listRowSeparator(.hidden).id("top")
                if s.failed {
                    ErrorText(L(R.string.connect_failed), small: true).plainRow(c)
                }
                if !s.shownSuggestions.isEmpty {
                    GroupHeading(text: L(R.string.goals_suggest_title), count: s.shownSuggestions.count).plainRow(c)
                    ForEach(s.shownSuggestions) { sug in
                        SuggestionCard(s: sug, onAdd: {
                            Haptics.success()
                            model.acceptSuggestion(sug) { added = L(R.string.goals_suggest_added, sug.title) }
                        }, onNo: { Haptics.tick(); model.dismissSuggestion(sug) })
                        .plainRow(c)
                    }
                }
                GroupHeading(text: L(R.string.goals_open), count: s.tracking.count).plainRow(c)
                if s.loaded && s.tracking.isEmpty {
                    MutedText(L(R.string.goals_empty), small: true).padding(.horizontal, 8).plainRow(c)
                }
                rows(s.tracking, today: today)
                if !s.done.isEmpty {
                    GroupHeading(text: L(R.string.goals_done), count: s.done.count) {
                        LinkButton(L(showDone ? R.string.goals_hide : R.string.goals_show)) { showDone.toggle() }
                    }
                    .plainRow(c)
                    if showDone { rows(s.done, today: today) }
                }
                Color.clear.frame(height: 88).plainRow(c)
            }
            .listStyle(.plain)
            .environment(\.defaultMinListRowHeight, 0)   // the zero-height "top" anchor row must not take a full row
            .scrollContentBackground(.hidden)
            .background(c.bg)
            .keloTopScroll(root: true)
            .refreshable { model.load() }
            .onTabReselect(.goals) { withAnimation { proxy.scrollTo("top", anchor: .top) } }
        }
        .overlay(alignment: .bottomTrailing) { addButton }
        .overlay(alignment: .bottom) { bottomNotes }
    }

    @ViewBuilder private func rows(_ goals: [GoalItem], today: LocalDay) -> some View {
        ForEach(goals) { g in
            GoalRow(goal: g, today: today, onToggle: {
                if g.isDone { Haptics.tick() } else { Haptics.success() }
                model.toggle(g)
            }, onOpen: { app.router.paths[.goals, default: NavigationPath()].append(GoalRoute(id: g.id)) })
            .plainRow(c)
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                Button(role: .destructive) { remove(g) } label: { Label(L(R.string.delete), systemImage: KeloIcon.trash.rawValue) }
            }
            .contextMenu {
                Button(role: .destructive) { remove(g) } label: { Label(L(R.string.delete), systemImage: KeloIcon.trash.rawValue) }
            }
        }
    }

    private func remove(_ g: GoalItem, pauseSchedules: Bool = false, asked: Bool = false) {
        // schedules on: the question comes first (before the undo offer); the choice goes with the real delete
        if !asked && GoalsRules.asksAboutSchedules(g) { askSchedules = g; return }
        Haptics.tick()
        // an earlier undo offer ends: that one is deleted now
        if let prev = undo { model.commitRemove(prev.goal) }
        model.startRemove(g, pauseSchedules: pauseSchedules)
        undo = UndoOffer(goal: g)
    }

    private var addButton: some View {
        Button { adding = true } label: {
            HStack(spacing: 8) {
                KeloIconView(.plus, size: 20, tint: c.onAccent)
                Text(L(R.string.goals_add)).font(KeloFont.button).foregroundStyle(c.onAccent).lineLimit(2)
            }
            .padding(.horizontal, 18).padding(.vertical, 14)
            .background(RoundedRectangle(cornerRadius: 16).fill(c.accent).shadow(color: c.scrim.opacity(0.18), radius: 6, y: 3))
        }
        .buttonStyle(PressShrinkStyle())
        .padding(.trailing, 16).padding(.bottom, 16)
    }

    @ViewBuilder private var bottomNotes: some View {
        VStack(spacing: 8) {
            if let u = undo {
                HStack(spacing: 12) {
                    Text(L(R.string.goals_removed, u.goal.title)).font(KeloFont.body).foregroundStyle(c.bg).lineLimit(3)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button(L(R.string.undo)) {
                        model.undoRemove(u.goal)
                        undo = nil
                    }
                    .font(KeloFont.button).foregroundStyle(c.accent)
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                .background(RoundedRectangle(cornerRadius: 8).fill(c.ink))
                .task(id: u.id) {
                    try? await Task.sleep(nanoseconds: 6_000_000_000)
                    if undo?.id == u.id { model.commitRemove(u.goal); undo = nil }
                }
            } else if let a = added {
                Text(a).font(KeloFont.body).foregroundStyle(c.bg).fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 8).fill(c.ink))
                    .task(id: a) {
                        try? await Task.sleep(nanoseconds: 3_500_000_000)
                        if added == a { added = nil }
                    }
            } else {
                StaleHint(visible: model.state.offline) { model.load() }
            }
        }
        .frame(maxWidth: 560)
        .padding(.horizontal, 12).padding(.bottom, 80)
    }
}

private extension View {
    func plainRow(_ c: KeloColors) -> some View {
        listRowInsets(EdgeInsets(top: 0, leading: 8, bottom: 0, trailing: 12)).listRowBackground(c.bg).listRowSeparator(.hidden)
    }
}

private struct GroupHeading<Trailing: View>: View {
    let text: String
    let count: Int
    let trailing: Trailing
    @Environment(\.kelo) private var c

    init(text: String, count: Int, @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.text = text; self.count = count; self.trailing = trailing()
    }

    var body: some View {
        HStack(spacing: 0) {
            Text(text).font(KeloFont.titleSmall).foregroundStyle(c.accent)
            Text(" · \(count)").font(KeloFont.titleSmall).foregroundStyle(c.muted)
            Spacer(minLength: 8)
            trailing
        }
        .padding(.leading, 8).padding(.trailing, 4).padding(.top, 16).padding(.bottom, 4)
        .accessibilityAddTraits(.isHeader)
    }
}

/// One goal: its box, the title, and under it the next step, the note, tag, plan, due date and progress.
private struct GoalRow: View {
    let goal: GoalItem
    let today: LocalDay
    let onToggle: () -> Void
    let onOpen: () -> Void
    /// Shown checked at once; the server's answer then moves the goal to its section.
    @State private var checked: Bool?
    @State private var bump = false
    @Environment(\.accessibilityReduceMotion) private var reduce
    @Environment(\.kelo) private var c

    var body: some View {
        let g = goal
        let isChecked = checked ?? g.isDone
        let meta = GoalMetaCodec.parse(g.note)
        HStack(alignment: .top, spacing: 4) {
            Button {
                checked = !g.isDone
                if !g.isDone && !reduce {
                    withAnimation(.spring(response: 0.18, dampingFraction: 0.5)) { bump = true }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { withAnimation(.spring()) { bump = false } }
                }
                onToggle()
            } label: {
                Image(systemName: isChecked ? "checkmark.square.fill" : "square").font(.system(size: 22))
                    .foregroundStyle(isChecked ? c.success : c.muted)
                    .scaleEffect(bump ? 1.2 : 1)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L(R.string.goals_mark_done))
            .accessibilityAddTraits(isChecked ? .isSelected : [])
            VStack(alignment: .leading, spacing: 4) {
                Text(g.title).font(KeloFont.bodyLarge).foregroundStyle(isChecked ? c.muted : c.ink).strikethrough(isChecked).lineLimit(4)
                if !g.isDone, let next = g.nextStep {
                    Text(L(R.string.goals_next, next)).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(3)
                }
                if !meta.note.isEmpty { Text(meta.note).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3) }
                FlowLayout(spacing: 6, lineSpacing: 4) {
                    if !g.category.isEmpty { GoalPill(text: "#" + g.category, tone: c.muted) }
                    if g.plan.total > 0 { GoalPill(text: L(R.string.goals_plan_count, g.plan.done, g.plan.total), tone: c.success) }
                    switch GoalsRules.activity(g) {
                    case .working: GoalPill(text: L(R.string.goals_working), tone: c.success)
                    case .asking: GoalPill(text: L(R.string.goals_asking), tone: c.accent)
                    case nil: EmptyView()
                    }
                    if let d = meta.due {
                        let late = !g.isDone && GoalsRules.overdue(d, today: today)
                        GoalPill(text: L(late ? R.string.goals_due_late : R.string.goals_due_by, WorkText.day(d)), tone: late ? c.danger : c.accent)
                    }
                }
                if let p = meta.progress {
                    HStack(spacing: 8) {
                        ThinProgress(value: Double(p) / 100, tone: c.success)
                        Text(L(R.string.goals_progress_value, p)).font(KeloFont.label).foregroundStyle(c.muted)
                    }
                }
            }
            .padding(.top, 11).padding(.trailing, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 8).padding(.horizontal, 4)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .accessibilityAddTraits(.isButton)
        .onChange(of: goal.status) { _, _ in checked = nil }
    }
}

struct GoalPill: View {
    let text: String
    let tone: Color
    var body: some View {
        Text(text).font(KeloFont.label).foregroundStyle(tone).lineLimit(2)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 6).fill(tone.opacity(0.1)))
    }
}

/// A goal Kelo found in the conversations: what, and why it fits; add makes it a goal, not for me waves it off for good.
private struct SuggestionCard: View {
    let s: GoalSuggestion
    let onAdd: () -> Void
    let onNo: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top, spacing: 8) {
                KeloIconView(.spark, size: 18, tint: c.accent).padding(.top, 2)
                Text(s.title).font(.system(.callout, weight: .semibold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            if !s.reason.isEmpty {
                Text(s.reason).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true).padding(.leading, 26)
            }
            HStack {
                Spacer(minLength: 0)
                Button(L(R.string.goals_suggest_no), action: onNo).font(KeloFont.button).foregroundStyle(c.muted).buttonStyle(.plain).padding(8)
                Button(L(R.string.goals_suggest_add), action: onAdd).font(KeloFont.button).foregroundStyle(c.accent).buttonStyle(.plain).padding(8)
            }
        }
        .padding(.leading, 14).padding(.trailing, 8).padding(.top, 12).padding(.bottom, 4)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.card))
        .padding(.horizontal, 4).padding(.vertical, 4)
    }
}

/// Adding or editing a goal: the title, a note, a tag, an optional due date and the progress.
private struct GoalSheet: View {
    let goal: GoalItem?
    let onClose: () -> Void
    let onSave: (String, String, String, @escaping () -> Void) -> Void
    @State private var title: String
    @State private var note: String
    @State private var tag: String
    @State private var due: LocalDay?
    @State private var progress: Int?
    @State private var picking = false
    @State private var saving = false
    @Environment(\.kelo) private var c

    init(goal: GoalItem?, onClose: @escaping () -> Void, onSave: @escaping (String, String, String, @escaping () -> Void) -> Void) {
        self.goal = goal; self.onClose = onClose; self.onSave = onSave
        let meta = GoalMetaCodec.parse(goal?.note ?? "")
        _title = State(initialValue: goal?.title ?? "")
        _note = State(initialValue: meta.note)
        _tag = State(initialValue: goal?.category ?? "")
        _due = State(initialValue: meta.due)
        _progress = State(initialValue: meta.progress)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(goal == nil ? R.string.goals_add : R.string.goals_edit), onClose: onClose)
                KeloTextField(L(R.string.goals_title_placeholder), text: $title, maxLength: 200)
                KeloTextField(L(R.string.goals_note_placeholder), text: $note, axis: .vertical, maxLength: 1800)
                KeloTextField(L(R.string.goals_tag_placeholder), text: $tag, maxLength: 40)
                FlowLayout(spacing: 8, lineSpacing: 6) {
                    HStack(spacing: 10) {
                        KeloIconView(.calendar, size: 20, tint: c.muted)
                        Text(due.map { L(R.string.goals_due_by, WorkText.day($0)) } ?? L(R.string.goals_due_none)).font(KeloFont.body).foregroundStyle(c.ink)
                    }
                    if due != nil { LinkButton(L(R.string.goals_due_clear)) { due = nil } }
                    LinkButton(L(R.string.goals_due_pick)) { picking.toggle() }
                }
                if picking {
                    DatePicker("", selection: Binding(get: { (due ?? LocalDay.today().adding(days: 7)).date() },
                                                      set: { due = LocalDay(date: $0) }), displayedComponents: .date)
                        .labelsHidden().datePickerStyle(.graphical)
                        .environment(\.locale, appLocale())
                }
                VStack(alignment: .leading, spacing: 4) {
                    FlowLayout(spacing: 8, lineSpacing: 4) {
                        Text(L(R.string.goals_progress)).font(KeloFont.body).foregroundStyle(c.ink)
                        Text(progress.map { L(R.string.goals_progress_value, $0) } ?? L(R.string.goals_progress_none)).font(KeloFont.small).foregroundStyle(c.muted)
                        if progress != nil { LinkButton(L(R.string.goals_due_clear)) { progress = nil } }
                    }
                    Slider(value: Binding(get: { Double(progress ?? 0) }, set: { progress = GoalsRules.snapProgress($0) }), in: 0...100, step: 10)
                        .tint(c.accent)
                        .accessibilityLabel(L(R.string.goals_progress))
                }
                PrimaryButton(L(goal == nil ? R.string.goals_add_button : R.string.save),
                              enabled: !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !saving, busy: saving) {
                    saving = true
                    let encoded = GoalMetaCodec.encode(GoalMeta(note: note, due: due, progress: progress))
                    onSave(title.trimmingCharacters(in: .whitespacesAndNewlines), encoded, GoalsRules.tag(tag)) { saving = false; onClose() }
                }
            }
            .padding(.horizontal, 20).padding(.bottom, 28)
        }
        .task(id: saving) {
            guard saving else { return }
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            saving = false
        }
    }
}
