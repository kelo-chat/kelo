import SwiftUI
import KeloCore
import KeloShared

/// A schedule's page (or a new one), like a phone calendar's event (Android agent/SchedulePage.kt): its name, what Kelo should
/// do, a date and a time, how it repeats (never, every day, weekdays, weekends, chosen weekdays, monthly on the date's day, yearly
/// on its date) and an optional last day. The cron / one-time form the server keeps is never shown: a schedule the form cannot
/// show (every few hours, every N minutes, an unusual rule the agent made) is said in words, with a way to replace it by a simple
/// repeat. Saved schedules also have their switch here, their runs, "run now" and delete.
struct ScheduleEditPage: View {
    let model: WorkModel
    let id: Int64?
    let onBack: () -> Void

    var body: some View {
        let s = id.flatMap { i in model.schedules.data?.items.first { $0.id == i } }
        if id != nil && s == nil {
            SubPage(L(R.string.agent_schedule_title), onBack: onBack) {
                if model.schedules.failed { ErrorText(L(R.string.connect_failed)) } else { SkeletonRows(rows: 4) }
            }
            .onAppear { model.loadSchedules() }
        } else {
            ScheduleForm(model: model, schedule: s, span: s.flatMap { model.schedules.data?.spans[$0.id] }, onBack: onBack)
                .id(s?.id ?? -1)
        }
    }
}

private enum DateTarget: String, Identifiable { case date, until; var id: String { rawValue } }

private struct ScheduleForm: View {
    let model: WorkModel
    let schedule: Schedule?
    let span: ScheduleSpan?
    let onBack: () -> Void

    @State private var name: String
    @State private var prompt: String
    /// A schedule the form cannot show stays as it is until the user chooses to replace it.
    @State private var simple: Bool
    @State private var plan: SchedulePlan
    @State private var timeSheet = false
    @State private var repeatSheet = false
    @State private var endSheet = false
    @State private var dateSheet: DateTarget?
    @State private var confirmDelete = false
    @State private var saving = false
    @Environment(\.kelo) private var c

    init(model: WorkModel, schedule: Schedule?, span: ScheduleSpan?, onBack: @escaping () -> Void) {
        self.model = model; self.schedule = schedule; self.span = span; self.onBack = onBack
        let today = LocalDay.today()
        let saved = schedule.flatMap { PlanRules.fromSchedule($0, startsOn: span?.startsOn, endsOn: span?.endsOn, today: today) }
        let first = saved ?? schedule.map { PlanRules.replacing($0, endsOn: span?.endsOn, today: today) }
            ?? SchedulePlan(date: today.adding(days: 1), hour: 9, minute: 0, freq: .none)
        _name = State(initialValue: schedule?.name ?? "")
        _prompt = State(initialValue: schedule?.prompt ?? "")
        _simple = State(initialValue: schedule == nil || saved != nil)
        _plan = State(initialValue: first)
    }

    /// The plan as it will be saved (no last day for a one-time run).
    private var shown: SchedulePlan {
        var p = plan
        if p.freq == .none { p.until = nil }
        return p
    }

