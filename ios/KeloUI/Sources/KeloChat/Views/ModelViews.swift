import SwiftUI
import KeloCore
import KeloShared

/// What a provider list remembers about consent while it is open (Android ConsentGate): what was agreed to here, and the question
/// on screen. Unofficial providers (consumer accounts) are asked about once per provider and consent version; the server records it.
@MainActor @Observable
final class ConsentGate {
    var asking: ChatProvider?
    var accepted: Set<String> = []
    var error: String?
    @ObservationIgnored private var onYes: () -> Void = {}
    @ObservationIgnored private var onNo: () -> Void = {}
    @ObservationIgnored let app: AppModel

    init(app: AppModel) { self.app = app }

    func needs(_ p: ChatProvider) -> Bool { !accepted.contains(p.name) && ProviderConsentRules.needs(p) }

    /// Runs `then` at once when `p` needs no agreement, else after the user agreed (and the server recorded it).
    func request(_ p: ChatProvider?, onDecline: @escaping () -> Void = {}, then: @escaping () -> Void) {
        guard let p, needs(p) else { then(); return }
        onYes = then; onNo = onDecline; error = nil
        asking = p
    }

    func decline() {
        if let p = asking { app.prefs.set(ProviderConsentRules.askedKey(p), true) }
        asking = nil
        onNo()
    }

    func agree() {
        guard let p = asking, let api = app.api else { return }
        Task {
            do {
                _ = try await api.request("PUT", "/api/providers/\(KeloApi.segment(p.name))/consent",
                                          body: ["accepted": .bool(true), "version": .number(Double(p.consentVersion))])
                accepted.insert(p.name)
                app.prefs.set(ProviderConsentRules.askedKey(p), true)
                asking = nil
                onYes()
            } catch {
                self.error = app.message(for: error)
            }
        }
    }
}

/// The consent question, as a sheet over the list while the gate asks.
struct ConsentSheetModifier: ViewModifier {
    @Bindable var gate: ConsentGate
    let providers: [ChatProvider]

    func body(content: Content) -> some View {
        content.keloSheet(isPresented: Binding(get: { gate.asking != nil }, set: { if !$0 && gate.asking != nil { gate.decline() } }),
                          detents: [.large]) {
            if let p = gate.asking { ConsentQuestion(gate: gate, provider: p, providers: providers) }
        }
    }
}

private struct ConsentQuestion: View {
    let gate: ConsentGate
    let provider: ChatProvider
    let providers: [ChatProvider]
    @State private var checked = false
    @Environment(\.kelo) private var c

    var body: some View {
        let service = ChatWords.provider(provider.name, providers: providers)
        let vendor = provider.vendor.trimmingCharacters(in: .whitespaces).isEmpty ? provider.displayName : provider.vendor
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                SheetHeader(L(R.string.consent_title, service)) { gate.decline() }
                Text(L(R.string.consent_body, service, vendor + ProviderConsentRules.subjectParticle(vendor), vendor))
                    .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                Toggle(isOn: $checked) {
                    Text(L(R.string.consent_check)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                .tint(c.accent)
                if let e = gate.error { ErrorText(e, small: true) }
                HStack(spacing: 10) {
                    SecondaryButton(L(R.string.cancel), fill: true) { gate.decline() }
                    PrimaryButton(L(R.string.consent_use), enabled: checked) { gate.agree() }
                }
            }
            .padding(.horizontal, 20).padding(.bottom, 20)
        }
    }
}

