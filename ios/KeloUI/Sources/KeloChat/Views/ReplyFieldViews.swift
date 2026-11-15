import SwiftUI
import KeloShared

/// A quick-reply question's typed field (`ReplyFields`): the system's own picker for its kind, whatever the question is about
/// (Android chat/ReplyFieldViews.kt). `value`: the plain form once complete ("" while half filled).
struct ReplyFieldView: View {
    let q: ReplyQuestion
    @Binding var value: String
    @State private var days: Set<Int> = []
    @State private var date: ReplyFields.YMD?
    @State private var time: ReplyFields.HM?
    @State private var dateSheet = false
    @State private var timeSheet = false
    @State private var loaded = false
    @Environment(\.kelo) private var c

    var body: some View {
        let kind = ReplyFields.kind(q)
        Group {
            switch kind {
            case ReplyFields.text:
                KeloTextField(QuickReplyRules.placeholder(q) ?? L(R.string.reply_type_hint), text: $value, axis: .vertical, maxLength: 300)
            case ReplyFields.number:
                VStack(alignment: .leading, spacing: 4) {
                    KeloTextField(q.unit.trimmingCharacters(in: .whitespaces).isEmpty ? L(R.string.reply_number_hint) : q.unit,
                                  text: Binding(get: { value }, set: { value = $0.filter { $0.isNumber || ".,-".contains($0) } }),
                                  keyboard: .number, maxLength: 20)
                        .frame(maxWidth: 240)
                    if let (lo, hi) = ReplyFields.range(q) {
                        let bad = !value.isEmpty && ReplyFields.check(q, value) == nil
                        Text(L(R.string.reply_number_range, lo, hi)).font(KeloFont.small).foregroundStyle(bad ? c.danger : c.muted)
                    }
                }
            case ReplyFields.date:
                dateChip
            case ReplyFields.time:
                timeChip
            case ReplyFields.datetime:
                FlowLayout(spacing: 6, lineSpacing: 6) { dateChip; timeChip }
            default:   // weekdays, weekdays_time
                VStack(alignment: .leading, spacing: 6) {
                    // the seven days as small round toggles in one line (the schedule page's look); several may be on
                    HStack(spacing: 4) {
                        ForEach(ReplyFields.weekOrder(firstWeekday: ReplyFieldWords.firstWeekday), id: \.self) { d in
                            let on = days.contains(d)
                            Button {
                                Haptics.tick()
                                days = ReplyFields.toggle(days, d); emit()
                            } label: {
                                Text(ReplyFieldWords.circleLabel(d))
                                    .font(.system(.caption, weight: on ? .semibold : .regular)).foregroundStyle(on ? c.onAccent : c.ink)
                                    .lineLimit(1).minimumScaleFactor(0.6)
                                    .frame(maxWidth: 38, maxHeight: 38)
                                    .aspectRatio(1, contentMode: .fit)   // shrinks to fit seven on a narrow phone
                                    .background(Circle().fill(on ? c.accent : c.card))
                                    .overlay(Circle().stroke(on ? c.accent : c.line, lineWidth: 1))
                            }
                            .buttonStyle(.plain)
                            .frame(maxWidth: .infinity)
                            .accessibilityLabel(ReplyFieldWords.dayFull(d))
                            .accessibilityAddTraits(on ? .isSelected : [])
                        }
                    }
                    .frame(maxWidth: 340)
                    if kind == ReplyFields.weekdaysTime { timeChip }
                }
            }
        }
        .onAppear(perform: load)
        .keloSheet(isPresented: $dateSheet, detents: [.large]) {
            ReplyDateSheet(start: date, onPick: { date = $0; emit() }, onClose: { dateSheet = false })
        }
        .keloSheet(isPresented: $timeSheet, detents: [.medium]) {
            ReplyTimeSheet(start: time, onPick: { time = $0; emit() }, onClose: { timeSheet = false })
        }
    }

    private var dateChip: some View {
        ChoiceChip(date.map { ReplyFieldWords.dateText($0) } ?? L(R.string.reply_pick_date), selected: date != nil) { dateSheet = true }
    }

    private var timeChip: some View {
        ChoiceChip(time.map { ReplyFieldWords.timeText($0) } ?? L(R.string.reply_pick_time), selected: time != nil) { timeSheet = true }
    }

    /// The pickers start on the field's value (the question's default).
    private func load() {
        guard !loaded else { return }
        loaded = true
        let v = value
        switch ReplyFields.kind(q) {
        case ReplyFields.date: date = ReplyFields.parseDate(v)
        case ReplyFields.time: time = ReplyFields.parseTime(v)
        case ReplyFields.datetime:
            let p = v.split(separator: " ", maxSplits: 1).map(String.init)
            date = p.first.flatMap(ReplyFields.parseDate); time = p.count == 2 ? ReplyFields.parseTime(p[1]) : nil
        case ReplyFields.weekdays: days = ReplyFields.parseDays(v) ?? []
        case ReplyFields.weekdaysTime:
            if let at = v.lastIndex(of: " ") {
                days = ReplyFields.parseDays(String(v[..<at])) ?? []; time = ReplyFields.parseTime(String(v[v.index(after: at)...]))
            }
        default: break
        }
    }