    var body: some View {
        let now = LocalMoment.now()
        let problem = simple ? PlanRules.problem(shown, now: now) : nil
        let blank = name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let canSave = !blank && problem == nil && !saving
        let today = now.day

        SubPage(schedule == nil ? L(R.string.agent_schedule_new) : L(R.string.agent_schedule_title), onBack: onBack) {
            label(L(R.string.agent_schedule_name))
            KeloTextField(L(R.string.agent_schedule_name_hint), text: $name, maxLength: 60)
            label(L(R.string.agent_schedule_prompt))
            KeloTextField(L(R.string.agent_schedule_prompt_hint), text: $prompt, axis: .vertical, maxLength: 2000)
                .frame(minHeight: 112, alignment: .top)
            if !simple, let s = schedule {
                label(L(R.string.agent_schedule_repeat))
                VStack(alignment: .leading, spacing: 6) {
                    Text(WorkText.schedule(s, days: span)).font(.system(.callout, weight: .medium)).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    MutedText(L(R.string.sched_custom_note), small: true)
                    LinkButton(L(R.string.sched_custom_convert)) { simple = true }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14).padding(.vertical, 12)
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
            } else {
                VStack(spacing: 0) {
                    PickerRow(label: L(plan.freq == .none ? R.string.agent_schedule_date : R.string.sched_starts), value: WorkText.fullDay(plan.date)) {
                        dateSheet = .date
                    }
                    Hairline()
                    PickerRow(label: L(R.string.agent_schedule_time), value: WorkText.clock(hour: plan.hour, minute: plan.minute)) { timeSheet = true }
                    Hairline()
                    PickerRow(label: L(R.string.sched_repeat), value: WorkText.repeatValue(shown)) { repeatSheet = true }
                    if plan.freq != .none {
                        Hairline()
                        PickerRow(label: L(R.string.sched_end),
                                  value: plan.until.map { L(R.string.sched_end_until, WorkText.dayOrFull($0, today: today)) } ?? L(R.string.sched_end_never)) {
                            endSheet = true
                        }
                    }
                }
                .padding(.horizontal, 14)
                .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                // what it will mean, in the words the agenda uses
                Text(WorkText.plan(shown, now: now)).font(KeloFont.body).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 2)
                switch problem {
                case .noDay: ErrorText(L(R.string.agent_schedule_need_day), small: true)
                case .past: ErrorText(L(R.string.sched_past), small: true)
                case .endBeforeStart: ErrorText(L(R.string.sched_end_before), small: true)
                case nil: EmptyView()
                }
            }
            if let s = schedule, !s.timezone.isEmpty, s.timezone != TimeZone.current.identifier {
                MutedText(L(R.string.agent_schedule_zone, s.timezone), small: true)
            }
            if let s = schedule { ScheduleStateBlock(model: model, schedule: s) }
            if blank { MutedText(L(R.string.sched_save_hint), small: true) }
            PrimaryButton(L(R.string.save), enabled: canSave, busy: saving) { save(now: now) }
                .padding(.top, 8)
            if let s = schedule {
                SecondaryButton(L(R.string.agent_schedule_run_now), fill: true) { model.runNow(s.id) }
                LinkButton(L(R.string.delete), danger: true) {
                    if model.archive.data == nil { model.loadArchive() }   // its keep_days, for the question
                    confirmDelete = true
                }
            }
        }
        .keloSheet(isPresented: $timeSheet, detents: [.medium]) {
            TimeSheet(hour: plan.hour, minute: plan.minute, onPick: { h, m in plan.hour = h; plan.minute = m }, onClose: { timeSheet = false })
        }
        .keloSheet(isPresented: $repeatSheet, detents: [.large]) {
            RepeatSheet(plan: $plan, onClose: { repeatSheet = false })
        }
        .keloSheet(isPresented: $endSheet, detents: [.medium]) {
            EndSheet(until: plan.until, today: today, onNever: { plan.until = nil; endSheet = false }, onPickDate: {
                endSheet = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { dateSheet = .until }
            }, onClose: { endSheet = false })
        }
        .keloSheet(item: $dateSheet, detents: [.large]) { which in
            let shownDay = which == .until ? (plan.until ?? PlanRules.defaultUntil(start: plan.date, today: today)) : plan.date
            let earliest = which == .until ? max(plan.date, today) : today
            DateSheet(title: L(which == .until ? R.string.sched_end : (plan.freq == .none ? R.string.agent_schedule_date : R.string.sched_starts)),
                      day: shownDay, earliest: earliest, onPick: { d in
                if which == .until { plan.until = d } else {
                    plan.date = d
                    // a weekly repeat follows the new date's weekday when only one day was picked
                    if plan.freq == .weekly && plan.days.count <= 1 { plan.days = [d.cronWeekday] }
                }
            }, onClose: { dateSheet = nil })
        }
        // 삭제: three answers. 보관함에 넣기 keeps it restorable for keep_days days; 삭제 removes it at once for good; 취소 does nothing.
        .alert(L(R.string.schedule_delete_title, schedule?.name ?? ""), isPresented: $confirmDelete) {
            Button(L(R.string.cancel), role: .cancel) {}
            Button(L(R.string.schedule_delete_archive)) { if let s = schedule { model.deleteSchedule(s.id, then: onBack) } }
            Button(L(R.string.delete), role: .destructive) { if let s = schedule { model.deleteSchedule(s.id, forever: true, then: onBack) } }
        } message: {
            let keep = model.archive.data?.keepDays ?? 30
            Text(P(R.plurals.schedule_delete_body, keep, keep))
        }
    }

    private func label(_ text: String) -> some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.top, 6).padding(.leading, 2)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func save(now: LocalMoment) {
        saving = true
        let body = PlanRules.body(id: schedule?.id, name: name, prompt: prompt, plan: simple ? shown : nil,
                                  timezone: schedule == nil ? TimeZone.current.identifier : nil, now: now)
        model.saveSchedule(id: schedule?.id, body: body, onFailed: { saving = false }) { _ in
            saving = false
            onBack()
        }
    }
}

/// A row of the date / time / repeat card: its name, the value at the end with a chevron; the whole row opens a picker.
private struct PickerRow: View {
    let label: String
    let value: String
    let action: () -> Void
    @Environment(\.kelo) private var c
    @Environment(\.dynamicTypeSize) private var size

