import SwiftUI
import KeloCore
import KeloShared

/// One goal's own page (Android goals/GoalDetailPage.kt): what the user told Kelo for it (a question they chose not to answer
/// shows as "not said"), the plan (the user's own steps can be ticked; Kelo's show where they stand), numbers with a small chart,
/// the schedules and documents made for it, and what happened so far. "Kelo에게 이어서 맡기기" has Kelo pick it up in the chat.
struct GoalDetailPage: View {
    let goal: GoalItem
    let onBack: () -> Void
    let onEdit: (GoalItem) -> Void
    let onOpenChat: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.scenePhase) private var phase
    @State private var model: GoalDetailModel
    @Environment(\.kelo) private var c

    init(goal: GoalItem, api: @escaping () -> KeloApi?, onBack: @escaping () -> Void, onEdit: @escaping (GoalItem) -> Void, onOpenChat: @escaping () -> Void) {
        self.goal = goal; self.onBack = onBack; self.onEdit = onEdit; self.onOpenChat = onOpenChat
        _model = State(initialValue: GoalDetailModel(goalId: goal.id, api: api))
    }

    var body: some View {
        let w = model.workspace
        // the title, note and tag as the list has them (an edit shows at once); the plan's state from the page's own load
        var g = goal
        if let wg = w?.goal { g.plan = wg.plan; g.asking = wg.asking; g.working = wg.working }
        let meta = GoalMetaCodec.parse(g.note)
        let shown = g
        return SubPage(g.title, onBack: onBack, actions: {
            Button(L(R.string.goal_edit)) { onEdit(shown) }.font(KeloFont.button).foregroundStyle(c.accent).buttonStyle(.plain).padding(.horizontal, 8)
        }) {
            if !meta.note.isEmpty { Text(meta.note).font(KeloFont.body).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
            FlowLayout(spacing: 6, lineSpacing: 6) {
                if g.plan.total > 0 { chip(L(R.string.goals_plan_count, g.plan.done, g.plan.total), c.success) }
                if let d = meta.due { chip(L(R.string.goals_due_by, WorkText.day(d)), c.accent) }
                switch GoalsRules.activity(g) {
                case .working: chip(L(R.string.goals_working), c.success)
                case .asking: chip(L(R.string.goals_asking), c.accent)
                case nil: EmptyView()
                }
            }
            PrimaryButton(L(R.string.goal_resume), enabled: !model.resuming && !g.isDone) {
                Haptics.success()
                model.resume(onStarted: onOpenChat)
            }
            if let r = model.resumed { MutedText(r, small: true) }
            if model.failed { ErrorText(L(R.string.connect_failed), small: true) }
            if let w { content(w) }
        }
        .onAppear { model.load() }
        .onChange(of: phase) { _, p in if p == .active { model.load() } }
        .goalRefresh(every: GoalsRules.refreshMs([shown])) { model.load() }
    }

    @ViewBuilder private func content(_ w: GoalWorkspace) -> some View {
        heading(L(R.string.goal_facts))
        if w.facts.isEmpty { MutedText(L(R.string.goal_facts_empty), small: true) }
        ForEach(Array(w.facts.enumerated()), id: \.offset) { _, f in
            FactRow(label: f.key, value: f.skipped ? L(R.string.goal_fact_skipped) : f.value, skipped: f.skipped)
        }

        heading(L(R.string.goal_plan))
        if w.steps.isEmpty { MutedText(L(R.string.goal_plan_empty), small: true) }
        ForEach(w.steps) { s in
            var shown = s
            let _ = model.ticking[s.id].map { shown.status = $0 }
            StepRow(step: shown) { Haptics.tick(); model.tick(s) }
        }

        if !w.metrics.isEmpty {
            heading(L(R.string.goal_numbers))
            ForEach(w.metrics) { m in MetricCard(metric: m) }
        }
        if !w.schedules.isEmpty {
            heading(L(R.string.goal_schedules))
            ForEach(w.schedules) { s in
                // a schedule opens on the work page (Android: the goal's schedule row)
                Button { app.router.openWork(.schedule(s.id)) } label: {
                VStack(alignment: .leading, spacing: 1) {
                    Text(s.name).font(KeloFont.body).foregroundStyle(c.ink).lineLimit(3)
                    let line = [s.description.isEmpty ? nil : s.description, s.enabled ? nil : L(R.string.goal_schedule_off)]
                        .compactMap { $0 }.joined(separator: " · ")
                    if !line.isEmpty { Text(line).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
                }
                .padding(.vertical, 2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        if !w.documents.isEmpty {
            heading(L(R.string.goal_documents))
            ForEach(w.documents) { d in
                HStack(spacing: 8) {
                    KeloIconView(.file, size: 18, tint: c.muted)
                    Text(TokenText.fit(d.title.isEmpty ? (d.path.split(separator: "/").last.map(String.init) ?? d.path) : d.title))
                        .font(KeloFont.body).foregroundStyle(d.exists ? c.ink : c.muted).lineLimit(2)
                }
            }
        }
        if !w.log.isEmpty {
            heading(L(R.string.goal_log))
            ForEach(w.log.prefix(20)) { e in
                HStack(alignment: .top, spacing: 10) {
                    Text(WorkText.day(LocalDay(seconds: e.at))).font(KeloFont.label).foregroundStyle(c.muted).lineLimit(1).padding(.top, 2)
                    Text(e.text).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func heading(_ text: String) -> some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.accent).padding(.top, 12).accessibilityAddTraits(.isHeader)
    }

    private func chip(_ text: String, _ tone: Color) -> some View {
        Text(text).font(KeloFont.label).foregroundStyle(tone).lineLimit(2)
            .padding(.horizontal, 8).padding(.vertical, 3).background(RoundedRectangle(cornerRadius: 6).fill(tone.opacity(0.12)))
    }
}

private struct StepRow: View {
    let step: GoalStep
    let onTick: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let s = step
        HStack(alignment: .top, spacing: 4) {
            if s.mine {
                Button(action: onTick) {
                    Image(systemName: s.isDone ? "checkmark.square.fill" : "square").font(.system(size: 22))
                        .foregroundStyle(s.isDone ? c.success : c.muted).frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L(R.string.goal_mark_step))
                .accessibilityAddTraits(s.isDone ? .isSelected : [])
            } else {
                KeloIconView(s.isDone ? .check : .spark, size: 20, tint: s.isDone ? c.success : c.accent).frame(width: 44, height: 44)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(s.title).font(KeloFont.bodyLarge).foregroundStyle(s.isDone ? c.muted : c.ink).strikethrough(s.isDone)
                    .fixedSize(horizontal: false, vertical: true)
                if !s.detail.isEmpty { Text(s.detail).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(4) }
                FlowLayout(spacing: 6, lineSpacing: 4) {
                    GoalPill(text: L(s.mine ? R.string.goal_owner_me : R.string.goal_owner_kelo), tone: s.mine ? c.muted : c.accent)
                    if !s.mine {
                        GoalPill(text: L(s.status == "done" ? R.string.goal_status_done : s.status == "doing" ? R.string.goal_status_doing : R.string.goal_status_todo),
                                 tone: s.isDone ? c.success : c.muted)
                    }
                    if let d = LocalDay(parsing: s.due) { GoalPill(text: L(R.string.goal_due, WorkText.day(d)), tone: c.muted) }
                }
            }
            .padding(.top, 11).padding(.trailing, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .contentShape(Rectangle())
        .onTapGesture { if s.mine { onTick() } }
    }
}

private struct MetricCard: View {
    let metric: GoalMetric
    @Environment(\.kelo) private var c

    var body: some View {
        let m = metric
        let last = m.values.last?.value
        VStack(alignment: .leading, spacing: 6) {
            FlowLayout(spacing: 8, lineSpacing: 2) {
                Text(m.name).font(.system(.subheadline, weight: .semibold)).foregroundStyle(c.ink)
                Text(last != nil ? L(R.string.goal_number_latest, GoalDetailRules.withUnit(last, m.unit)) : L(R.string.goal_number_none))
                    .font(KeloFont.small).foregroundStyle(last != nil ? c.ink : c.muted)
            }
            let points = GoalDetailRules.chart(m)
            if points.count >= 2 {
                let target = GoalDetailRules.targetLevel(m)
                Canvas { ctx, size in
                    let pad: CGFloat = 6
                    func at(_ p: (Double, Double)) -> CGPoint {
                        CGPoint(x: pad + p.0 * (size.width - 2 * pad), y: size.height - pad - p.1 * (size.height - 2 * pad))
                    }
                    if let t = target {
                        let y = size.height - pad - t * (size.height - 2 * pad)
                        var line = Path(); line.move(to: CGPoint(x: pad, y: y)); line.addLine(to: CGPoint(x: size.width - pad, y: y))
                        ctx.stroke(line, with: .color(c.success), style: StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                    }
                    var path = Path()
                    for (i, p) in points.enumerated() { if i == 0 { path.move(to: at(p)) } else { path.addLine(to: at(p)) } }
                    ctx.stroke(path, with: .color(c.accent), lineWidth: 2)
                    let end = at(points.last!)
                    ctx.fill(Path(ellipseIn: CGRect(x: end.x - 3.5, y: end.y - 3.5, width: 7, height: 7)), with: .color(c.accent))
                }
                .frame(height: 64)
                .accessibilityLabel(m.values.map { GoalDetailRules.number($0.value) }.joined(separator: ", "))
            }
            FlowLayout(spacing: 12, lineSpacing: 2) {
                if let s = m.start { Text(L(R.string.goal_number_start, GoalDetailRules.withUnit(s, m.unit))).font(KeloFont.label).foregroundStyle(c.muted) }
                if let t = m.target { Text(L(R.string.goal_number_target, GoalDetailRules.withUnit(t, m.unit))).font(KeloFont.label).foregroundStyle(c.success) }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
    }
}
