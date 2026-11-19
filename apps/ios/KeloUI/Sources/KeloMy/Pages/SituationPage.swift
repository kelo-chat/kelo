import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 알림 → "What Kelo looked at": when Kelo looks at what, its passes by day (tap one for its detail).
/// Read-only; no ids, tool names or JSON are shown (Android my/SituationPage.kt SituationPage).
struct SituationPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @Environment(AppModel.self) private var app
    @State private var model: SituationModel

    init(api: KeloApi) { _model = State(initialValue: SituationModel(api: api)) }

    var body: some View {
        SubPage(L(R.string.situation_title), onBack: { dismiss() }) {
            if !model.error.isEmpty && model.list == nil {
                ErrorText(model.error, small: true)
            } else if model.missing {
                MutedText(L(R.string.notify_page_unavailable), small: true)
            } else if let list = model.list {
                if let looks = list.looks {
                    ForEach(Array(SituationRules.shownLooks(looks).enumerated()), id: \.offset) { _, look in LookGroup(look: look) }
                } else {   // an older server: what it covers, as before
                    let hourly = SituationRules.hourly(list.lookedAt)
                    let onDemand = SituationRules.onDemand(list.lookedAt)
                    if !hourly.isEmpty {
                        SectionLabel(L(R.string.situation_hourly_head))
                        NameBox(names: hourly.map { lookedName($0) })
                    }
                    if !onDemand.isEmpty {
                        SectionLabel(L(R.string.situation_read_when_needed_head))
                        MutedText(L(R.string.situation_read_when_needed_about), small: true)
                        NameBox(names: onDemand.map { lookedName($0) })
                    }
                }
                if list.passes.isEmpty {
                    EmptyNote(L(R.string.situation_empty))
                } else {
                    let groups = SituationRules.groupPasses(list.passes, today: LocalDay(seconds: Date().timeIntervalSince1970))
                    ForEach(Array(groups.enumerated()), id: \.offset) { _, g in
                        SectionLabel(MyFormat.dayLabel(g.label))
                        VStack(spacing: 0) {
                            ForEach(Array(g.items.enumerated()), id: \.offset) { i, pass in
                                if i > 0 { Hairline() }
                                Button { MyNav(app: app).push(.situationPass(pass.id)) } label: { PassRow(pass: pass) }
                                    .buttonStyle(.plain)
                            }
                        }
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                    }
                }
                MutedText(L(R.string.situation_keep_days, list.keepDays), small: true)
                if list.nextBefore != nil {
                    Button(L(R.string.situation_load_more)) { Task { await model.loadMore() } }
                        .disabled(model.loadingMore)
                        .font(KeloFont.body)
                        .foregroundStyle(c.accent)
                }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
    }

}

/// The detail of one pass: what changed, the same as before (a count), what could not be read, what was turned off, and what Kelo did.
struct SituationDetailPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: SituationDetailModel

    init(api: KeloApi, passId: Int) { _model = State(initialValue: SituationDetailModel(api: api, passId: passId)) }

    var body: some View {
        SubPage(title, onBack: { dismiss() }) {
            if !model.error.isEmpty && model.detail == nil {
                ErrorText(model.error, small: true)
            } else if model.missing {
                MutedText(L(R.string.notify_page_unavailable), small: true)
            } else if let d = model.detail {
                let buckets = SituationRules.bucketed(d.sources)
                let same = buckets[.same] ?? []
                if let changed = buckets[.changed], !changed.isEmpty { group(R.string.situation_detail_changed, changed) }
                if !same.isEmpty { MutedText(L(R.string.situation_detail_same, same.count), small: true) }
                if let failed = buckets[.failed], !failed.isEmpty { group(R.string.situation_detail_failed, failed) }
                if let off = buckets[.off], !off.isEmpty { group(R.string.situation_detail_off, off) }
                if let other = buckets[.other], !other.isEmpty { group(R.string.situation_state_other, other) }
                SectionLabel(L(R.string.situation_detail_done))
                let groups = SituationRules.groupEvents(d.events)
                if groups.isEmpty {
                    MutedText(L(R.string.situation_detail_none), small: true)
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(groups.enumerated()), id: \.offset) { i, g in
                            if i > 0 { Hairline() }
                            EventGroupRow(group: g)
                        }
                    }
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
    }

    private var title: String {
        guard let d = model.detail, d.pass.at > 0 else { return L(R.string.situation_title) }
        let day = LocalDay(seconds: d.pass.at)
        return L(R.string.agent_day_time, MyFormat.monthDay(year: day.year, month: day.month, day: day.day), MyFormat.clock(d.pass.at))
    }

    @ViewBuilder private func group(_ heading: StringKey, _ sources: [SituationSource]) -> some View {
        SectionLabel(L(heading))
        VStack(spacing: 0) {
            ForEach(Array(sources.enumerated()), id: \.offset) { i, s in
                if i > 0 { Hairline() }
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(sourceName(s)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
                    Spacer(minLength: 0)
                    Text(stateText(s.state)).font(KeloFont.small).foregroundStyle(c.muted)
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
    }
}

/// A cover's name: a section by its words, anything else as the server named it.
@MainActor private func lookedName(_ l: SituationLooked) -> String {
    if l.kind.trimmingCharacters(in: .whitespacesAndNewlines) == "section" { return sectionName(l.id) }
    return l.name.isEmpty ? L(R.string.situation_section_other) : l.name
}

/// "HH:MM" in the phone's clock format (as the notification settings show the send times).
@MainActor private func lookTimeWords(_ text: String) -> String {
    NotifySettingsRules.parseTime(text).map { MyFormat.clock($0.hour, $0.minute) } ?? text
}

/// A box of names (the covers of the look).
private struct NameBox: View {
    let names: [String]
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(names.enumerated()), id: \.offset) { i, name in
                if i > 0 { Hairline() }
                Text(name).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14).padding(.vertical, 12)
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
    }
}

/// One group of the page: its heading, its times and a note where it has them, then what is looked at (Android LookGroup).
private struct LookGroup: View {
    let look: SituationLook

    var body: some View {
        let kind = SituationRules.lookKind(look.whenCode)
        let times = SituationRules.lookTimes(look).map(lookTimeWords).joined(separator: " · ")
        switch kind {
        case .hourly:
            SectionLabel(L(R.string.situation_hourly_head))
        case .send:
            SectionLabel(L(R.string.situation_send_head))
            if !times.isEmpty { MutedText(L(R.string.heads_send_times, times), small: true) }
            MutedText(L(R.string.situation_send_about), small: true)
        case .briefing:
            SectionLabel(L(R.string.situation_briefing_head))
            if !times.isEmpty { MutedText(L(R.string.situation_briefing_time, times), small: true) }
        case .needed:
            SectionLabel(L(R.string.situation_read_when_needed_head))
            MutedText(L(R.string.situation_read_when_needed_about), small: true)
        case .other:
            EmptyView()
        }
        if kind != .other { NameBox(names: look.covers.map { lookedName($0) }) }
    }
}

/// One pass in a day's list: its time, what it came to, and a chevron.
private struct PassRow: View {
    let pass: SituationPass
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 10) {
            Text(MyFormat.clock(pass.at)).font(KeloFont.body).foregroundStyle(c.muted)
            Text(outcomeText).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
            KeloIconView(.chevronRight, size: 16, tint: c.muted)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .contentShape(Rectangle())
    }

    private var outcomeText: String {
        switch SituationRules.outcome(pass.status) {
        case .noChange: L(R.string.situation_outcome_no_change)
        case .nothing: L(R.string.situation_outcome_nothing)
        case .proposed: L(R.string.situation_outcome_proposed, pass.counts.proposed)
        case .selected: L(R.string.situation_outcome_selected, pass.counts.selected)
        case .delivered: L(R.string.situation_outcome_delivered)
        case .running: L(R.string.situation_outcome_running)
        case .failed: L(R.string.situation_outcome_failed)
        case .other: L(R.string.situation_outcome_other)
        }
    }
}

/// One item's timeline in a detail: its title, then each status change with its time.
private struct EventGroupRow: View {
    let group: SituationRules.EventGroup
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(group.title.isEmpty ? L(R.string.situation_section_other) : group.title)
                .font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
            ForEach(Array(group.events.enumerated()), id: \.offset) { _, e in
                HStack(spacing: 8) {
                    Text(MyFormat.clock(e.at)).font(KeloFont.small).foregroundStyle(c.muted)
                    Text(eventText(e)).font(KeloFont.small).foregroundStyle(c.muted)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14).padding(.vertical, 12)
    }

    private func eventText(_ e: SituationEvent) -> String {
        switch SituationRules.eventKind(status: e.status, priority: e.priority) {
        case .proposed: L(R.string.situation_event_proposed)
        case .updated, .other: L(R.string.situation_event_updated)
        case .selectedToday: L(R.string.situation_event_selected_today)
        case .selectedRegular: L(R.string.situation_event_selected_regular)
        case .told: L(R.string.situation_event_told)
        case .dropped:
            e.why.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || e.why.trimmingCharacters(in: .whitespacesAndNewlines) == "expired"
                ? L(R.string.situation_event_dropped_none) : L(R.string.situation_event_dropped, e.why.trimmingCharacters(in: .whitespacesAndNewlines))
        case .expired: L(R.string.situation_event_expired)
        case .waiting: L(R.string.situation_event_waiting)
        }
    }
}

/// The name of a section, by the app's words (an unknown code is "Other").
@MainActor func sectionName(_ code: String) -> String {
    switch code.trimmingCharacters(in: .whitespacesAndNewlines) {
    case "conversation": L(R.string.situation_section_conversation)
    case "mail": L(R.string.situation_section_mail)
    case "calendar": L(R.string.situation_section_calendar)
    case "goals": L(R.string.situation_section_goals)
    case "schedules": L(R.string.situation_section_schedules)
    case "memory": L(R.string.situation_section_memory)
    case "corrections": L(R.string.situation_section_corrections)
    case "held": L(R.string.deliveries_held_title)
    case "weather": L(R.string.briefing_weather)
    case "missed": L(R.string.briefing_missed)
    default: L(R.string.situation_section_other)
    }
}

/// A source's name: a section by its words, anything else as the server named it.
@MainActor func sourceName(_ s: SituationSource) -> String {
    if SituationRules.isSection(s) { return sectionName(s.id) }
    return s.name.isEmpty ? L(R.string.situation_section_other) : s.name
}

/// A source's state, in the detail's words (an unknown state is "Other").
@MainActor func stateText(_ state: String) -> String {
    switch SituationRules.bucket(state) {
    case .changed: state.trimmingCharacters(in: .whitespacesAndNewlines) == "new" ? L(R.string.situation_state_new) : L(R.string.situation_state_changed)
    case .same: L(R.string.situation_state_same)
    case .failed:
        switch state.trimmingCharacters(in: .whitespacesAndNewlines) {
        case "timeout": L(R.string.situation_state_timeout)
        case "skipped": L(R.string.situation_state_skipped)
        default: L(R.string.situation_state_failed)
        }
    case .off: L(R.string.situation_state_off)
    case .other: L(R.string.situation_state_other)
    }
}