/// The model chooser (Android ModelChooser): the models of the current provider and of the other ready providers (folded); one tap
/// switches (PUT /api/profile, the unofficial-provider question first when needed); under the list the thinking level, then a
/// way to the detailed settings (fallback model, a model typed by hand).
struct ModelChooser: View {
    let model: ChatModel
    let onMore: () -> Void
    let onClose: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    /// each provider's last good list (kept on the phone) shows at once; opening a section refreshes it in place
    @State private var lists: [String: ChatModelList] = [:]
    @State private var listsKept = false
    /// providers fetched while this sheet is open (each once)
    @State private var fetched: Set<String> = []
    @State private var switching: ModelChoice?
    @State private var err = ""
    @State private var levelsOpen = false
    /// the model list folds like the thinking level: closed at first, so the level and the detailed settings never sit below
    /// every model (nil until the sheet appears: then it starts open only when no model is chosen yet)
    @State private var listOpen: Bool?
    /// providers folded or opened by hand; the current one starts open, the rest folded
    @State private var openState: [String: Bool] = [:]
    @State private var gate: ConsentGate?
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let profile = app.profile ?? Profile()
        let providers = model.providers
        let sections = ModelChooserRules.sections(current: profile.provider, providers: providers)
        VStack(alignment: .leading, spacing: 0) {
            SheetHeader(L(R.string.model_sheet_title), onClose: onClose).padding(.horizontal, 20)
            if !err.isEmpty { ErrorText(err, small: true).padding(.horizontal, 20).padding(.vertical, 4) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    let isListOpen = listOpen ?? ModelChooserRules.listStartsOpen(currentModel: profile.model, sections: sections)
                    if !sections.isEmpty {
                        modelListRow(
                            provider: profile.provider.isEmpty ? "" : ChatWords.provider(profile.provider, providers: providers),
                            model: profile.model.isEmpty ? L(R.string.chat_model_none)
                                : ModelNames.display(profile.model, label: lists[profile.provider]?.labels[profile.model]),
                            open: isListOpen
                        ) {
                            let t = ModelChooserRules.toggle(listOpen: isListOpen, levelsOpen: levelsOpen, .list)
                            withAnimation(KeloMotionSpec.standard(reduceMotion)) { listOpen = t.list; levelsOpen = t.levels }
                        }
                        ChatsGoLine(provider: profile.provider, company: providers.first { $0.name == profile.provider }?.company)
                            .padding(.horizontal, 20).padding(.bottom, 4)
                    }
                    // The list and each provider's models fold in and out inside withAnimation: the rows slide in from under
                    // their header and fade, and what sits below moves with them instead of jumping (clipped, so nothing shows
                    // outside the space while it grows or shrinks).
                    FoldBox {
                        if isListOpen {
                            VStack(alignment: .leading, spacing: 0) {
                                ForEach(Array(sections.enumerated()), id: \.element) { index, provider in
                                    providerSection(provider, index: index, sections: sections, profile: profile, providers: providers)
                                }
                            }
                            .transition(ModelChooserRules.foldTransition(reduceMotion))
                        }
                    }
                    if sections.isEmpty { MutedText(L(R.string.chooser_none_ready), small: true).padding(.horizontal, 20).padding(.vertical, 8) }
                    let effort = ModelChooserRules.effort(providers.first { $0.name == profile.provider }, facts: lists[profile.provider]?.info[profile.model])
                    if case .fromModel = effort {} else {
                        Hairline().padding(.top, isListOpen ? 8 : 0)   // animated with the fold that changes it
                        effortRow(effort, current: profile.effort, listOpen: isListOpen)
                    }
                    Hairline()
                    Button(action: onMore) {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(L(R.string.chooser_more)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                                Text(L(R.string.chooser_more_hint)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer(minLength: 8)
                            KeloIconView(.chevronRight, size: 20, tint: c.muted)
                        }
                        .padding(.horizontal, 20).padding(.vertical, 14).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                .padding(.bottom, 16)
            }
        }
        .onAppear {
            if gate == nil { gate = ConsentGate(app: app) }
            if listOpen == nil { listOpen = ModelChooserRules.listStartsOpen(currentModel: profile.model, sections: sections) }
            if !listsKept {
                listsKept = true
                for (k, v) in ModelCatalog.keptLists(app.cache, as: ChatModelList.self) where lists[k] == nil { lists[k] = v }
            }
            if let l = model.modelList, !l.provider.isEmpty { lists[l.provider] = l }
            if let first = sections.first, first == profile.provider { load(first) }
        }
        .modifier(OptionalConsent(gate: gate, providers: providers))
    }

    /// One provider: its header (with a chevron that turns), then its models, which fold in and out with an animation.
    @ViewBuilder
    private func providerSection(_ provider: String, index: Int, sections: [String], profile: Profile, providers: [ChatProvider]) -> some View {
        let main = provider == profile.provider
        // the current provider starts open and folds with its arrow like the others; the rest start folded
        let expanded = openState[provider] ?? main
        VStack(alignment: .leading, spacing: 0) {
            if sections.count > 1 || !main {
                sectionHeader(ChatWords.provider(provider, providers: providers), foldable: true, expanded: expanded, first: index == 0) {
                    withAnimation(KeloMotionSpec.standard(reduceMotion)) { openState[provider] = !expanded }
                    if !expanded { load(provider) }
                }
            }
            FoldBox { if expanded {
                let list = lists[provider]
                let rows = ModelChooserRules.rows(provider: provider, list: list, current: ModelChoice(profile.provider, profile.model))
                let names = ModelNames.displayAll(rows, labels: list?.labels ?? [:])
                VStack(alignment: .leading, spacing: 0) {
                    if list == nil { loadingRow }
                    ForEach(rows, id: \.self) { id in
                        modelRow(name: names[id] ?? id, blurb: ChatWords.blurb(id, facts: list?.info[id]),
                                 selected: main && id == profile.model, busy: switching == ModelChoice(provider, id)) {
                            pick(ModelChoice(provider, id))
                        }
                    }
                    if let e = list?.error { MutedText(e, small: true).padding(.horizontal, 20).padding(.vertical, 6) }
                    if list?.stale == true { MutedText(L(R.string.model_list_stale), small: true).padding(.horizontal, 20).padding(.vertical, 6) }
                }
                .transition(ModelChooserRules.foldTransition(reduceMotion))
            } }
        }
    }

    /// Fetches a provider's list once per open; the kept one shows meanwhile and is replaced in place (a failure keeps it, marked
    /// stale; with nothing kept the reason shows).
    private func load(_ provider: String) {
        guard !fetched.contains(provider), let api = app.api else { return }
        fetched.insert(provider)
        let cache = app.cache
        Task {
            let fresh: ChatModelList
            do { fresh = try await ModelCatalog.fetch(api, cache, provider: provider, as: ChatModelList.self) }
            catch { fresh = .catalogFailure(provider: provider, error: L(R.string.model_picker_list_failed)) }
            guard let shown = ModelCatalog.shown(fresh, lists[provider]), shown != lists[provider] else { return }
            withAnimation(KeloMotionSpec.standard(reduceMotion)) { lists[provider] = shown }   // the loading row grows into the models
        }
    }

    /// After a pick: close when nothing is left to choose here, else fold the list (the thinking level is right under it)
    private func afterPick(_ choice: ModelChoice) {
        let facts = lists[choice.provider]?.info[choice.model]
        if ModelChooserRules.closeAfterPick(ModelChooserRules.effort(model.providers.first { $0.name == choice.provider }, facts: facts)) { onClose() }
        else { withAnimation(KeloMotionSpec.standard(reduceMotion)) { listOpen = false } }
    }

    /// The model list's folding row, built like the thinking level's: "Model" with the provider under it, the model in use on the
    /// right (at most about three fifths of the row, wrapping), a chevron that turns when it opens.
    private func modelListRow(provider: String, model name: String, open: Bool, onToggle: @escaping () -> Void) -> some View {
        let label = VStack(alignment: .leading, spacing: 2) {
            Text(L(R.string.model_picker_model)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            if !provider.isEmpty {
                Text(provider).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            }
        }
        let nameText = Text(name).font(KeloFont.button).foregroundStyle(c.accent)
        let chevron = KeloIconView(.chevronRight, size: 20, tint: c.muted).rotationEffect(.degrees(ModelChooserRules.chevronTurn(open)))
        return Button(action: onToggle) {
            Group {
                if typeSize.isAccessibilitySize {
                    // very large text: the name goes under the label, so neither is squeezed to a letter per line
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 4) {
                            label
                            nameText.fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        chevron
                    }
                } else {
                    // the name takes at most about three fifths of the row and wraps there; the label keeps the rest
                    ShareRow(share: ModelChooserRules.nameShare, spacing: 8) {
                        label
                        nameText.multilineTextAlignment(.trailing)
                        chevron
                    }
                }
            }
            .padding(.horizontal, 20).padding(.vertical, 14)
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityValue(L(open ? R.string.a11y_expanded : R.string.a11y_collapsed))
        .accessibilityIdentifier("kelo.modelListRow")
    }

    private func pick(_ choice: ModelChoice) {
        gate?.request(model.providers.first { $0.name == choice.provider }) { switchTo(choice) }
    }

    private func switchTo(_ choice: ModelChoice) {
        guard switching == nil, let api = app.api else { return }
        let profile = app.profile ?? Profile()
        if choice.provider == profile.provider && choice.model == profile.model { afterPick(choice); return }
        err = ""
        switching = choice
        Task {
            defer { if switching == choice { switching = nil } }
            do {
                // the server empties a fallback on the same provider
                app.setProfile(try await api.updateProfile(["provider": choice.provider, "model": choice.model]))
                afterPick(choice)
            } catch let e as ApiError {
                if let n = ProviderConsentRules.requiredBy(e), var needed = model.providers.first(where: { $0.name == n }) {
                    switching = nil
                    needed.consent = nil
                    gate?.request(needed) { switchTo(choice) }
                } else {
                    err = app.message(for: e)
                }
            } catch {
                err = app.message(for: error)
            }
        }
    }

    private func sectionHeader(_ label: String, foldable: Bool, expanded: Bool, first: Bool, onToggle: @escaping () -> Void) -> some View {
        Button(action: onToggle) {
            HStack {
                Text(label).font(KeloFont.titleSmall).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if foldable { KeloIconView(.chevronRight, size: 18, tint: c.muted).rotationEffect(.degrees(ModelChooserRules.chevronTurn(expanded))) }
            }
            .padding(.leading, 20).padding(.trailing, 16).padding(.top, first ? 4 : 14).padding(.bottom, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!foldable)
        .accessibilityValue(foldable ? L(expanded ? R.string.goals_hide : R.string.goals_show) : "")
    }

    private var loadingRow: some View {
        HStack(spacing: 10) {
            ProgressView().controlSize(.small)
            MutedText(L(R.string.loading), small: true)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    private func modelRow(name: String, blurb: String, selected: Bool, busy: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).font(selected ? KeloFont.button : KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    Text(blurb).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Group {
                    if busy { ProgressView().controlSize(.small) }
                    else if selected { KeloIconView(.check, size: 22, tint: c.accent) }
                }
                .frame(width: 24, height: 24)
            }
            .padding(.horizontal, 20).padding(.vertical, 10)
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityValue(selected ? L(R.string.chooser_in_use) : "")
    }

    @ViewBuilder
    private func effortRow(_ choice: EffortChoice, current: String, listOpen isListOpen: Bool) -> some View {
        switch choice {
        case .levels(let levels):
            let shown = ModelChooserRules.shownLevel(levels, current)
            Button {
                let t = ModelChooserRules.toggle(listOpen: isListOpen, levelsOpen: levelsOpen, .levels)
                withAnimation(KeloMotionSpec.standard(reduceMotion)) { listOpen = t.list; levelsOpen = t.levels }
            } label: {
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L(R.string.chat_effort)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                        Text(L(R.string.chooser_effort_hint)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 8)
                    Text(ChatWords.effort(shown)).font(KeloFont.button).foregroundStyle(c.accent)
                    KeloIconView(.chevronRight, size: 20, tint: c.muted).rotationEffect(.degrees(ModelChooserRules.chevronTurn(levelsOpen)))
                }
                .padding(.horizontal, 20).padding(.vertical, 14).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            FoldBox { if levelsOpen {
                VStack(spacing: 0) {
                    ForEach(levels, id: \.self) { v in
                        Button {
                            withAnimation(KeloMotionSpec.standard(reduceMotion)) { levelsOpen = false }
                            model.pickEffort(v)
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(ChatWords.effort(v)).font(v == shown ? KeloFont.button : KeloFont.bodyLarge).foregroundStyle(c.ink)
                                    if v.isEmpty { Text(L(R.string.chooser_effort_default_hint)).font(KeloFont.small).foregroundStyle(c.muted) }
                                }
                                Spacer(minLength: 8)
                                if v == shown { KeloIconView(.check, size: 20, tint: c.accent) }
                            }
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12).fill(v == shown ? c.bubble : .clear))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(v == shown ? .isSelected : [])
                    }
                }
                .padding(.horizontal, 12).padding(.bottom, 8)
                .transition(ModelChooserRules.foldTransition(reduceMotion))
            } }
        default:
            // shown, but greyed, with the reason in words
            VStack(alignment: .leading, spacing: 2) {
                Text(L(R.string.chat_effort)).font(KeloFont.bodyLarge).foregroundStyle(c.disabledInk)
                Text(ModelChooserRules.effortNote(choice) ?? L(R.string.chooser_effort_none))
                    .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 20).padding(.vertical, 14)
            .accessibilityElement(children: .combine)
        }
    }
}

/// The consent sheet once the gate exists (it needs the app from the environment).
struct OptionalConsent: ViewModifier {
    let gate: ConsentGate?
    let providers: [ChatProvider]
    func body(content: Content) -> some View {
        if let gate { content.modifier(ConsentSheetModifier(gate: gate, providers: providers)) } else { content }
    }
}

/// Pick the model and an automatic fallback (Android ModelSheet). The fallback must be on another provider than the main model.
struct ModelSheet: View {
    let onClose: () -> Void
    let onSaved: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var providers: [ChatProvider] = []
    @State private var main = ModelChoice.none
    @State private var fb = ModelChoice.none
    @State private var started = false
    @State private var busy = false
    @State private var note = false
    @State private var err = ""
    @State private var gate: ConsentGate?
    @State private var signOut: (name: String, outcome: SignOutRules.Outcome)?
    @State private var signOutDetail = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.model_sheet_title), onClose: onClose)
                ModelPicker(providers: providers, value: main,
                            noneLabel: L(app.profile?.modelReady == false ? R.string.model_picker_choose_provider : R.string.model_sheet_server_default),
                            gate: gate) { v in
                    main = v
                    let (next, cleared) = ModelSheetRules.afterMainChange(v, fb)
                    fb = next
                    note = cleared
                }
                signInLine(main)
                Text(L(R.string.model_sheet_fallback)).font(KeloFont.titleMedium).foregroundStyle(c.ink).padding(.top, 6)
                MutedText(L(R.string.model_sheet_fallback_hint), small: true)
                ModelPicker(providers: providers, value: fb, noneLabel: L(R.string.model_sheet_no_fallback), exclude: main.provider, gate: gate) { v in
                    note = false
                    fb = v
                }
                signInLine(fb)
                if note { MutedText(L(R.string.model_sheet_fallback_cleared), small: true) }
                if !err.isEmpty { ErrorText(err, small: true) }
                HStack(spacing: 10) {
                    SecondaryButton(L(R.string.cancel), fill: true, action: onClose)
                    PrimaryButton(L(R.string.model_sheet_apply), enabled: !busy && ModelSheetRules.complete(main) && ModelSheetRules.complete(fb),
                                  busy: busy, action: apply)
                }
                .padding(.top, 4)
            }
            .padding(.horizontal, 20).padding(.bottom, 16)
        }
        .onAppear {
            if gate == nil { gate = ConsentGate(app: app) }
            if providers.isEmpty { providers = ModelCatalog.keptChatProviders(app.cache) }   // the last list at once, refreshed below
            if !started, let p = app.profile {
                started = true
                main = ModelChoice(p.provider, p.model)
                fb = ModelSheetRules.initialFallback(p)
            }
        }
        .task { await loadProviders() }
        .modifier(OptionalConsent(gate: gate, providers: providers))
    }

    private func loadProviders() async {
        guard let api = app.api, let p = try? await ModelCatalog.fetchChatProviders(api, app.cache) else { return }
        providers = p
    }

    /// The account and 로그아웃 under a picker whose provider is signed in, the phone sign-in when the server offers it, and the
    /// answer after signing out.
    @ViewBuilder
    private func signInLine(_ choice: ModelChoice) -> some View {
        if let p = providers.first(where: { $0.name == choice.provider }) {
            if SignOutRules.offered(p.signedIn) {
                SignOutBox(name: p.name, displayName: p.displayName, account: p.account, mainProvider: app.profile?.provider ?? "",
                           quietWhenMain: true) { outcome, result, detail in
                    signOut = (p.name, outcome)
                    signOutDetail = detail
                    if SignOutRules.done(outcome) {
                        if result?.fallbackCleared == true && fb.provider == p.name { fb = .none }
                        Task {
                            await loadProviders()
                            if let api = app.api, let fresh = try? await api.profile() { app.setProfile(fresh) }
                        }
                    }
                }
            }
            if let s = signOut, s.name == choice.provider { SignOutMessage(outcome: s.outcome, detail: signOutDetail) }
            if SignInRules.offered(signin: p.signin, signedIn: p.signedIn), let gate {
                SignInBox(provider: p, gate: gate) {
                    signOut = nil
                    Task { await loadProviders() }   // the line then shows the account and 로그아웃
                }
            }
        }
    }

    private func apply() {
        guard let api = app.api else { return }
        busy = true; err = ""
        Task {
            defer { busy = false }
            do {
                app.setProfile(try await api.updateProfile([
                    "provider": main.provider, "model": main.model.trimmingCharacters(in: .whitespaces),
                    "fallback_provider": fb.provider, "fallback_model": fb.model.trimmingCharacters(in: .whitespaces),
                ]))
                onSaved()
                onClose()
            } catch let e as ApiError {
                // an unofficial provider without the user's agreement: ask, then save again
                if let n = ProviderConsentRules.requiredBy(e), var needed = providers.first(where: { $0.name == n }) {
                    needed.consent = nil
                    gate?.request(needed) { apply() }
                } else {
                    err = app.message(for: e)
                }
            } catch {
                err = app.message(for: error)
            }
        }
    }
}

