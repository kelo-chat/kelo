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
@MainActor private func roleWord(_ role: String) -> String? {
    switch role {
    case "background": return L(R.string.usage_role_background)
    case "fallback": return L(R.string.usage_role_fallback)
    default: return nil
    }
}

/// The limits section: the current model's service first, then other services folded open.
struct LimitsSection: View {
    let usage: MyUsage?
    let chosenModel: String
    var providers: [ModelProvider] = []
    let labelForProvider: (String) -> String
    let onRefresh: () -> Void
    var onAdminKey: ((String) -> Void)? = nil
    var onRemoveAdminKey: ((String) -> Void)? = nil

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.kelo) private var c
    @State private var open: Bool = false

    var body: some View {
        let parts = UsageLogic.limitParts(usage)
        MyCard {
            if let u = usage {
                if parts.current != nil || parts.currentAccount != nil {
                    let current = parts.current
                    let currentAccount = parts.currentAccount
                    let serviceLabel: String = {
                        if let current { return labelForProvider(current.provider) }
                        if let a = currentAccount { return a.displayName.isEmpty ? labelForProvider(a.provider) : a.displayName }
                        return ""
                    }()
                    HStack {
                        Text(L(R.string.usage_limits_current, serviceLabel))
                            .font(KeloFont.titleSmall).foregroundStyle(c.ink).lineLimit(2)
                        Spacer(minLength: 8)
                        LinkButton(L(R.string.refresh), action: onRefresh)
                    }
                    if let current {
                        if let err = current.error {
                            MutedText(err, small: true)
                        } else {
                            UsageGroupsView(groups: UsageText.groups(MyUsage(subscription: current), model: chosenModel), full: true)
                        }
                    } else if let a = currentAccount {
                        if let v = a.balance { StatLine(label: L(R.string.balance_left), value: MyFormat.money(v, currency: a.currency)) }
                        if let v = a.limitRemaining {
                            StatLine(label: L(R.string.balance_limit_left),
                                     value: MyFormat.money(v, currency: a.currency) + (a.limit.map { " / " + MyFormat.money($0, currency: a.currency) } ?? ""))
                        }
                        if let v = a.usedToday { StatLine(label: L(R.string.balance_used_today), value: MyFormat.money(v, currency: a.currency)) }
                        if let v = a.usedWeek { StatLine(label: L(R.string.balance_used_week), value: MyFormat.money(v, currency: a.currency)) }
                        if let v = a.usedMonth { StatLine(label: L(R.string.balance_used_month), value: MyFormat.money(v, currency: a.currency)) }
                        let info = providers.first(where: { $0.name == a.provider })?.adminKey
                        if let err = a.adminError {
                            MutedText(err, small: true)
                        }
                        if info?.supported == true {
                            if info?.set == true {
                                HStack {
                                    MutedText(L(R.string.admin_key_set, info?.hint ?? ""), small: true)
                                    Spacer(minLength: 8)
                                    LinkButton(L(R.string.admin_key_remove), danger: true, action: { onRemoveAdminKey?(a.provider) })
                                }
                            } else {
                                LinkButton(L(R.string.admin_key_add), action: { onAdminKey?(a.provider) })
                            }
                        }
                    }
                } else {
                    MutedText(L(R.string.usage_no_limits_app_tokens), small: true)
                    StatLine(label: L(R.string.usage_today), value: MyFormat.number(u.today.sum))
                    StatLine(label: L(R.string.usage_total), value: MyFormat.number(u.total.sum))
                }
            } else {
                SkeletonRows(rows: 3)
            }
            if parts.otherCount > 0 {
                if open {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(parts.others.enumerated()), id: \.offset) { _, sub in
                            Hairline().padding(.top, 6).padding(.bottom, 2)
                            let label = labelForProvider(sub.provider)
                            let title: String = {
                                if let rw = roleWord(sub.role) { return "\(label) · \(rw)" }
                                return label
                            }()
                            Text(title).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                            if let err = sub.error {
                                MutedText(err, small: true)
                            } else {
                                UsageGroupsView(groups: UsageText.groups(MyUsage(subscription: sub), model: chosenModel), full: false)
                            }
                        }
                        ForEach(Array(parts.otherAccounts.enumerated()), id: \.offset) { _, a in
                            Hairline().padding(.top, 6).padding(.bottom, 2)
                            let label = a.displayName.isEmpty ? labelForProvider(a.provider) : a.displayName
                            let title: String = {
                                if let rw = roleWord(a.role) { return "\(label) · \(rw)" }
                                return label
                            }()
                            Text(title).font(KeloFont.titleSmall).foregroundStyle(c.ink)
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
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }
                Button {
                    if reduceMotion {
                        open.toggle()
                    } else {
                        withAnimation(.easeInOut(duration: 0.32)) {
                            open.toggle()
                        }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(open ? L(R.string.usage_limits_fold) : L(R.string.usage_limits_more, parts.otherCount))
                            .font(KeloFont.small)
                            .foregroundStyle(c.muted)
                        KeloIconView(.chevronDown, size: 14, tint: c.muted)
                            .rotationEffect(.degrees(open ? 180 : 0))
                            .animation(reduceMotion ? nil : .easeInOut(duration: 0.32), value: open)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
                }
                .buttonStyle(.plain)
            }
        }
    }
}

