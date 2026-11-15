import SwiftUI
import KeloCore
import KeloShared

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
    @State private var chooser: ModelChooserModel?
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
        let lists = chooser?.lists ?? [:]
        let err = chooser?.err ?? ""
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
                                    providerSection(provider, index: index, sections: sections, profile: profile, providers: providers, lists: lists)
                                }
                            }
                            .transition(ModelChooserRules.foldTransition(reduceMotion))
                        }
                    }
                    if sections.isEmpty { MutedText(L(R.string.chooser_none_ready), small: true).padding(.horizontal, 20).padding(.vertical, 8) }
                    let effort = EffortRules.effort(providers.first { $0.name == profile.provider }, facts: lists[profile.provider]?.info[profile.model])
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
            if gate == nil, let api = app.api { gate = ConsentGate(api: api, prefs: app.prefs) }
            if chooser == nil, let api = app.api { chooser = ModelChooserModel(api: api, cache: app.cache, host: app) }
            if listOpen == nil { listOpen = ModelChooserRules.listStartsOpen(currentModel: profile.model, sections: sections) }
            chooser?.keepLists(current: model.modelList)
            if let first = sections.first, first == profile.provider { chooser?.load(first) }
        }
        .keloSheet(isPresented: Binding(get: { gate?.asking != nil }, set: { if !$0 { gate?.decline() } }), detents: [.large]) {
            if let gate { ProviderConsentSheet(gate: gate) }
        }
    }

    /// One provider: its header (with a chevron that turns), then its models, which fold in and out with an animation.
    @ViewBuilder
    private func providerSection(_ provider: String, index: Int, sections: [String], profile: Profile, providers: [ModelProvider],
                                 lists: [String: ModelList]) -> some View {
        let main = provider == profile.provider
        // the current provider starts open and folds with its arrow like the others; the rest start folded
        let expanded = openState[provider] ?? main
        VStack(alignment: .leading, spacing: 0) {
            if sections.count > 1 || !main {
                sectionHeader(ChatWords.provider(provider, providers: providers), foldable: true, expanded: expanded, first: index == 0) {
                    withAnimation(KeloMotionSpec.standard(reduceMotion)) { openState[provider] = !expanded }
                    if !expanded { chooser?.load(provider) }
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
                                 selected: main && id == profile.model, busy: chooser?.switching == ModelChoice(provider, id)) {
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

    /// After a pick: close when nothing is left to choose here, else fold the list (the thinking level is right under it)
    private func afterPick(_ choice: ModelChoice) {
        let facts = chooser?.lists[choice.provider]?.info[choice.model]
        if ModelChooserRules.closeAfterPick(EffortRules.effort(model.providers.first { $0.name == choice.provider }, facts: facts)) { onClose() }
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
        chooser?.switchTo(choice, after: { afterPick(choice) }, needConsent: { name, retry in
            guard var needed = model.providers.first(where: { $0.name == name }) else { return }
            needed.consent = nil
            gate?.request(needed, then: retry)
        })
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
            let shown = EffortRules.shownLevel(levels, current)
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
                Text(EffortRules.effortNote(choice) ?? L(R.string.chooser_effort_none))
                    .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 20).padding(.vertical, 14)
            .accessibilityElement(children: .combine)
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