/// Provider + model in one block; the model list is whatever that provider really offers. Unofficial providers come last, under
/// their own divider; choosing one goes through the consent gate.
struct ModelPicker: View {
    let providers: [ChatProvider]
    let value: ModelChoice
    var noneLabel: String?
    var exclude: String?
    let gate: ConsentGate?
    let onChange: (ModelChoice) -> Void
    @Environment(AppModel.self) private var app
    /// the provider's last good list (kept on the phone) at once, refreshed in place; never the previous provider's
    @State private var list: ChatModelList?
    @State private var loading = false
    /// the refresh takes over `ModelCatalog.slowMs`: a small hint shows
    @State private var slow = false
    /// the provider whose list has been looked up (kept or fetched); until then the chosen model shows by name
    @State private var loadedFor: String?
    @State private var custom = false

    private static let customValue = "__custom__"

    init(providers: [ChatProvider], value: ModelChoice, noneLabel: String? = nil, exclude: String? = nil, gate: ConsentGate?,
         onChange: @escaping (ModelChoice) -> Void) {
        self.providers = providers; self.value = value; self.noneLabel = noneLabel; self.exclude = exclude; self.gate = gate
        self.onChange = onChange
    }

    var body: some View {
        let info = providers.first { $0.name == value.provider }
        VStack(alignment: .leading, spacing: 8) {
            SelectField(label: L(R.string.model_picker_provider), options: providerOptions, selected: value.provider, sorted: true,
                        groupLabel: L(R.string.model_picker_unofficial)) { v in
                let p = providers.first { $0.name == v }
                if let gate { gate.request(p) { onChange(ModelChoice(v, "")) } } else { onChange(ModelChoice(v, "")) }
            }
            if !value.provider.isEmpty && info?.configured == false { ErrorText(L(R.string.model_picker_not_ready), small: true) }
            if !value.provider.isEmpty {
                // nothing kept yet and still loading: the chosen model alone, by name (never the raw id in a text field)
                let pending = loading || loadedFor != value.provider
                let own = list?.provider == value.provider ? list : nil
                let shownList = own ?? (pending ? ModelCatalog.placeholder(provider: value.provider, model: value.model, as: ChatModelList.self) : nil)
                let showSelect = !(shownList?.models.isEmpty ?? true)
                if showSelect, let l = shownList {
                    SelectField(label: L(R.string.model_picker_model), options: modelOptions(l, pending: pending),
                                selected: custom ? Self.customValue : value.model) { v in
                        if v == Self.customValue { custom = true } else { custom = false; onChange(ModelChoice(value.provider, v)) }
                    }
                }
                if custom || !showSelect {
                    KeloTextField(L(R.string.model_picker_custom_placeholder),
                                  text: Binding(get: { value.model }, set: { onChange(ModelChoice(value.provider, $0)) }),
                                  keyboard: .ascii, maxLength: 200)
                }
                if let e = shownList?.error { MutedText(e, small: true) }
                if shownList?.stale == true { MutedText(L(R.string.model_list_stale), small: true) }
                if slow { MutedText(L(R.string.loading), small: true) }
            }
        }
        .task(id: value.provider) { await load() }
    }