struct UsagePage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: UsageModel
    @State private var adminFor: String?
    @State private var removeFor: String?
    private let api: KeloApi

    init(api: KeloApi) {
        self.api = api
        _model = State(initialValue: UsageModel(api: api))
    }

    var body: some View {
        let usage = model.usage
        let chosen = app.profile?.model ?? ""
        SubPage(L(R.string.usage_title), onBack: { dismiss() }) {
            UsageByModelCard(api: api)
            LimitsSection(
                usage: usage,
                chosenModel: chosen,
                providers: model.providers,
                labelForProvider: { model.label($0) },
                onRefresh: { model.refresh() },
                onAdminKey: { adminFor = $0 },
                onRemoveAdminKey: { removeFor = $0 }
            )
        }
        .task { await model.load() }
        .keloSheet(isPresented: Binding(get: { adminFor != nil }, set: { if !$0 { adminFor = nil; model.clearAdminNote() } }), detents: [.medium, .large]) {
            if let p = adminFor {
                AdminKeySheet(provider: p, model: model, onDone: {
                    adminFor = nil
                    model.refresh()
                })
            }
        }
        .alert(L(R.string.admin_key_remove_q), isPresented: Binding(get: { removeFor != nil }, set: { if !$0 { removeFor = nil } })) {
            Button(L(R.string.admin_key_remove), role: .destructive) {
                guard let p = removeFor else { return }
                removeFor = nil
                Task {
                    await model.removeAdminKey(provider: p) {
                        model.refresh()
                    }
                }
            }
            Button(L(R.string.cancel), role: .cancel) { removeFor = nil }
        }
    }
}

private func adminHowTo(_ provider: String) -> StringKey {
    switch provider {
    case "openai": return R.string.admin_key_how_openai
    case "anthropic": return R.string.admin_key_how_anthropic
    case "openrouter": return R.string.admin_key_how_openrouter
    default: return R.string.admin_key_how_openai
    }
}

struct AdminKeySheet: View {
    let provider: String
    @Bindable var model: UsageModel
    let onDone: () -> Void
    @State private var text = ""
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L(R.string.admin_key_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
            MutedText(L(adminHowTo(provider)), small: true)
            MutedText(L(R.string.admin_key_safety), small: true)
            KeloTextField(L(R.string.model_key_paste), text: $text, secure: true, keyboard: .text, onSubmit: {
                if !text.trimmingCharacters(in: .whitespaces).isEmpty && !model.adminBusy {
                    Task { await model.saveAdminKey(provider: provider, key: text, then: onDone) }
                }
            })
            .onChange(of: text) { _, _ in model.clearAdminNote() }
            if let note = model.adminNote {
                ErrorText(L(note), small: true)
            }
            PrimaryButton(L(R.string.admin_key_save),
                          enabled: !text.trimmingCharacters(in: .whitespaces).isEmpty && !model.adminBusy,
                          busy: model.adminBusy) {
                Task { await model.saveAdminKey(provider: provider, key: text, then: onDone) }
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 24)
        .padding(.bottom, 16)
    }
}

private let modelLight = [0x3B6FD8, 0xE0913A, 0x3AA57A, 0x9B6AD6, 0xC9506A].map { Color(hex: $0) }
private let modelDark = [0x6C97EE, 0xF0A85A, 0x5CC396, 0xB48BEA, 0xE0718A].map { Color(hex: $0) }

