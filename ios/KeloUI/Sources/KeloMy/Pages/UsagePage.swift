import SwiftUI
import KeloCore
import KeloShared

/// The usage words (Android my/UsageUi.kt).
@MainActor enum UsageText {
    static var labels: [(String, String)] {
        Array(zip(UsageLogic.labelSources, [L(R.string.usage_label_session), L(R.string.usage_label_week), L(R.string.usage_label_all_models),
                                            L(R.string.usage_label_weekly), L(R.string.usage_label_five_hour)]))
    }

    static func groups(_ u: MyUsage?, model: String) -> [UsageGroup] {
        let joiner = L(R.string.usage_group_joiner)
        return UsageLogic.groups(u, model: model) { g in UsageLogic.groupName(g, models: { L(R.string.usage_group_models, $0) }, joiner: joiner) }
    }

    /// "42% 사용" for providers with limits, else today's tokens; nil while unknown.
    static func summary(_ u: MyUsage?, model: String) -> String? {
        if let p = UsageLogic.summaryPercent(groups(u, model: model)) { return L(R.string.usage_used, p) }
        if let u, u.subscription == nil { return L(R.string.my_usage_today_tokens, MyFormat.number(u.today.sum)) }
        return nil
    }

    static func reset(_ w: UsageWindow) -> String? {
        let now = Date().timeIntervalSince1970
        guard let r = UsageLogic.reset(w, now: now) else { return nil }
        switch UsageLogic.phrase(r, now: now) {
        case .soon: return L(R.string.usage_reset_soon_phrase)
        case .inMinutes(let n): return L(R.string.usage_reset_in_minutes, n)
        case .inHours(let n): return L(R.string.usage_reset_in_hours, n)
        case .on(let at): return L(R.string.usage_reset_on, MyFormat.monthDayTime(at))
        case .raw(let t): return L(R.string.usage_resets, TokenText.fit(t, max: 24))
        }
    }
}

/// The label and "42% 사용", the bar, and when it resets.
struct UsageBar: View {
    let w: UsageWindow
    let compact: Bool
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(UsageLogic.label(w.label, UsageText.labels)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
                Spacer(minLength: 8)
                Text(L(R.string.usage_used, w.usedPercent)).font(KeloFont.button).foregroundStyle(c.ink)
            }
            KeloBar(UsageLogic.barFraction(w.usedPercent), hot: UsageLogic.isHot(w.usedPercent))
            let reset = UsageText.reset(w)
            let line: String = compact ? (reset ?? "") : {
                let left = L(R.string.usage_left, UsageLogic.left(w.usedPercent))
                return reset.map { L(R.string.usage_left_and_reset, left, $0) } ?? left
            }()
            if !line.isEmpty { MutedText(line, small: true) }
        }
        .padding(.vertical, 6)
    }
}

/// One block per group of models; the one in use first and marked, the others one line each in the short form.
struct UsageGroupsView: View {
    let groups: [UsageGroup]
    let full: Bool
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(groups.enumerated()), id: \.offset) { i, g in
                if i > 0 { Hairline().padding(.top, 12).padding(.bottom, 10) }
                if let name = g.group, !name.isEmpty {
                    HStack(spacing: 8) {
                        Text(g.name).font(KeloFont.button).foregroundStyle(c.ink).lineLimit(2)
                        if g.active { KeloTag(L(R.string.usage_in_use)) }
                    }
                    .padding(.top, i == 0 ? 6 : 0).padding(.bottom, 2)
                }
                if UsageLogic.oneLine(groups, g, full: full) {
                    MutedText(g.windows.map { L(R.string.usage_one_line_part, UsageLogic.label($0.label, UsageText.labels), $0.usedPercent) }
                        .joined(separator: L(R.string.usage_group_joiner)), small: true).padding(.top, 4)
                } else {
                    ForEach(Array(g.windows.enumerated()), id: \.offset) { _, w in UsageBar(w: w, compact: !full) }
                }
            }
        }
    }
}