    private func load() async {
        let provider = value.provider
        let cache = app.cache
        let kept = ModelCatalog.keptList(cache, provider, as: ChatModelList.self)
        list = kept   // the provider's own last list at once; never the previous provider's models
        custom = kept.map { !value.model.isEmpty && !$0.models.contains(value.model) } ?? false
        slow = false
        guard !provider.isEmpty, let api = app.api else { loadedFor = provider; return }
        loading = true
        let hint = Task { @MainActor in
            try? await Task.sleep(nanoseconds: ModelCatalog.slowNanos)
            if !Task.isCancelled { slow = true }
        }
        defer {
            hint.cancel()
            if !Task.isCancelled { loading = false; slow = false; loadedFor = provider }   // a newer provider's load owns them now
        }
        do {
            let fresh = try await ModelCatalog.fetch(api, cache, provider: provider, as: ChatModelList.self)
            guard !Task.isCancelled else { return }
            let m = ModelCatalog.shown(fresh, kept) ?? fresh
            list = m
            custom = m.models.isEmpty || (!value.model.isEmpty && !m.models.contains(value.model))
        } catch is CancellationError {
        } catch {
            guard !Task.isCancelled else { return }
            list = ModelCatalog.shown(.catalogFailure(provider: provider, error: L(R.string.model_picker_list_failed)), kept)
        }
    }