    private func emit() {
        switch ReplyFields.kind(q) {
        case ReplyFields.date: value = date.map(ReplyFields.datePlain) ?? ""
        case ReplyFields.time: value = time.map(ReplyFields.timePlain) ?? ""
        case ReplyFields.datetime:
            if let d = date, let t = time { value = "\(ReplyFields.datePlain(d)) \(ReplyFields.timePlain(t))" } else { value = "" }
        case ReplyFields.weekdays: value = days.isEmpty ? "" : ReplyFields.daysPlain(days)
        case ReplyFields.weekdaysTime:
            if !days.isEmpty, let t = time { value = "\(ReplyFields.daysPlain(days)) \(ReplyFields.timePlain(t))" } else { value = "" }
        default: break
        }
    }
}

/// How field answers are written in the app language (`ReplyFields.display` with the app's own phrases).
@MainActor
enum ReplyFieldWords {
    static var locale: Locale { appLocale() }

    /// The first day of the week where the user is (Calendar's: 1 = Sunday): the app language's, with the phone's region when the
    /// language carries none (the same rule as the schedule page).
    static var firstWeekday: Int {
        var cal = Calendar(identifier: .gregorian)
        let region = Locale.current.region?.identifier
        let id = locale.region == nil && region != nil ? locale.identifier + "_" + region! : locale.identifier
        cal.locale = Locale(identifier: id)
        return cal.firstWeekday
    }

    static var words: ReplyFields.Words {
        ReplyFields.Words(daily: L(R.string.agent_repeat_daily), weekly: L(R.string.sched_value_weekly),
                          dailyAt: L(R.string.agent_repeat_daily_at), weeklyAt: L(R.string.agent_repeat_weekly_at),
                          dateAt: L(R.string.reply_date_at), separator: L(R.string.agent_list_separator))
    }

    static func show(_ q: ReplyQuestion, _ plain: String) -> String {
        ReplyFields.display(q, plain, locale: locale, words: words, firstWeekday: firstWeekday)
    }

    /// The label in a small day circle: the short name without its dot when at most 3 letters, else the narrow one (`d`: 0 = Monday).
    static func circleLabel(_ d: Int) -> String {
        var cal = Calendar(identifier: .gregorian); cal.locale = locale
        var s = cal.shortWeekdaySymbols[(d + 1) % 7]
        while s.hasSuffix(".") { s.removeLast() }
        return (1...3).contains(s.count) ? s : cal.veryShortWeekdaySymbols[(d + 1) % 7]
    }

    static func dayFull(_ d: Int) -> String {
        var cal = Calendar(identifier: .gregorian); cal.locale = locale
        return cal.weekdaySymbols[(d + 1) % 7]
    }

    static func dateText(_ d: ReplyFields.YMD) -> String { ReplyFields.dateText(d, locale) }
    static func timeText(_ t: ReplyFields.HM) -> String { ReplyFields.timeText(t, locale) }
}

private struct ReplyDateSheet: View {
    let onPick: (ReplyFields.YMD) -> Void
    let onClose: () -> Void
    @State private var date: Date

    init(start: ReplyFields.YMD?, onPick: @escaping (ReplyFields.YMD) -> Void, onClose: @escaping () -> Void) {
        self.onPick = onPick; self.onClose = onClose
        _date = State(initialValue: start.map(ReplyFields.dateValue) ?? Date())
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(R.string.reply_pick_date), onClose: onClose)
                DatePicker("", selection: $date, displayedComponents: .date).labelsHidden().datePickerStyle(.graphical)
                PrimaryButton(L(R.string.ok)) {
                    let c = Calendar.current.dateComponents([.year, .month, .day], from: date)
                    onPick(ReplyFields.YMD(year: c.year ?? 2026, month: c.month ?? 1, day: c.day ?? 1)); onClose()
                }
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .environment(\.locale, appLocale())
    }
}

private struct ReplyTimeSheet: View {
    let onPick: (ReplyFields.HM) -> Void
    let onClose: () -> Void
    @State private var time: Date

    init(start: ReplyFields.HM?, onPick: @escaping (ReplyFields.HM) -> Void, onClose: @escaping () -> Void) {
        self.onPick = onPick; self.onClose = onClose
        let s = start ?? ReplyFields.HM(hour: 12, minute: 0)
        _time = State(initialValue: Calendar.current.date(bySettingHour: s.hour, minute: s.minute, second: 0, of: Date()) ?? Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SheetHeader(L(R.string.reply_pick_time), onClose: onClose)
            DatePicker("", selection: $time, displayedComponents: .hourAndMinute)
                .labelsHidden()
                #if os(iOS)
                .datePickerStyle(.wheel)
                #endif
                .frame(maxWidth: .infinity)
            PrimaryButton(L(R.string.ok)) {
                let c = Calendar.current.dateComponents([.hour, .minute], from: time)
                onPick(ReplyFields.HM(hour: c.hour ?? 12, minute: c.minute ?? 0)); onClose()
            }
        }
        .padding(.horizontal, 20).padding(.bottom, 24)
        .environment(\.locale, appLocale())
    }
}