/// All the limits of the chosen model, or (when it reports none) the tokens this app has used; and API-key balances.
struct UsagePage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var usage: MyUsage?
    @State private var providers: [MyProvider] = []

    var body: some View {
        let model = app.profile?.model ?? ""
        SubPage(L(R.string.usage_title), onBack: { dismiss() }) {
            MyCard {
                if let u = usage {
                    if let sub = u.subscription {
                        HStack {
                            Text(label(sub.provider)).font(KeloFont.titleSmall).foregroundStyle(c.ink).lineLimit(2)
                            Spacer(minLength: 8)
                            LinkButton(L(R.string.refresh)) { Task { await load(refresh: true) } }
                        }
                        if let err = sub.error { MutedText(err, small: true) }
                        else { UsageGroupsView(groups: UsageText.groups(u, model: model), full: true) }
                    } else {
                        MutedText(L(R.string.usage_no_limits_app_tokens), small: true)
                        StatLine(label: L(R.string.usage_today), value: MyFormat.number(u.today.sum))
                        StatLine(label: L(R.string.usage_total), value: MyFormat.number(u.total.sum))
                    }
                } else {
                    SkeletonRows(rows: 3)
                }
            }
            if let jobs = usage?.jobs { JobUsageCard(jobs: jobs) }
            ForEach(Array((usage?.accounts ?? []).filter { $0.error == nil }.enumerated()), id: \.offset) { _, a in
                MyCard {
                    Text(a.displayName.isEmpty ? label(a.provider) : a.displayName).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                    if let v = a.balance { StatLine(label: L(R.string.balance_left), value: MyFormat.money(v, currency: a.currency)) }
                    if let v = a.limitRemaining {
                        StatLine(label: L(R.string.balance_limit_left),
                                 value: MyFormat.money(v, currency: a.currency) + (a.limit.map { " / " + MyFormat.money($0, currency: a.currency) } ?? ""))
                    }
                    if let v = a.usedToday { StatLine(label: L(R.string.balance_used_today), value: MyFormat.money(v, currency: a.currency)) }
                    if let v = a.usedWeek { StatLine(label: L(R.string.balance_used_week), value: MyFormat.money(v, currency: a.currency)) }
                    if let v = a.usedMonth { StatLine(label: L(R.string.balance_used_month), value: MyFormat.money(v, currency: a.currency)) }
                }
            }
        }
        .task {
            await load(refresh: false)
            providers = ((try? await app.requireApi().request("GET", "/api/providers")).map(MyProvider.list)) ?? []
        }
    }

    private func label(_ name: String) -> String { providers.first { $0.name == name }?.displayName ?? name }

    private func load(refresh: Bool) async {
        if let u = try? await app.requireApi().get("/api/usage" + (refresh ? "?refresh=true" : ""), as: MyUsage.self) { usage = u }
    }
}

/// A kind of work in the app's words (Android my/UsageUi.kt jobKindLabel).
@MainActor func jobKindLabel(_ kind: String) -> String {
    switch UsageLogic.jobKind(kind) {
    case "chat": L(R.string.usage_kind_chat)
    case "schedule": L(R.string.usage_kind_schedule)
    case "checklist": L(R.string.usage_kind_checklist)
    case "task": L(R.string.usage_kind_task)
    case "worker": L(R.string.usage_kind_worker)
    case "reflection": L(R.string.usage_kind_reflection)
    case "memory": L(R.string.usage_kind_memory)
    case "journal": L(R.string.usage_kind_journal)
    case "proactive": L(R.string.usage_kind_proactive)
    case "compaction": L(R.string.usage_kind_compaction)
    case "followup": L(R.string.usage_kind_followup)
    case "briefing": L(R.string.usage_kind_briefing)
    case "probe": L(R.string.usage_kind_probe)
    default: L(R.string.usage_kind_other)
    }
}

/// Where the tokens went, today and this month, by kind of work, with the share reused from the provider's cache when it says.
struct JobUsageCard: View {
    let jobs: JobUsage
    @Environment(\.kelo) private var c

    var body: some View {
        MyCard {
            Text(L(R.string.usage_jobs_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
            ForEach(Array([(L(R.string.usage_today), jobs.today), (L(R.string.usage_jobs_month), jobs.month)].enumerated()), id: \.offset) { i, pair in
                let rows = UsageLogic.jobRows(pair.1)
                if i > 0 { Hairline().padding(.vertical, 8) }
                JobLine(label: pair.0, tokens: rows.isEmpty ? nil : UsageLogic.partTokens(pair.1),
                        cachePercent: UsageLogic.cachePercent(pair.1.cacheReadTokens, pair.1.inputTokens), bold: true)
                if rows.isEmpty { MutedText(L(R.string.usage_jobs_empty), small: true) }
                ForEach(Array(rows.enumerated()), id: \.offset) { _, r in JobLine(label: jobKindLabel(r.kind), tokens: r.tokens, cachePercent: r.cachePercent) }
            }
            MutedText(L(R.string.usage_jobs_note), small: true).padding(.top, 6)
        }
    }
}

/// A label that wraps on the left, its tokens on the right, and the cached share under it (only when reported).
private struct JobLine: View {
    let label: String
    let tokens: Int64?
    let cachePercent: Int?
    var bold = false
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(label).font(bold ? KeloFont.button : KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                if let tokens { Text(L(R.string.usage_jobs_tokens, MyFormat.number(tokens))).font(KeloFont.button).foregroundStyle(c.ink) }
            }
            if let p = cachePercent, tokens != nil { MutedText(L(R.string.usage_jobs_cache, p), small: true) }
        }
        .padding(.vertical, 4)
    }
}
