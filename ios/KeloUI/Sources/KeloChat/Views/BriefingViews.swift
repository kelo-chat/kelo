import SwiftUI
import KeloCore
import KeloShared

/// Where a tap on a briefing item goes (Android chat/BriefingCard.kt BriefingTap).
public enum BriefingTap: Equatable, Sendable {
    /// The chat at the follow-up's own message
    case chatAt(Int64)
    /// The 작업 panel's list of what waits (a follow-up without a message of its own)
    case waiting
    /// A background job's task
    case task(Int64)
    /// The goals tab
    case goals
    /// The phone's calendar at that moment (unix seconds)
    case event(Double)
    /// A question about that mail written into the composer (nothing is sent until the owner sends it)
    case mail(String)
    /// A next step's question written into the composer the same way (nothing is sent until the owner sends it)
    case ask(String)
}

/// A small reference chip under a "worth noticing" note: the card item it points at, labelled with it; `tap` nil = not clickable.
public struct BriefingChip: Equatable, Sendable {
    public var ref: String
    public var label: String
    public var tap: BriefingTap?
    public init(ref: String, label: String, tap: BriefingTap? = nil) { self.ref = ref; self.label = label; self.tap = tap }
}

/// The briefing card's small rules, apart from the UI (Android BriefingRules).
public enum BriefingRules {
    /// Where an item opens; nil: it has no place of its own (a promise noted in memory).
    public static func tap(_ open: BriefingOpen?) -> BriefingTap? {
        guard let open else { return nil }
        switch open.kind {
        case "followup": return open.messageId.map { .chatAt($0) } ?? .waiting
        case "job": return open.taskId.map { .task($0) } ?? .waiting
        case "goal": return .goals
        case "event": return open.start.map { .event($0) }
        case "mail": return open.subject.flatMap { $0.isBlank ? nil : .mail($0) }
        default: return nil
        }
    }

    /// The card's sections, in order, that have something: headline, notes ("worth noticing"), weather, today, ahead, missed,
    /// steps ("next steps"), hours.
    public static func sections(_ card: BriefingCard) -> [String] {
        var out: [String] = []
        if headline(card) != nil { out.append("headline") }
        if !notes(card).isEmpty { out.append("notes") }
        if card.weather != nil { out.append("weather") }
        if !card.events.isEmpty { out.append("today") }
        if !card.ahead.isEmpty { out.append("ahead") }
        if !card.missed.isEmpty { out.append("missed") }
        if !steps(card).isEmpty { out.append("steps") }
        if hoursShown(card.hours) { out.append("hours") }
        return out
    }

    /// The day in short: the model's headline, else the older line; one text, never both; nil when there is none.
    public static func headline(_ card: BriefingCard) -> String? {
        for t in [card.headline, card.line] {
            if let t, !t.isBlank { return t.trimmingCharacters(in: .whitespacesAndNewlines) }
        }
        return nil
    }

    /// The notes under "worth noticing": the ones with a text (nothing else is cut or capped here).
    public static func notes(_ card: BriefingCard) -> [BriefingLink] { withText(card.connections) }

    /// The questions under "next steps": the ones with a text.
    public static func steps(_ card: BriefingCard) -> [BriefingLink] { withText(card.suggestions) }

    private static func withText(_ l: [BriefingLink]) -> [BriefingLink] {
        l.compactMap { n in
            let t = n.text.trimmingCharacters(in: .whitespacesAndNewlines)
            return t.isEmpty ? nil : BriefingLink(text: t, refs: n.refs)
        }
    }

    /// The chips under a note: one per ref that is an item on the card (in the note's order, each once), labelled with the item
    /// ("Weather"; "10:00 Design review"; the title of an ahead / missed item) and doing what a tap on that item does. A ref
    /// that points at nothing, or at an item with no title, is left out; a chip with no tap target is not clickable.
    public static func chips(_ link: BriefingLink, _ card: BriefingCard, weatherLabel: String, clock: (Double) -> String) -> [BriefingChip] {
        var seen = Set<String>()
        var out: [BriefingChip] = []
        for raw in link.refs {
            let ref = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !ref.isEmpty, seen.insert(ref).inserted, let chip = chip(ref, card, weatherLabel: weatherLabel, clock: clock) else { continue }
            out.append(chip)
        }
        return out
    }