    private var providerOptions: [SelectOption] {
        var out = [SelectOption("", noneLabel ?? L(R.string.model_picker_choose_provider), keep: true)]
        for p in providers {
            let taken = !(exclude ?? "").isEmpty && p.name == exclude
            var text: String = switch p.configured {
            case true?: L(R.string.model_picker_ready, p.displayName)
            case false?: L(R.string.model_picker_needs_setup, p.displayName)
            case nil: p.displayName
            }
            if taken { text = L(R.string.model_picker_taken, text) }
            out.append(SelectOption(p.name, text, enabled: !taken, rank: p.configured == true ? 0 : 1, sortKey: p.displayName,
                                    group: ProviderConsentRules.group(p)))
        }
        return out
    }

    private func modelOptions(_ l: ChatModelList, pending: Bool) -> [SelectOption] {
        var out: [SelectOption] = []
        if value.model.isEmpty {
            out.append(SelectOption("", L(pending ? R.string.loading : R.string.model_picker_choose_model), enabled: false, keep: true))
        }
        let names = ModelNames.displayAll(l.models, labels: l.labels)
        out += l.models.map { SelectOption($0, names[$0] ?? $0) }
        out.append(SelectOption(Self.customValue, L(R.string.model_picker_custom), keep: true))
        return out
    }
}

