import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 알림: this phone's notifications (on or off in iPhone Settings), follow-ups (다시 묻기), and "Kelo가 먼저 알려주기" (the
/// agent may write first: on or off, at most N times a day, only between the hours set) with its optional list of things to keep
/// an eye on (Android my/NotifyPage.kt).
struct NotifyPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @Environment(\.keloBackground) private var background
    @Environment(\.scenePhase) private var phase
    @Environment(AppModel.self) private var app
    @State private var model: NotifySettingsModel
    @State private var briefing: BriefingSettingsModel
    @State private var sources: BriefingSourcesModel
    @State private var phoneOn = NotifyPermission.cached()
    @State private var picking: String?
    @State private var pickTime = Date()
    @State private var draft = ""
    /// The hours suggestion just answered here (its id, and true = changed)
    @State private var hoursDone: (String, Bool)?

    init(api: KeloApi) {
        _model = State(initialValue: NotifySettingsModel(api: api))
        _briefing = State(initialValue: BriefingSettingsModel(api: api))
        _sources = State(initialValue: BriefingSourcesModel(api: api))
    }

    private var proactive: ProactiveSettings? { model.proactive }
    private var checklist: ChecklistSettings? { model.checklist }
    private var followups: FollowupSettings? { model.followups }
    private var toastText: String? { model.notice ?? briefing.notice ?? sources.notice }
    private func clearToast() { model.clearNotice(); briefing.clearNotice(); sources.clearNotice() }

    var body: some View {
        SubPage(L(R.string.notify_my_row), onBack: { dismiss() }) {
            Group {
                SectionLabel(L(R.string.notify_page_phone))
                MenuRow(.bell, L(R.string.notify_page_phone_row), value: phoneOn ? L(R.string.notify_state_on) : L(R.string.notify_state_off),
                        valueColor: phoneOn ? nil : c.danger) {
                    Task { if phoneOn { DeviceInfo.openAppSettings() } else { phoneOn = await NotifyPermission.request(); if phoneOn { await background?.syncPush() } } }
                }
            }
            .padding(.horizontal, -16)
            if !phoneOn { MutedText(L(R.string.my_ios_notify_background_note), small: true) }
            if !model.error.isEmpty { ErrorText(model.error, small: true) }

            SectionLabel(L(R.string.followup_settings_title)).padding(.horizontal, -16)
            if model.followupsMissing { MutedText(L(R.string.notify_page_unavailable), small: true) }
            else if let f = followups { followupBlock(f) }
            else { SkeletonRows(rows: 1) }

            SectionLabel(L(R.string.hours_section)).padding(.horizontal, -16)
            if model.proactiveMissing { MutedText(L(R.string.notify_page_unavailable), small: true) }
            else if let p = proactive { hoursBlock(p) }
            else { SkeletonRows(rows: 2) }

            if !briefing.missing {
                SectionLabel(L(R.string.briefing_title)).padding(.horizontal, -16)
                if let b = briefing.settings {
                    BriefingBlock(settings: b, model: briefing)
                } else {
                    SkeletonRows(rows: 2)
                }
            }

            SectionLabel(L(R.string.heads_section)).padding(.horizontal, -16)
            if model.proactiveMissing { MutedText(L(R.string.notify_page_unavailable), small: true) }
            else if let p = proactive { headsUp(p) }
            else { SkeletonRows(rows: 2) }
            if proactive?.enabled == true && !model.checklistMissing {
                if let k = checklist { watchList(k.list) } else { SkeletonRows(rows: 1) }
            }

            // which calendars and mailboxes the briefing and the heads-ups read: one choice for both, shown whether the briefing is
            // on or not (Android my/NotifyPage.kt, my/BriefingSources.kt); nothing when no calendar or mailbox is connected
            BriefingSourcesBlock(model: sources)
        }
        .task {
            async let phone: Void = { phoneOn = await NotifyPermission.isOn() }()
            await model.load()
            await briefing.load()
            await phone
        }
        .onChange(of: phase) { _, p in if p == .active { Task { phoneOn = await NotifyPermission.isOn() } } }
        .keloSheet(isPresented: Binding(get: { picking != nil }, set: { if !$0 { picking = nil } }), detents: [.medium]) {
            VStack(spacing: 12) {
                SheetHeader(L(R.string.hours_section)) { picking = nil }
                DatePicker("", selection: $pickTime, displayedComponents: .hourAndMinute).labelsHidden()
                    #if os(iOS)
                    .datePickerStyle(.wheel)
                    #endif
                PrimaryButton(L(R.string.ok)) {
                    let comps = Calendar.current.dateComponents([.hour, .minute], from: pickTime)
                    if let field = picking {
                        model.putProactive([field: .string(NotifySettingsRules.formatTime(comps.hour ?? 7, comps.minute ?? 0))])
                    }
                    picking = nil
                }
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .toast(toastText, clear: clearToast)
    }

    // ---- blocks ------------------------------------------------------------------------------------------------------------------

    private func toggleLine(_ title: String, _ body: String, on: Bool, onToggle: @escaping (Bool) -> Void) -> some View {
        ToggleRow(title, detail: body, isOn: Binding(get: { on }, set: { Haptics.tick(); onToggle($0) }))
    }

    /// 새 소식 알림 (proactive.py): every few hours (the server's own every_minutes) it looks for new things the owner should know. The
    /// about text says how often, and that what waits for the owner is asked again by the follow-ups (다시 묻기) whatever this is set to.
    @ViewBuilder private func headsUp(_ p: ProactiveSettings) -> some View {
        let every = NotifySettingsRules.every(p.everyMinutes)
        let often = every.hours ? P(R.plurals.agent_repeat_every_hours, every.n, every.n) : P(R.plurals.work_every_minutes, every.n, every.n)
        toggleLine(L(R.string.heads_switch), L(R.string.heads_about, often, L(R.string.followup_settings_title)), on: p.enabled) {
            model.putProactive(["enabled": .bool($0)])
        }
        if p.enabled {
            stepper(L(R.string.heads_per_day), value: p.dailyTotal, text: P(R.plurals.heads_times, p.dailyTotal, p.dailyTotal), min: 1) { v in
                let caps = NotifySettingsRules.perDay(v, regular: p.dailyRegular, now: p.dailyNow)
                model.putProactive(["daily_regular": .number(Double(caps.regular)), "daily_total": .number(Double(caps.total)), "daily_now": .number(Double(caps.now))])
            }
            deliveries(p)
            if model.situationReady && p.research { situationRow }
            toggleLine(L(R.string.heads_mail), L(R.string.heads_mail_about), on: p.mailCheck) {
                model.putProactive(["mail_check": .bool($0)])
            }
            let sent = p.today.values.reduce(0, +)
            if sent > 0 { MutedText(P(R.plurals.heads_sent_today, sent, sent), small: true) }
        }
    }

    /// "What Kelo looked at" (only when the server has the look: an older server hides the row).
    private var situationRow: some View {
        Button { MyNav(app: app).push(.situation) } label: {
            HStack(spacing: 2) {
                Text(L(R.string.situation_row)).font(KeloFont.small).foregroundStyle(c.accent)
                KeloIconView(.chevronRight, size: 14, tint: c.accent)
            }
        }
        .buttonStyle(.plain)
    }

    /// How many messages a day Kelo starts (0...10, 0 = only what cannot wait) and "오늘 n번 보냄 · 보관 중 m개" (the held part opens the
    /// list); each row only when the server sends its field (Android my/ProactiveDeliveries.kt DeliveriesBlock).
    @ViewBuilder private func deliveries(_ p: ProactiveSettings) -> some View {
        if let count = p.dailyDeliveries {
            stepper(L(R.string.deliveries_label), value: count, text: String(count), min: DeliveryRules.min, max: DeliveryRules.max) { v in
                model.putProactive(["daily_deliveries": .number(Double(DeliveryRules.clamp(v)))])
            }
            MutedText(L(R.string.deliveries_about), small: true)
        }
        if p.todayDeliveries != nil || p.held != nil {
            let today = p.todayDeliveries.map { Text(P(R.plurals.deliveries_today, $0, $0)).font(KeloFont.small).foregroundStyle(c.muted) }
            let held = p.held.map { n in
                Button { MyNav(app: app).push(.proactiveHeld) } label: {
                    HStack(spacing: 2) {
                        Text(P(R.plurals.deliveries_held, n, n)).font(KeloFont.small).foregroundStyle(c.accent)
                        KeloIconView(.chevronRight, size: 14, tint: c.accent)
                    }
                }
                .buttonStyle(.plain)
            }
            // one line when it fits, else the two parts one under the other (never a part broken in the middle)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 6) {
                    if let today { today.fixedSize() }
                    if today != nil && held != nil { Text(L(R.string.deliveries_separator)).font(KeloFont.small).foregroundStyle(c.muted) }
                    if let held { held.fixedSize() }
                    Spacer(minLength: 0)
                }
                VStack(alignment: .leading, spacing: 2) {
                    if let today { today.fixedSize(horizontal: false, vertical: true) }
                    if let held { held }
                }
            }
        }
    }

    /// 알림 시간: when Kelo may write, one row for weekdays and one for weekends and public holidays (an older server: weekdays only),
    /// each with its times, where they came from, the window learned from the owner's use when it differs ("배운 시간 … 적용"), and an
    /// open suggestion for that day type. Hours set here by hand are never changed by Kelo (Android NotifyPage HoursBlock).
    @ViewBuilder private func hoursBlock(_ p: ProactiveSettings) -> some View {
        let h = p.hours
        let kinds = h.holidayCountry != nil ? [ContactHoursRules.weekday, ContactHoursRules.weekend] : [ContactHoursRules.weekday]
        MutedText(L(R.string.hours_about), small: true)
        ForEach(kinds, id: \.self) { kind in
            let w = ContactHoursRules.window(h, kind)
            VStack(alignment: .leading, spacing: 8) {
                if kinds.count > 1 {
                    Text(L(kind == ContactHoursRules.weekend ? R.string.hours_weekend : R.string.hours_weekday)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
                }
                TimesRow(gap: 10) { hourButtons(kind, w.start, w.end) }
                if kind == ContactHoursRules.weekend && w.source == "default" {
                    MutedText(L(R.string.hours_weekend_follows), small: true)
                } else if ContactHoursRules.learned(w.source) {
                    HStack(spacing: 6) {
                        KeloIconView(.check, size: 16, tint: c.accent)
                        Text(L(R.string.hours_learned)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    }
                } else if w.source == "manual" {
                    MutedText(L(R.string.hours_manual), small: true)
                }
                if kind == ContactHoursRules.weekend && w.source == "manual" {
                    LinkButton(L(R.string.hours_weekend_reset)) { model.putProactive(["weekend_start": .string(""), "weekend_end": .string("")]) }
                }
                if let l = ContactHoursRules.learnedFor(h, kind) {
                    HStack(spacing: 8) {
                        Text(L(R.string.hours_learned_value, hoursSpanText(l.wakeStart, l.wakeEnd))).font(KeloFont.small).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                        SecondaryButton(L(R.string.hours_learned_apply)) { model.postHours(ContactHoursRules.applyPath(kind)) }
                            .accessibilityIdentifier("kelo.hours.apply.\(kind)")
                    }
                }
                if let s = suggestion(h, kind) {
                    HoursSuggestionCard(suggestion: s, done: hoursDone.flatMap { $0.0 == s.id ? $0.1 : nil }) { accept in
                        hoursDone = (s.id, accept)
                        model.postHours(accept ? ContactHoursRules.acceptPath(s.id) : ContactHoursRules.keepPath(s.id))
                    }
                }
            }
            .padding(.vertical, 4)
        }
        toggleLine(L(R.string.hours_auto_switch), L(R.string.hours_auto_about_manual), on: p.autoHours) { model.putProactive(["auto_hours": .bool($0)]) }
    }

    private func suggestion(_ h: ContactHours, _ kind: String) -> HoursSuggestion? {
        var s = kind == ContactHoursRules.weekend ? h.weekendSuggestion : h.suggestion
        s?.dayType = kind
        return ContactHoursRules.shown(s) ? s : nil
    }

    @ViewBuilder private func hourButtons(_ kind: String, _ start: String, _ end: String) -> some View {
        SecondaryButton(L(R.string.proactive_from, timeWords(start)), fill: true) { openPicker(ContactHoursRules.field(kind, start: true), start) }
        SecondaryButton(L(ContactHoursRules.pastMidnight(start, end) ? R.string.proactive_until_next_day : R.string.proactive_until,
                          timeWords(end)), fill: true) { openPicker(ContactHoursRules.field(kind, start: false), end) }
    }

    private func openPicker(_ field: String, _ value: String) {
        let t = NotifySettingsRules.parseTime(value) ?? (7, 0)
        pickTime = Calendar.current.date(bySettingHour: t.hour, minute: t.minute, second: 0, of: Date()) ?? Date()
        picking = field
    }

    private func timeWords(_ text: String) -> String {
        NotifySettingsRules.parseTime(text).map { MyFormat.clock($0.hour, $0.minute) } ?? text
    }

    private func stepper(_ label: String, value: Int, text: String, min: Int, max: Int = NotifySettingsRules.maxTotal,
                         onChange: @escaping (Int) -> Void) -> some View {
        HStack(spacing: 4) {
            Text(label).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Button { onChange(value - 1) } label: { KeloIconView(.minus, size: 22, tint: value > min ? c.ink : c.disabledInk).frame(width: 40, height: 40) }
                .buttonStyle(.plain).disabled(value <= min).accessibilityLabel(L(R.string.proactive_less))
            Text(text).font(KeloFont.titleMedium).foregroundStyle(c.ink)
            Button { onChange(value + 1) } label: { KeloIconView(.plus, size: 22, tint: value < max ? c.ink : c.disabledInk).frame(width: 40, height: 40) }
                .buttonStyle(.plain).disabled(value >= max).accessibilityLabel(L(R.string.proactive_more))
        }
    }

    @ViewBuilder private func watchList(_ items: [String]) -> some View {
        Text(L(R.string.heads_list_title)).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.top, 8)
        MutedText(L(R.string.heads_list_about), small: true)
        if !items.isEmpty {
            VStack(spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                    if i > 0 { Hairline() }
                    HStack {
                        Text(item).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true).padding(.vertical, 10)
                        Spacer(minLength: 8)
                        Button { model.putChecklist(items.filter { $0 != item }) } label: { KeloIconView(.close, size: 18, tint: c.muted).frame(width: 40, height: 40) }
                            .buttonStyle(.plain).accessibilityLabel(L(R.string.heads_list_remove, item))
                    }
                    .padding(.leading, 14)
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        }
        if items.count < NotifySettingsRules.maxItems {
            KeloTextField(L(R.string.heads_list_hint), text: $draft, maxLength: NotifySettingsRules.maxItem)
            SecondaryButton(L(R.string.heads_list_add), enabled: !draft.trimmingCharacters(in: .whitespaces).isEmpty) {
                let next = NotifySettingsRules.added(items, draft)
                if next != items { model.putChecklist(next) }
                draft = ""
            }
        }
    }

    @ViewBuilder private func followupBlock(_ f: FollowupSettings) -> some View {
        toggleLine(L(R.string.followup_settings_switch), L(R.string.followup_settings_about), on: f.enabled) { model.putFollowups(["enabled": .bool($0)]) }
        if f.enabled {
            FieldLabel(L(R.string.followup_settings_first))
            SelectField(title: L(R.string.followup_settings_first),
                        options: FollowupDelayRules.choices(FollowupDelayRules.firstChoices, current: f.firstMinutes).map { SelectOption(value: String($0), label: MyFormat.later($0)) },
                        selected: String(f.firstMinutes)) { v in
                guard let first = Int(v) else { return }
                let (a, b) = FollowupDelayRules.delays(first, f.secondMinutes)
                var body: [String: JSON] = ["first_delay_min": .number(Double(a))]
                if b != f.secondMinutes { body["second_delay_min"] = .number(Double(b)) }
                model.putFollowups(body)
            }
            FieldLabel(L(R.string.followup_settings_second))
            SelectField(title: L(R.string.followup_settings_second),
                        options: FollowupDelayRules.choices(FollowupDelayRules.secondChoices, current: f.secondMinutes).filter { $0 >= f.firstMinutes }
                            .map { SelectOption(value: String($0), label: MyFormat.later($0)) },
                        selected: String(f.secondMinutes)) { v in
                guard let second = Int(v) else { return }
                model.putFollowups(["second_delay_min": .number(Double(FollowupDelayRules.delays(f.firstMinutes, second).1))])
            }
            MutedText(L(R.string.followup_settings_hold_note), small: true)
        }
    }
}