    private static func chip(_ ref: String, _ card: BriefingCard, weatherLabel: String, clock: (Double) -> String) -> BriefingChip? {
        if let w = card.weather, ref == (w.id.isBlank ? "w" : w.id) { return BriefingChip(ref: ref, label: weatherLabel) }
        if let e = card.events.first(where: { $0.id == ref }), !e.title.isBlank {
            return BriefingChip(ref: ref, label: clock(e.start) + " " + e.title, tap: tap(e.open))
        }
        if let a = card.ahead.first(where: { $0.id == ref }), !a.title.isBlank { return BriefingChip(ref: ref, label: a.title, tap: tap(a.open)) }
        if let m = card.missed.first(where: { $0.id == ref }), !m.title.isBlank { return BriefingChip(ref: ref, label: m.title, tap: tap(m.open)) }
        return nil
    }

    /// A big event's days in the coming-up line: "11월 12일" or "11월 12일 – 11월 14일", each day written by `day`.
    public static func aheadDays(_ a: BriefingAhead, day: (Int, Int, Int) -> String) -> String {
        func ymd(_ s: String) -> (Int, Int, Int)? {
            let p = s.split(separator: "-").compactMap { Int($0) }
            return p.count == 3 ? (p[0], p[1], p[2]) : nil
        }
        guard let f = ymd(a.first) else { return "" }
        guard let l = ymd(a.last), (l.0, l.1, l.2) > (f.0, f.1, f.2) else { return day(f.0, f.1, f.2) }
        return day(f.0, f.1, f.2) + " – " + day(l.0, l.1, l.2)
    }

    /// The coming-up line: "상하이 여행 11월 12일 – 11월 14일 · 출국 KE893 11월 16일".
    public static func aheadLine(_ card: BriefingCard, day: (Int, Int, Int) -> String) -> String {
        card.ahead.map { a in [a.title, aheadDays(a, day: day)].filter { !$0.isEmpty }.joined(separator: " ") }.joined(separator: " · ")
    }

    /// The label of a missed item's kind.
    public static func kindLabel(_ kind: String) -> StringKey {
        switch kind {
        case "job": R.string.briefing_kind_job
        case "approval": R.string.briefing_kind_approval
        case "login": R.string.briefing_kind_login
        case "mail": R.string.briefing_kind_mail
        case "goal": R.string.briefing_kind_goal
        case "promise": R.string.briefing_kind_promise
        default: R.string.briefing_kind_followup
        }
    }

    /// The hours line can be shown: an id and a real start time.
    public static func hoursShown(_ h: BriefingHours?) -> Bool {
        guard let h else { return false }
        return !h.id.isBlank && ContactHoursRules.minutes(h.wakeStart) != nil
    }
}