/// A <select>: a field showing the chosen option. A short list opens as a menu; a long one (the ~40 providers) opens a sheet
/// with a search field, ready options first, the chosen one marked and options that cannot be chosen greyed out.
struct SelectField: View {
    let label: String
    let options: [SelectOption]
    let selected: String
    var sorted = false
    var groupLabel: String?
    let onSelect: (String) -> Void
    @State private var sheet = false
    @Environment(\.kelo) private var c

    var body: some View {
        let shown = options.first { $0.value == selected }?.text ?? selected
        let field = HStack {
            Text(shown).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2).multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            KeloIconView(.chevronRight, size: 16, tint: c.muted).rotationEffect(.degrees(90))
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        .contentShape(Rectangle())
        .accessibilityLabel(label)
        .accessibilityValue(shown)

        if SelectRules.useSheet(options.count) {
            Button { sheet = true } label: { field }
                .buttonStyle(.plain)
                .keloSheet(isPresented: $sheet, detents: [.large]) {
                    SelectSheet(label: label, options: options, selected: selected, sorted: sorted, groupLabel: groupLabel,
                                onClose: { sheet = false }, onSelect: { sheet = false; onSelect($0) })
                }
        } else {
            let arranged = SelectRules.arrange(options, query: "", sorted: sorted)
            Menu {
                ForEach(Array(arranged.enumerated()), id: \.element.id) { i, o in
                    if SelectRules.dividerBefore(arranged, i) {
                        Divider()
                        if let groupLabel { Text(groupLabel) }
                    }
                    Button { onSelect(o.value) } label: {
                        if o.value == selected { Label(o.text, systemImage: "checkmark") } else { Text(o.text) }
                    }
                    .disabled(!o.enabled)
                }
            } label: { field }
            .buttonStyle(.plain)
        }
    }
}