    var body: some View {
        Button(action: action) {
            let layout = size.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4)) : AnyLayout(HStackLayout(spacing: 16))
            layout {
                Text(label).font(KeloFont.body).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 4) {
                    Text(value).font(.system(.callout, weight: .medium)).foregroundStyle(c.ink)
                        .multilineTextAlignment(size.isAccessibilitySize ? .leading : .trailing).fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: size.isAccessibilitySize ? .leading : .trailing)
                    KeloIconView(.chevronRight, size: 16, tint: c.muted)
                }
            }
            .padding(.vertical, 12).frame(minHeight: 52).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// A saved schedule's switch, its next and last run, why it was switched off or paused, and its last runs.
private struct ScheduleStateBlock: View {
    let model: WorkModel
    let schedule: Schedule
    @State private var read: ScheduleRunsModel
    @Environment(\.kelo) private var c

    init(model: WorkModel, schedule: Schedule) {
        self.model = model; self.schedule = schedule
        _read = State(initialValue: ScheduleRunsModel(work: model, id: schedule.id))
    }

    private var runs: [ScheduleRun]? { read.runs }

    var body: some View {
        let s = schedule
        VStack(alignment: .leading, spacing: 4) {
            Toggle(isOn: Binding(get: { s.enabled }, set: { Haptics.tick(); model.setEnabled(s, $0) })) {
                Text(L(s.enabled ? R.string.work_schedule_on : R.string.work_schedule_off)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
            }
            .tint(c.accent)
            .accessibilityLabel(L(R.string.agent_schedule_switch, s.name))
            if s.enabled, let next = ScheduleTimes.nextRunSeconds(s) {
                Text(L(R.string.agent_schedule_next, WorkText.rel(next), WorkText.dayClock(next))).font(KeloFont.small).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let lr = s.lastRun, let at = lr.at {
                Text(L(R.string.agent_schedule_last, WorkText.run(lr.status), WorkText.rel(at))).font(KeloFont.label)
                    .foregroundStyle(lr.status == "failed" ? c.danger : c.muted)
            }
            if let r = s.disabledReason, !r.isEmpty, !s.enabled {
                Text(L(R.string.agent_schedule_off_reason, r)).font(KeloFont.label).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true)
            }
            if s.paused {
                Text(L(R.string.work_schedule_paused_reason, (s.pausedReason ?? "").isEmpty ? L(R.string.work_schedule_paused_unknown) : s.pausedReason!))
                    .font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                LinkButton(L(R.string.work_schedule_resume)) { model.resumeSchedule(s.id) }
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        .padding(.top, 8)

        Text(L(R.string.work_schedule_runs)).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.top, 6)
        Group {
            if let list = runs {
                if list.isEmpty {
                    MutedText(L(R.string.work_schedule_runs_empty), small: true)
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(list.enumerated()), id: \.element.id) { i, r in
                            if i > 0 { Hairline() }
                            HStack(alignment: .top, spacing: 10) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(r.startedAt > 0 ? WorkText.dayClock(r.startedAt) : "").font(KeloFont.label).foregroundStyle(c.muted)
                                    let line = r.result.isEmpty ? (r.error ?? "") : r.result
                                    if !line.isEmpty { Text(line).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(4) }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                Text(WorkText.run(r.status)).font(.system(.caption2, weight: .semibold))
                                    .foregroundStyle(r.status == "failed" ? c.danger : r.status == "done" ? c.success : c.muted)
                            }
                            .padding(.horizontal, 14).padding(.vertical, 10)
                        }
                    }
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                }
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .task(id: s.id) { await read.load() }
        ScheduleGrantsBlock(read: read)
    }
}

/// What this schedule's runs may do without asking (a login, a purchase up to a cap), allowed once at setup; each can be revoked.
/// Nothing shows while there is none (Android SchedulePage GrantsBlock).
private struct ScheduleGrantsBlock: View {
    let read: ScheduleRunsModel
    @Environment(\.kelo) private var c

    var body: some View {
        Group {
            if !read.grants.isEmpty {
                // headed like the runs above it
                Text(L(R.string.grant_section_title)).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.top, 6)
                    .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
                ScheduleGrantRows(read.grants, showSchedule: false) { g in read.revoke(g) }
                MutedText(L(R.string.grant_section_note), small: true)
            }
        }
    }
}

/// The time picker.
private struct TimeSheet: View {
    let onPick: (Int, Int) -> Void
    let onClose: () -> Void
    @State private var time: Date