/// The "from" and "until" buttons: two equal halves of one row when both labels fit on one line there, else one under the other, each
/// full width (`ContactHoursRules.timesSideBySide`), so a label never wraps inside its pill. A ViewThatFits picked the row whenever the
/// two together fit, and then the longer one ("다음 날 오전 1:00까지") wrapped in its half (Android NotifyPage TimesRow).
private struct TimesRow: Layout {
    let gap: CGFloat
    private func side(_ subviews: Subviews, _ width: CGFloat?) -> Bool {
        guard subviews.count == 2, let width else { return false }
        let needs = subviews.map { $0.sizeThatFits(ProposedViewSize(width: nil, height: nil)).width }
        return ContactHoursRules.timesSideBySide(Double(needs[0]), Double(needs[1]), available: Double(width), gap: Double(gap))
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? subviews.map { $0.sizeThatFits(.unspecified).width }.reduce(0, +) + gap
        if side(subviews, proposal.width) {
            let each = (width - gap) / 2
            let h = subviews.map { $0.sizeThatFits(ProposedViewSize(width: each, height: nil)).height }.max() ?? 0
            return CGSize(width: width, height: h)
        }
        let hs = subviews.map { $0.sizeThatFits(ProposedViewSize(width: width, height: nil)).height }
        return CGSize(width: width, height: hs.reduce(0, +) + gap * CGFloat(max(hs.count - 1, 0)))
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        if side(subviews, bounds.width) {
            let each = (bounds.width - gap) / 2
            var x = bounds.minX
            for v in subviews {
                v.place(at: CGPoint(x: x, y: bounds.midY), anchor: .leading, proposal: ProposedViewSize(width: each, height: nil))
                x += each + gap
            }
        } else {
            var y = bounds.minY
            for v in subviews {
                let h = v.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil)).height
                v.place(at: CGPoint(x: bounds.minX, y: y), proposal: ProposedViewSize(width: bounds.width, height: h))
                y += h + gap
            }
        }
    }
}