private struct SelectSheet: View {
    let label: String
    let options: [SelectOption]
    let selected: String
    let sorted: Bool
    let groupLabel: String?
    let onClose: () -> Void
    let onSelect: (String) -> Void
    @State private var query = ""
    @Environment(\.kelo) private var c

    var body: some View {
        let shown = SelectRules.arrange(options, query: query, sorted: sorted)
        VStack(alignment: .leading, spacing: 8) {
            SheetHeader(label, onClose: onClose).padding(.horizontal, 20)
            KeloTextField(L(R.string.select_search), text: $query, maxLength: 80).padding(.horizontal, 16)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(shown.enumerated()), id: \.element.id) { i, o in
                            if SelectRules.dividerBefore(shown, i) {
                                HStack {
                                    Hairline()
                                    if let groupLabel { Text(groupLabel).font(KeloFont.label).foregroundStyle(c.muted).fixedSize() }
                                    Hairline()
                                }
                                .padding(.horizontal, 16).padding(.vertical, 8)
                            }
                            Button { onSelect(o.value) } label: {
                                HStack {
                                    Text(o.text).font(o.value == selected ? KeloFont.button : KeloFont.bodyLarge)
                                        .foregroundStyle(!o.enabled ? c.muted.opacity(0.6) : o.value == selected ? c.accent : c.ink)
                                        .lineLimit(2).multilineTextAlignment(.leading)
                                    Spacer(minLength: 8)
                                    if o.value == selected { KeloIconView(.check, size: 20, tint: c.accent) }
                                }
                                .padding(.horizontal, 20).padding(.vertical, 12).frame(minHeight: 48)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .disabled(!o.enabled)
                            .id(o.id)
                            .accessibilityAddTraits(o.value == selected ? .isSelected : [])
                        }
                        if SelectRules.nothingFound(shown, query: query) {
                            Text(L(R.string.select_no_match)).font(KeloFont.body).foregroundStyle(c.muted)
                                .padding(.horizontal, 20).padding(.vertical, 14)
                        }
                    }
                }
                .onAppear { proxy.scrollTo(selected, anchor: .center) }
            }
        }
    }
}