@MainActor @Observable final class UsageByModelViewModel {
    private(set) var days: Int = UsageLogic.defaultDays
    private(set) var summary: UsageSummary?
    private(set) var failed: Bool = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?

    init(api: KeloApi) {
        self.api = api
        load()
    }

    func pick(_ days: Int) {
        guard days != self.days, UsageLogic.periods.contains(days) else { return }
        self.days = days
        self.summary = nil
        self.failed = false
        load()
    }

    func load() {
        let d = days
        loading?.cancel()
        loading = Task { @MainActor [self] in
            do {
                let s = try await api.get("/api/usage/summary?days=\(d)", as: UsageSummary.self)
                if !Task.isCancelled, days == d {
                    summary = s
                    failed = false
                }
            } catch {
                if !Task.isCancelled, days == d {
                    failed = true
                }
            }
        }
    }
}

/// My → Usage: the period's tokens (fresh input + output) as one number, one bar by model and its legend (design A, mock-up v6).
struct UsageByModelCard: View {
    @State private var vm: UsageByModelViewModel
    @Environment(\.kelo) private var c

    init(api: KeloApi) {
        _vm = State(initialValue: UsageByModelViewModel(api: api))
    }

    var body: some View {
        let lang = appLanguageCode()
        let palette = c.dark ? modelDark : modelLight
        MyCard {
            HStack(spacing: 6) {
                ForEach(UsageLogic.periods, id: \.self) { d in
                    let on = d == vm.days
                    Button {
                        vm.pick(d)
                    } label: {
                        Text(d == 1 ? L(R.string.usage_today) : L(R.string.usage_days, d))
                            .font(KeloFont.label)
                            .foregroundStyle(on ? c.card : c.muted)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(
                                Capsule().fill(on ? c.ink : Color.clear)
                            )
                            .overlay(
                                Capsule().strokeBorder(on ? c.ink : c.line, lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
            if let s = vm.summary {
                if s.total.calls == 0 {
                    MutedText(L(R.string.usage_models_empty), small: true)
                } else {
                    let t = s.total
                    let big = UsageLogic.used(t.inputTokens, t.outputTokens, t.cacheReadTokens)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L(R.string.usage_jobs_tokens, UsageLogic.compactTokens(big, lang)))
                            .font(.system(size: 34, weight: .heavy))
                            .foregroundStyle(c.ink)
                        Text(L(R.string.usage_replies, t.calls))
                            .font(KeloFont.small)
                            .foregroundStyle(c.muted)
                        if t.cacheReadTokens > 0 {
                            Text(L(R.string.usage_cache_left_out, UsageLogic.compactTokens(t.cacheReadTokens, lang)))
                                .font(KeloFont.label)
                                .foregroundStyle(c.muted)
                        }
                    }
                    let shares = UsageLogic.modelShares(s.models)
                    if !shares.isEmpty {
                        let totalTokens = shares.reduce(0) { $0 + $1.tokens }
                        GeometryReader { proxy in
                            HStack(spacing: 0) {
                                ForEach(Array(shares.enumerated()), id: \.offset) { i, sh in
                                    if sh.tokens > 0 {
                                        palette[i % palette.count]
                                            .frame(width: max(0, proxy.size.width * CGFloat(sh.tokens) / CGFloat(max(1, totalTokens))))
                                    }
                                }
                            }
                        }
                        .frame(height: 14)
                        .background(c.line)
                        .clipShape(RoundedRectangle(cornerRadius: 7))

                        Text(L(R.string.usage_models_caption, UsageLogic.compactTokens(big, lang)))
                            .font(KeloFont.small)
                            .foregroundStyle(c.muted)

                        ForEach(Array(shares.enumerated()), id: \.offset) { i, sh in
                            HStack(spacing: 10) {
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(palette[i % palette.count])
                                    .frame(width: 10, height: 10)
                                Text(sh.others > 0 ? L(R.string.usage_models_others, sh.others) : ModelNames.display(sh.model, label: nil))
                                    .font(KeloFont.body)
                                    .foregroundStyle(c.ink)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Text(UsageLogic.compactTokens(sh.tokens, lang))
                                    .font(KeloFont.body.weight(.bold))
                                    .foregroundStyle(c.ink)
                                Text("\(sh.percent)%")
                                    .font(KeloFont.label)
                                    .foregroundStyle(c.muted)
                            }
                        }
                    }
                }
            } else if vm.failed {
                MutedText(L(R.string.usage_models_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }
        }
    }
}