    init(hour: Int, minute: Int, onPick: @escaping (Int, Int) -> Void, onClose: @escaping () -> Void) {
        self.onPick = onPick; self.onClose = onClose
        var cal = Calendar.current
        cal.timeZone = .current
        _time = State(initialValue: cal.date(bySettingHour: hour, minute: minute, second: 0, of: Date()) ?? Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SheetHeader(L(R.string.agent_schedule_time), onClose: onClose)
            DatePicker("", selection: $time, displayedComponents: .hourAndMinute)
                .labelsHidden()
                #if os(iOS)
                .datePickerStyle(.wheel)
                #endif
                .frame(maxWidth: .infinity)
            PrimaryButton(L(R.string.ok)) {
                let c = Calendar.current.dateComponents([.hour, .minute], from: time)
                onPick(c.hour ?? 9, c.minute ?? 0)
                onClose()
            }
        }
        .padding(.horizontal, 20).padding(.bottom, 24)
        .environment(\.locale, appLocale())
    }
}

/// A date picker from `earliest` on.
private struct DateSheet: View {
    let title: String
    let earliest: LocalDay
    let onPick: (LocalDay) -> Void
    let onClose: () -> Void
    @State private var date: Date

    init(title: String, day: LocalDay, earliest: LocalDay, onPick: @escaping (LocalDay) -> Void, onClose: @escaping () -> Void) {
        self.title = title; self.earliest = earliest; self.onPick = onPick; self.onClose = onClose
        _date = State(initialValue: max(day, earliest).date())
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(title, onClose: onClose)
                DatePicker("", selection: $date, in: earliest.date()..., displayedComponents: .date)
                    .labelsHidden().datePickerStyle(.graphical)
                PrimaryButton(L(R.string.ok)) { onPick(LocalDay(date: date)); onClose() }
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .environment(\.locale, appLocale())
    }
}

/// How it repeats. Any choice but "every week" closes the sheet; "every week" shows the weekdays as small round toggles.
private struct RepeatSheet: View {
    @Binding var plan: SchedulePlan
    let onClose: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                SheetHeader(L(R.string.sched_repeat), onClose: onClose).padding(.bottom, 8)
                ForEach(Freq.allCases, id: \.self) { f in
                    OptionRow(text: WorkText.freqOption(f, date: plan.date), selected: plan.freq == f) {
                        Haptics.tick()
                        plan = PlanRules.withFreq(plan, f)
                        if f != .weekly { onClose() }
                    }
                    if f == .weekly && plan.freq == .weekly { WeekdayCircles(days: $plan.days) }
                }
                if plan.freq == .weekly {
                    PrimaryButton(L(R.string.ok), action: onClose).padding(.top, 12)
                }
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
    }
}

/// The seven weekdays as small circles, the locale's first day first; the last chosen day stays on.
private struct WeekdayCircles: View {
    @Binding var days: Set<Int>
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 4) {
            ForEach(PlanRules.weekOrder(first: WorkText.firstWeekday), id: \.self) { d in
                let on = days.contains(d)
                Button {
                    Haptics.tick()
                    days = PlanRules.toggleDay(days, d)
                } label: {
                    Text(PlanRules.circleLabel(short: WorkText.weekdayShort(d), narrow: WorkText.weekdayNarrow(d)))
                        .font(.system(.caption, weight: on ? .semibold : .regular)).foregroundStyle(on ? c.onAccent : c.ink)
                        .lineLimit(1).minimumScaleFactor(0.7)
                        .frame(width: 36, height: 36)
                        .background(Circle().fill(on ? c.accent : c.card))
                        .overlay(Circle().stroke(on ? c.accent : c.line, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity)
                .accessibilityLabel(WorkText.weekdayFull(d))
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .frame(maxWidth: 340)
        .padding(.leading, 36).padding(.top, 2).padding(.bottom, 10)
    }
}

/// When a repeat ends: never, or on a date (which opens the date picker).
private struct EndSheet: View {
    let until: LocalDay?
    let today: LocalDay
    let onNever: () -> Void
    let onPickDate: () -> Void
    let onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SheetHeader(L(R.string.sched_end), onClose: onClose).padding(.bottom, 8)
            OptionRow(text: L(R.string.sched_end_never), selected: until == nil) { Haptics.tick(); onNever() }
            OptionRow(text: L(R.string.sched_end_on), selected: until != nil,
                      sub: until.map { L(R.string.sched_end_until, WorkText.dayOrFull($0, today: today)) }) { Haptics.tick(); onPickDate() }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20).padding(.bottom, 24)
    }
}

/// One choice of a sheet: a radio mark, its words and an optional second line.
private struct OptionRow: View {
    let text: String
    let selected: Bool
    var sub: String? = nil
    let action: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                ZStack {
                    Circle().stroke(selected ? c.accent : c.muted, lineWidth: 2).frame(width: 20, height: 20)
                    if selected { Circle().fill(c.accent).frame(width: 10, height: 10) }
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(text).font(.system(.callout, weight: selected ? .medium : .regular)).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    if let sub { Text(sub).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 10).frame(minHeight: 52).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}