/// A row of three: a label that takes what is left (leading), a value that gets at most `share` of the row and wraps there
/// (trailing), and a small trailing mark. Measured from the row's own width, so nothing feeds back into it.
struct ShareRow: Layout {
    let share: CGFloat
    let spacing: CGFloat

    private func frames(width: CGFloat?, _ s: Subviews) -> (size: CGSize, rects: [CGRect]) {
        guard s.count == 3 else { return (.zero, []) }
        let mark = s[2].sizeThatFits(.unspecified)
        let labelIdeal = s[0].sizeThatFits(.unspecified)
        let valueIdeal = s[1].sizeThatFits(.unspecified)
        let w = width ?? (labelIdeal.width + valueIdeal.width + mark.width + spacing * 2)
        let room = max(0, w - mark.width - spacing * 2)
        let value = s[1].sizeThatFits(ProposedViewSize(width: min(valueIdeal.width, room * share), height: nil))
        let labelW = max(0, room - value.width)
        let label = s[0].sizeThatFits(ProposedViewSize(width: labelW, height: nil))
        let h = max(label.height, value.height, mark.height)
        return (CGSize(width: w, height: h), [
            CGRect(x: 0, y: (h - label.height) / 2, width: labelW, height: label.height),
            CGRect(x: w - mark.width - spacing - value.width, y: (h - value.height) / 2, width: value.width, height: value.height),
            CGRect(x: w - mark.width, y: (h - mark.height) / 2, width: mark.width, height: mark.height),
        ])
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        frames(width: proposal.width, subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let f = frames(width: bounds.width, subviews).rects
        for (i, r) in f.enumerated() {
            subviews[i].place(at: CGPoint(x: bounds.minX + r.minX, y: bounds.minY + r.minY),
                              proposal: ProposedViewSize(width: r.width, height: r.height))
        }
    }
}

/// The space a folding list opens and closes in: it takes the list's height (animated by the withAnimation that changes it) and
/// clips what slides in or out, so the list never draws over the rows above it while it moves.
private struct FoldBox<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .clipped()
    }
}

extension ModelChooserRules {
    /// How a folding list comes and goes: it slides in from under its header and fades (the space around it is animated by the
    /// withAnimation that changes it); with Reduce Motion only a plain fade, no movement.
    static func foldTransition(_ reduce: Bool) -> AnyTransition {
        reduce ? .opacity : .move(edge: .top).combined(with: .opacity)
    }
}