/// The morning briefing in the chat: a header, the one line Kelo wrote (when the model was there), then 날씨 / 오늘 일정 / 놓친 것 with
/// only what has something; every item that has a place opens it; a pending contact-hours suggestion at the bottom (accept or keep).
/// The data source's credit sits under the weather.
struct BriefingCardView: View {
    let card: BriefingCard
    let onTap: (BriefingTap) -> Void
    let onHours: (BriefingHours, Bool) -> Void
    @Environment(\.kelo) private var c
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                KeloIconView(.spark, size: 18, tint: c.accent)
                Text(L(R.string.briefing_title)).font(KeloFont.titleMedium.weight(.semibold)).foregroundStyle(c.ink)
                    .frame(maxWidth: .infinity, alignment: .leading).accessibilityAddTraits(.isHeader)
            }
            if let line = BriefingRules.headline(card) {
                Text(line).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            let notes = BriefingRules.notes(card)
            if !notes.isEmpty {
                section(.eye, L(R.string.briefing_connections)) {
                    ForEach(Array(notes.enumerated()), id: \.offset) { _, n in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("•").font(KeloFont.body).foregroundStyle(c.accent)
                                Text(n.text).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            let chips = BriefingRules.chips(n, card, weatherLabel: L(R.string.briefing_weather), clock: { clock($0) })
                            if !chips.isEmpty {
                                FlowLayout(spacing: 6, lineSpacing: 6) {
                                    ForEach(Array(chips.enumerated()), id: \.offset) { _, chip in refChip(chip) }
                                }
                                .padding(.leading, 16)
                            }
                        }
                    }
                }
            }
            if let w = card.weather {
                section(.cloud, L(R.string.briefing_weather)) {
                    let head = [card.place?.name ?? "", w.headline].filter { !$0.isBlank }.joined(separator: " · ")
                    if !head.isBlank { Text(head).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true) }
                    ForEach(Array(w.notes.enumerated()), id: \.offset) { _, n in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("•").font(KeloFont.body).foregroundStyle(c.accent)
                            Text(n.text).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if !w.attribution.isBlank {
                        Button { if let u = URL(string: w.attributionUrl), !w.attributionUrl.isEmpty { openURL(u) } } label: {
                            Text(w.attribution).font(KeloFont.caption).foregroundStyle(c.muted).multilineTextAlignment(.leading)
                        }
                        .buttonStyle(.plain).disabled(w.attributionUrl.isEmpty)
                    }
                }
            }
            if !card.events.isEmpty {
                if card.weather != nil { Hairline() }
                section(.calendar, L(R.string.briefing_today)) {
                    ForEach(Array(card.events.enumerated()), id: \.offset) { _, e in
                        item(title: e.title, lead: clock(e.start), detail: e.place.isBlank ? nil : L(R.string.briefing_event_where, e.place),
                             source: e.source.isBlank ? nil : e.source, tap: BriefingRules.tap(e.open))
                    }
                }
            }
            if !card.ahead.isEmpty {
                // big events this week, as one short line (the server found them: a trip, a flight, a stay, several days)
                if card.weather != nil || !card.events.isEmpty { Hairline() }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    KeloIconView(.clock, size: 16, tint: c.muted)
                    (Text(L(R.string.briefing_coming_up)).fontWeight(.semibold) + Text("  " + BriefingRules.aheadLine(card) { Self.monthDay($0, $1, $2) }))
                        .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
            }
            if !card.missed.isEmpty {
                if card.weather != nil || !card.events.isEmpty || !card.ahead.isEmpty { Hairline() }
                section(.bell, L(R.string.briefing_missed)) {
                    ForEach(Array(card.missed.enumerated()), id: \.offset) { _, m in
                        item(title: m.title, lead: nil, kind: L(BriefingRules.kindLabel(m.kind)), detail: m.detail.isBlank ? nil : m.detail,
                             source: m.source.isBlank ? nil : m.source, tap: BriefingRules.tap(m.open))
                    }
                }
            }
            let steps = BriefingRules.steps(card)
            if !steps.isEmpty {
                if BriefingRules.sections(card).count > 1 { Hairline() }
                section(.arrowRight, L(R.string.briefing_steps)) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { _, st in
                        Button { onTap(.ask(st.text)) } label: {
                            Text(st.text).font(KeloFont.body).foregroundStyle(c.ink).multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 12).padding(.vertical, 8)
                                .background(RoundedRectangle(cornerRadius: 12).fill(c.mine))
                                .contentShape(RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(PressShrinkStyle())
                    }
                }
            }
            if let h = card.hours, BriefingRules.hoursShown(h) { BriefingHoursLine(hours: h, onHours: onHours) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.line, lineWidth: 1))
        .accessibilityIdentifier("kelo.briefing")
    }

    /// A day as the app's language writes a month and day ("11월 12일", "Nov 12").
    @MainActor static func monthDay(_ y: Int, _ m: Int, _ d: Int) -> String {
        let f = DateFormatter(); f.locale = Strings.shared.locale; f.timeZone = TimeZone(identifier: "UTC")
        f.setLocalizedDateFormatFromTemplate("MMMd")
        var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(identifier: "UTC")!
        guard let date = cal.date(from: DateComponents(year: y, month: m, day: d, hour: 12)) else { return "\(m)/\(d)" }
        return f.string(from: date)
    }

    private func clock(_ unix: Double) -> String {
        Date(timeIntervalSince1970: unix).formatted(Date.FormatStyle(date: .omitted, time: .shortened).locale(Strings.shared.locale))
    }

    /// A reference chip: the item's label (wraps, never cut); tappable ones show it in the accent colour and do what the item does.
    @ViewBuilder private func refChip(_ chip: BriefingChip) -> some View {
        let label = Text(chip.label).font(KeloFont.caption).foregroundStyle(chip.tap == nil ? c.muted : c.accent)
            .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(c.line, lineWidth: 1))
        if let tap = chip.tap {
            Button { onTap(tap) } label: { label.contentShape(RoundedRectangle(cornerRadius: 10)) }.buttonStyle(.plain)
        } else {
            label.accessibilityElement(children: .combine)
        }
    }

    private func small(_ t: String) -> some View {
        Text(t).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
    }

    private func section<Content: View>(_ icon: KeloIcon, _ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                KeloIconView(icon, size: 16, tint: c.muted)
                Text(title).font(KeloFont.titleSmall).foregroundStyle(c.muted).accessibilityAddTraits(.isHeader)
            }
            content()
        }
    }

    /// One event or missed item: a time or a small kind label before the title; tappable when it has a place.
    @ViewBuilder private func item(title: String, lead: String?, kind: String? = nil, detail: String?, source: String?, tap: BriefingTap?) -> some View {
        let row = HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                if let kind { Text(kind).font(KeloFont.caption).foregroundStyle(c.accent) }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    if let lead { Text(lead).font(KeloFont.body.weight(.medium)).foregroundStyle(c.ink) }
                    Text(title).font(KeloFont.body).foregroundStyle(c.ink).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                }
                if let detail { Text(detail).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2) }
                if let source {
                    Text(source).font(KeloFont.caption).foregroundStyle(c.muted).lineLimit(1)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(RoundedRectangle(cornerRadius: 6).fill(c.line.opacity(0.45)))
                        .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if tap != nil { KeloIconView(.chevronRight, size: 16, tint: c.muted) }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        if let tap {
            Button { onTap(tap) } label: { row }.buttonStyle(.plain)
        } else {
            row.accessibilityElement(children: .combine)
        }
    }
}

