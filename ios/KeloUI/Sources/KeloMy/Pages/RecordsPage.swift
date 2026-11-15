import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 기록: one week at a time. A strip of the seven days (how much was written each day), then each day with the journal
/// entries the agent wrote and that night's look-back note; below, the nightly look-back's switch and what it learned
/// (preferences to confirm or set aside). Android my/RecordsPage.kt.
struct RecordsPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: RecordsModel
    @State private var expanded: Set<Int64> = []
    @State private var askRebuild: String?
    @State private var askDelete: JournalEntry?
    @State private var showMemory = false
    @State private var askUndo = false

    let api: KeloApi
    init(api: KeloApi) { self.api = api; _model = State(initialValue: RecordsModel(api: api)) }

    private var start: LocalDay { model.start }
    private var today: LocalDay { model.today }
    private var gaps: [JournalGap] { model.gaps }
    private var look: MyLookback? { model.look }
    private var decided: [Int64: String] { model.decided }
    private var hours: HoursSuggestion? { model.hours }
    private var rebuilding: Set<String> { model.rebuilding }

    var body: some View {
        SubPage(L(R.string.my_records_title), onBack: { dismiss() }) {
            weekBar
            if let e = model.entries {
                dayStrip(DigestRules.perDay(start, e))
                let notes = Dictionary((look?.notes ?? []).map { (String($0.date.prefix(10)), $0) }, uniquingKeysWith: { a, _ in a })
                let end = start.adding(days: 6).description
                let dates = Array(Set(e.map { String($0.date.prefix(10)) } + gaps.map { String($0.date.prefix(10)) }
                                      + notes.keys.filter { $0 >= start.description && $0 <= end })).sorted(by: >)
                Text(P(R.plurals.my_records_week_count, e.count, e.count)).font(KeloFont.small).foregroundStyle(c.muted)
                if dates.isEmpty { EmptyNote(L(R.string.my_records_week_empty)) }
                ForEach(dates, id: \.self) { date in day(date, entries: e.filter { $0.date.hasPrefix(date) }, gap: gaps.first { $0.date.hasPrefix(date) }, note: notes[date]) }
            } else if model.weekFailed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }

            SectionLabel(L(R.string.lookback_title)).padding(.horizontal, -16)
            if let data = look {
                if !data.available {
                    MutedText(L(R.string.lookback_unavailable), small: true)
                    if let h = hours { hoursCard(h) }
                } else {
                    MenuCard {
                        ToggleRow(L(R.string.lookback_title), detail: L(R.string.lookback_when),
                                  isOn: Binding(get: { data.enabled }, set: { model.setLookback($0) }))
                        if let run = data.lastRun, let kind = LookbackRules.runLine(run), let line = lastRunLine(run, kind) {
                            Text(line).font(KeloFont.small).foregroundStyle(kind.failedLine ? c.danger : c.muted).padding(.bottom, 10)
                                .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                        }
                        if let pass = data.memoryPass, MemoryPassRules.shown(pass) { memoryLine(pass) }
                    }
                    MutedText(L(R.string.lookback_about), small: true)
                    // the review list: learned message hours first (a row of their own), then the suggested and kept rules
                    if let h = hours { hoursCard(h) }
                    learnedRules(data.rules)
                }
            } else if model.lookFailed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 1)
            }
            // skills Kelo proposed or drafted, waiting for the owner's approval (the same review area as the learned rules)
            SkillReviewSection(api: { [api] in api }, onMessage: { model.setNotice($0) })
        }
        .task(id: start) { await model.loadWeek() }
        .task { await model.loadLookback() }
        .task { await model.loadHours() }
        .alert(askRebuild.map { L(R.string.agent_journal_rebuild_confirm, dayLabel($0)) } ?? "",
               isPresented: Binding(get: { askRebuild != nil }, set: { if !$0 { askRebuild = nil } })) {
            Button(L(R.string.agent_journal_rebuild)) { if let d = askRebuild { model.rebuild(d) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .alert(askDelete.map { L(R.string.agent_journal_delete_confirm, $0.title) } ?? "",
               isPresented: Binding(get: { askDelete != nil }, set: { if !$0 { askDelete = nil } })) {
            Button(L(R.string.delete), role: .destructive) { if let e = askDelete { model.delete(e) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .toast(model.notice, clear: model.clearNotice)
        .sheet(isPresented: $showMemory) { memorySheet }
        .onChange(of: model.memoryClosed) { _, _ in showMemory = false }
    }

    /// The last memory pass in one line ("기억 정리: 3개 추가, 2개 정리") with a link to what changed; after an undo,
    /// "기억 정리를 되돌렸어요". Android agent/LookbackParts.kt MemoryPassLine.
    private func memoryLine(_ pass: MemoryPass) -> some View {
        let text: String
        if pass.undone {
            text = L(R.string.memory_pass_undone)
        } else {
            let added = pass.added > 0 ? P(R.plurals.memory_pass_added, pass.added, pass.added) : nil
            let tidied = pass.tidied > 0 ? P(R.plurals.memory_pass_tidied, pass.tidied, pass.tidied) : nil
            let counts = (added != nil && tidied != nil) ? L(R.string.memory_pass_both, added!, tidied!) : (added ?? tidied ?? "")
            text = L(R.string.memory_pass_line, counts)
        }
        return HStack(spacing: 8) {
            Text(text).font(KeloFont.small).foregroundStyle(c.muted).frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
            if !pass.undone {
                LinkButton(L(R.string.memory_pass_view)) { model.openMemory(); showMemory = true }
            }
        }
        .padding(.bottom, 10)
    }

    private func areaName(_ a: MemoryArea) -> String {
        switch a {
        case .aboutYou: return L(R.string.memory_area_about_you)
        case .toRemember: return L(R.string.memory_area_to_remember)
        case .people: return L(R.string.memory_area_people)
        case .workingNotes: return L(R.string.memory_area_working_notes)
        }
    }

    /// What the last memory pass changed, by area ("+" new or rewritten lines, "−" lines taken out), with its undo.
    private var memorySheet: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(L(R.string.memory_pass_changes_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if let d = model.memChanges {
                    let groups = MemoryPassRules.group(d.changes)
                    if groups.isEmpty { MutedText(L(R.string.memory_pass_no_lines), small: true) }
                    ForEach(Array(groups.enumerated()), id: \.offset) { _, g in
                        Text(areaName(g.area)).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.top, 4)
                        MenuCard {
                            VStack(alignment: .leading, spacing: 6) {
                                ForEach(Array(g.added.enumerated()), id: \.offset) { _, line in changedLine(line, added: true) }
                                ForEach(Array(g.removed.enumerated()), id: \.offset) { _, line in changedLine(line, added: false) }
                            }
                            .padding(.vertical, 12)
                        }
                    }
                    if d.lastPass?.canUndo == true {
                        SecondaryButton(L(R.string.memory_pass_undo), fill: true) { askUndo = true }
                            .disabled(model.undoing).padding(.top, 4)
                    }
                } else if model.memFailed {
                    ErrorText(L(R.string.connect_failed), small: true)
                } else {
                    SkeletonRows(rows: 3)
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(c.bg.ignoresSafeArea())
        .presentationDetents([.medium, .large])
        .alert(L(R.string.memory_pass_undo_confirm), isPresented: $askUndo) {
            Button(L(R.string.memory_pass_undo)) { model.undoMemory() }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func changedLine(_ text: String, added: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(added ? "+" : "\u{2212}").font(KeloFont.body).fontWeight(.bold).foregroundStyle(added ? c.accent : c.muted).frame(width: 12)
            Text(text).font(KeloFont.small).foregroundStyle(added ? c.ink : c.muted).strikethrough(!added, color: c.muted)
                .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(L(added ? R.string.memory_pass_added_line : R.string.memory_pass_removed_line, text))
    }

    private func dayLabel(_ date: String) -> String {
        guard let d = DayGroups.parseDay(date) else { return date }
        return MyFormat.monthDay(year: d.year, month: d.month, day: d.day)
    }

    private var weekBar: some View {
        let canNext = DigestRules.canGoNext(start, today: today)
        let a = start, b = start.adding(days: 6)
        return HStack {
            Button { model.previousWeek() } label: { KeloIconView(.chevronLeft, size: 22, tint: c.ink).frame(width: 44, height: 44) }
                .buttonStyle(.plain).accessibilityLabel(L(R.string.my_records_prev_week))
            Text(L(R.string.my_records_week_range, MyFormat.monthDay(year: a.year, month: a.month, day: a.day), MyFormat.monthDay(year: b.year, month: b.month, day: b.day)))
                .font(KeloFont.titleMedium).foregroundStyle(c.ink).multilineTextAlignment(.center).frame(maxWidth: .infinity)
            Button { model.nextWeek() } label: { KeloIconView(.chevronRight, size: 22, tint: canNext ? c.ink : c.disabledInk).frame(width: 44, height: 44) }
                .buttonStyle(.plain).disabled(!canNext).accessibilityLabel(L(R.string.my_records_next_week))
        }
    }

    private func dayStrip(_ counts: [Int]) -> some View {
        let most = max(counts.max() ?? 0, 1)
        let symbols = Calendar.current.veryShortStandaloneWeekdaySymbols   // Sunday first
        return HStack(alignment: .bottom, spacing: 0) {
            ForEach(0..<7, id: \.self) { i in
                let d = start.adding(days: i)
                VStack(spacing: 4) {
                    RoundedRectangle(cornerRadius: 4).fill(counts[i] > 0 ? c.accent : c.line)
                        .frame(width: 14, height: CGFloat(6 + 30 * counts[i] / most))
                        .frame(height: 36, alignment: .bottom)
                    Text(symbols[(i + 1) % 7]).font(KeloFont.label).foregroundStyle(d == today ? c.accent : c.muted)
                        .fontWeight(d == today ? .bold : .regular)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .accessibilityHidden(true)
    }

    @ViewBuilder private func day(_ date: String, entries: [JournalEntry], gap: JournalGap?, note: LookbackNote?) -> some View {
        HStack {
            Text(dayLabel(date)).font(KeloFont.titleSmall).foregroundStyle(c.accent)
            Spacer(minLength: 8)
            if rebuilding.contains(date) { ProgressView().controlSize(.small) }
            else if let p = DayGroups.parseDay(date), p < today { LinkButton(L(R.string.agent_journal_rebuild)) { askRebuild = date } }
        }
        .padding(.top, 6)
        MenuCard {
            ForEach(Array(entries.enumerated()), id: \.element.id) { i, e in
                if i > 0 { Hairline() }
                entryRow(e)
            }
            if let g = gap {
                Text(L(g.status == "failed" ? R.string.agent_journal_failed : R.string.agent_journal_pending)
                     + (g.status == "failed" && !g.reason.isEmpty ? " · " + g.reason : ""))
                    .font(KeloFont.small).foregroundStyle(c.muted).padding(.vertical, 14).frame(maxWidth: .infinity, alignment: .leading)
            }
            if let n = note {
                if !entries.isEmpty || gap != nil { Hairline() }
                VStack(alignment: .leading, spacing: 4) {
                    Text(L(R.string.my_records_night_note)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
                    Text((try? AttributedString(markdown: n.text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(n.text))
                        .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                .padding(.vertical, 14).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func entryRow(_ e: JournalEntry) -> some View {
        let open = expanded.contains(e.id)
        return HStack(alignment: .top, spacing: 8) {
            Button {
                if open { expanded.remove(e.id) } else { expanded.insert(e.id) }
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(e.title).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    if !e.summary.isEmpty {
                        Text(e.summary).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(open ? nil : 2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button { askDelete = e } label: { KeloIconView(.trash, size: 18, tint: c.muted).frame(width: 36, height: 36) }
                .buttonStyle(.plain).accessibilityLabel(L(R.string.agent_journal_delete))
        }
        .padding(.vertical, 10)
    }

    /// The last run in one plain sentence ("어젯밤 정리했어요 · 새 규칙 2개 제안") from the server's structured fields only; its log
    /// text is never shown. Android agent/LookbackParts.kt LastRunLine.
    private func lastRunLine(_ run: LookbackRun, _ kind: RunLine) -> String? {
        var whenText = ""
        if let at = run.at {
            switch LookbackRules.runWhen(at, now: Date()) {
            case .lastNight: whenText = L(R.string.lookback_at_last_night)
            case .today: whenText = L(R.string.lookback_at_today)
            case .day:
                let d = Calendar.current.dateComponents([.year, .month, .day], from: Date(timeIntervalSince1970: at))
                whenText = L(R.string.lookback_at_day, MyFormat.monthDay(year: d.year ?? 2000, month: d.month ?? 1, day: d.day ?? 1))
            }
        } else if kind != .noModel {
            return nil
        }
        var text: String
        switch kind {
        case .done(let n):
            text = L(R.string.lookback_line_done, whenText) + " · "
                + (n > 0 ? P(R.plurals.lookback_line_new, n, n) : L(R.string.lookback_line_none))
        case .noChat: text = L(R.string.lookback_line_no_chat, whenText)
        case .switchedOff: text = L(R.string.lookback_line_off, whenText)
        case .inUse: text = L(R.string.lookback_line_busy, whenText)
        case .skipped: text = L(R.string.lookback_line_skipped, whenText)
        case .failedModel: text = L(R.string.lookback_line_failed_why, whenText, L(R.string.lookback_fail_model))
        case .failedAnswer: text = L(R.string.lookback_line_failed_why, whenText, L(R.string.lookback_fail_answer))
        case .failed: text = L(R.string.lookback_line_failed, whenText)
        case .noModel: text = L(R.string.lookback_line_no_model)
        }
        return text.prefix(1).uppercased() + text.dropFirst()
    }

    @ViewBuilder private func hoursCard(_ h: HoursSuggestion) -> some View {
        HoursSuggestionCard(suggestion: h, done: model.hoursDone) { accept in model.answerHours(h, accept: accept) }
    }

    @ViewBuilder private func learnedRules(_ rules: [LearnedRule]) -> some View {
        let groups = LookbackRules.group(rules, justDecided: Set(decided.keys))
        if groups.proposed.isEmpty && groups.accepted.isEmpty && groups.rejected.isEmpty {
            MenuCard { MutedText(L(R.string.lookback_rules_empty), small: true).padding(.vertical, 14) }
        }
        if !groups.proposed.isEmpty {
            Text(P(R.plurals.lookback_rules_proposed, groups.proposed.count, groups.proposed.count)).font(KeloFont.titleSmall).foregroundStyle(c.accent)
            MenuCard {
                ForEach(Array(groups.proposed.enumerated()), id: \.element.id) { i, r in
                    if i > 0 { Hairline() }
                    VStack(alignment: .leading, spacing: 8) {
                        Text(r.rule).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        if let basis = basis(r) {
                            Text(basis).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                        }
                        if let choice = decided[r.id] {
                            HStack(spacing: 8) {
                                KeloIconView(choice == LearnedRule.accepted ? .check : .minus, size: 18, tint: choice == LearnedRule.accepted ? c.accent : c.muted)
                                Text(L(choice == LearnedRule.accepted ? R.string.lookback_rule_kept : R.string.lookback_rule_discarded))
                                    .font(KeloFont.body).foregroundStyle(c.ink).frame(maxWidth: .infinity, alignment: .leading)
                                LinkButton(L(R.string.undo)) { Haptics.tick(); model.undoDecision(r.id) }
                            }
                        } else {
                            if LookbackRules.inUse(r) {
                                Text(L(R.string.lookback_rule_in_use)).font(KeloFont.label).foregroundStyle(c.muted)
                            }
                            HStack(spacing: 8) {
                                SecondaryButton(L(R.string.lookback_rule_discard), fill: true) { model.decide(r.id, LearnedRule.rejected) }
                                PrimaryButton(L(R.string.lookback_rule_keep)) { Haptics.success(); model.decide(r.id, LearnedRule.accepted) }
                            }
                        }
                    }
                    .padding(.vertical, 12)
                }
            }
        }
        if !groups.accepted.isEmpty {
            Text(L(R.string.lookback_rules_accepted)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
            MenuCard {
                ForEach(Array(groups.accepted.enumerated()), id: \.element.id) { i, r in
                    if i > 0 { Hairline() }
                    VStack(alignment: .leading, spacing: 4) {
                        ruleText(r)
                        LinkButton(L(R.string.lookback_rule_stop)) { model.setRule(r.id, LearnedRule.rejected) }
                    }
                    .padding(.vertical, 10)
                }
            }
        }
        if !groups.rejected.isEmpty {
            DisclosureGroup(P(R.plurals.lookback_rules_rejected, groups.rejected.count, groups.rejected.count)) {
                ForEach(groups.rejected, id: \.id) { r in
                    VStack(alignment: .leading, spacing: 4) {
                        ruleText(r)
                        LinkButton(L(R.string.lookback_rule_restore)) { model.setRule(r.id, LearnedRule.accepted) }
                    }
                    .padding(.vertical, 6)
                }
            }
            .font(KeloFont.small).foregroundStyle(c.muted)
        }
    }

    private func ruleText(_ r: LearnedRule) -> some View {
        let open = expanded.contains(-r.id - 1)
        return Button {
            if open { expanded.remove(-r.id - 1) } else { expanded.insert(-r.id - 1) }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(r.rule).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                if let day = ruleDay(r) { Text(day).font(KeloFont.label).foregroundStyle(c.muted) }
                if open {
                    // why the look-back thinks so (its own words), or a plain line when it gave none
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L(R.string.lookback_rule_why)).font(KeloFont.label).foregroundStyle(c.muted)
                        Text(r.evidence.isEmpty ? L(R.string.lookback_rule_no_evidence) : r.evidence).font(KeloFont.small).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(RoundedRectangle(cornerRadius: 8).fill(c.bubble.opacity(0.6)))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint(L(R.string.lookback_rule_why))
    }

    /// "11월 8일" from the rule's YYYY-MM-DD date.
    private func ruleDay(_ r: LearnedRule) -> String? {
        let p = r.date.prefix(10).split(separator: "-").compactMap { Int($0) }
        return p.count == 3 ? MyFormat.monthDay(year: p[0], month: p[1], day: p[2]) : nil
    }

    /// One line of why it was suggested: "근거: 11월 8일 · 캘린더 일정 등록 허락을 3번 거절했어요".
    private func basis(_ r: LearnedRule) -> String? {
        let ev = r.evidence.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = [ruleDay(r), ev.isEmpty ? nil : ev].compactMap { $0 }.joined(separator: " · ")
        return body.isEmpty ? nil : L(R.string.lookback_rule_basis, body)
    }
}