/// "요즘 주말엔 보통 오전 10:00쯤 일어나세요. …" with 바꾸기 / 그대로; after an answer, one line saying what was chosen.
private struct BriefingHoursLine: View {
    let hours: BriefingHours
    let onHours: (BriefingHours, Bool) -> Void
    @Environment(\.kelo) private var c
    @State private var done: Bool?

    var body: some View {
        let h = hours
        let start = ContactHoursRules.minutes(h.wakeStart).map { clock($0 / 60, $0 % 60) } ?? h.wakeStart
        Hairline()
        VStack(alignment: .leading, spacing: 8) {
            Text(L(h.dayType == ContactHoursRules.weekend ? R.string.briefing_hours_weekend : R.string.briefing_hours_weekday, start))
                .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            if let d = done {
                HStack(spacing: 8) {
                    KeloIconView(d ? .check : .minus, size: 16, tint: d ? c.accent : c.muted)
                    Text(d ? L(R.string.hours_suggest_accepted, span(h.wakeStart, h.wakeEnd)) : L(R.string.hours_suggest_kept))
                        .font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
            } else {
                HStack(spacing: 8) {
                    SecondaryButton(L(R.string.briefing_hours_keep), fill: true) { Haptics.warning(); done = false; onHours(h, false) }
                    PrimaryButton(L(R.string.briefing_hours_change)) { Haptics.success(); done = true; onHours(h, true) }
                }
            }
        }
    }

    private func clock(_ hour: Int, _ minute: Int) -> String {
        var comps = DateComponents(); comps.hour = hour; comps.minute = minute; comps.year = 2000; comps.month = 1; comps.day = 1
        let d = Calendar(identifier: .gregorian).date(from: comps) ?? Date()
        return d.formatted(Date.FormatStyle(date: .omitted, time: .shortened).locale(Strings.shared.locale))
    }

    private func span(_ a: String, _ b: String) -> String {
        let x = ContactHoursRules.minutes(a).map { clock($0 / 60, $0 % 60) } ?? a
        let y = ContactHoursRules.minutes(b).map { clock($0 / 60, $0 % 60) } ?? b
        return L(ContactHoursRules.pastMidnight(a, b) ? R.string.hours_span_next_day : R.string.hours_span, x, y)
    }
}
