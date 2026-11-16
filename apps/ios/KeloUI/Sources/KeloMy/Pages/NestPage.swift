import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Nest (Android my/NestPage.kt): installing a package from a GitHub link, the installed packages with update and remove, what Kelo
/// made by talking ("Made here": the drafts and the custom skills, each with Open, Share, Delete) and which package tabs the bottom bar
/// shows. The pages it opens push onto its own stack ("screen:<key>", "skill:<id>", "text:<id>").
struct NestPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @Environment(\.openURL) private var openURL
    @Environment(\.keloConnectPanel) private var connectPanel
    @State private var model: NestMadeModel
    @State private var links: NestLinksModel
    @State private var files: KeloFilesModel
    @State private var at: [String] = []
    @State private var deleting: MadeItem?
    @State private var removing: NestInstalledRow?
    @State private var changedOf: NestInstalledRow?
    @State private var contentsOpen = false
    @State private var skillAsk = false
    @State private var note: String?
    private let api: KeloApi

    init(api: KeloApi) {
        self.api = api
        _model = State(initialValue: NestMadeModel(api: api))
        _links = State(initialValue: NestLinksModel(api: api))
        _files = State(initialValue: KeloFilesModel(api: api))
    }

    private var top: String { at.last ?? "" }
    private func pop() { if at.isEmpty { dismiss() } else { at.removeLast() } }
    private var nestState: NestUiState { app.nest?.state ?? .empty }
    private func entry(_ key: String) -> NestEntry? { nestState.byPlace.values.flatMap { $0 }.first { $0.key == key } }

    var body: some View {
        Group {
            if top.hasPrefix("screen:") {
                if let e = entry(String(top.dropFirst(7))) { NestFullPage(entry: e, onBack: pop) }
                else { SubPage(L(R.string.nest_title), onBack: pop) { MutedText(L(R.string.nest_gone)) } }
            } else if top.hasPrefix("skill:") {
                let id = String(top.dropFirst(6))
                SubPage(files.skill.flatMap { $0.id == id ? $0.title : nil } ?? "", onBack: pop) {
                    SkillReadView(model: files, id: id) { skillAsk = true }
                }
                .task(id: id) { await files.openSkill(id) }
                .alert(L(R.string.files_skill_delete_q), isPresented: $skillAsk) {
                    Button(L(R.string.files_delete), role: .destructive) {
                        Task { await files.deleteSkill(id) { _ in }; await model.load(); pop() }
                    }
                    Button(L(R.string.cancel), role: .cancel) {}
                }
            } else if top.hasPrefix("text:") {
                DraftTextPage(api: api, id: String(top.dropFirst(5)), title: model.items.first { $0.id == String(top.dropFirst(5)) }?.title ?? "", onBack: pop)
            } else {
                list
            }
        }
        .task { await model.load() }
        .task { await links.loadInstalled() }
        .onChange(of: links.card?.token) { _, _ in contentsOpen = false }   // a new preview or none: the contents sheet of the last one closes
        .task { for await _ in NestLive.ids() { await model.changed() } }   // Kelo writes to a draft while this page is open: the list follows
        .onChange(of: model.effects) { _, _ in drain() }
        .onChange(of: model.message) { _, m in
            if let m { note = m.isEmpty ? L(R.string.nest_share_failed) : m; model.message = nil }
        }
        .overlay(alignment: .bottom) {
            if let note {
                Text(note).font(KeloFont.small).foregroundStyle(.white).multilineTextAlignment(.center)
                    .padding(.horizontal, 14).padding(.vertical, 10).background(Capsule().fill(Color.black.opacity(0.82)))
                    .padding(.bottom, 24).padding(.horizontal, 16)
                    .task(id: note) { try? await Task.sleep(nanoseconds: 3_000_000_000); self.note = nil }
            }
        }
        .keloConfirm(deleting.map { L(R.string.nest_delete_q, $0.title) } ?? "", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                     yes: L(R.string.nest_delete)) {
            if let d = deleting { Task { await model.delete(d) } }
        }
        .keloConfirm(removing.map { L(R.string.nest_remove_pkg_q, $0.title) } ?? "", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                     yes: L(R.string.nest_remove)) {
            if let r = removing { Task { await links.remove(r) } }
        }
        .keloSheet(isPresented: Binding(get: { links.card != nil }, set: { if !$0 { links.dismissCard() } })) {
            if let card = links.card { linkCardSheet(card) }
        }
        .keloSheet(isPresented: Binding(get: { links.look != nil }, set: { if !$0 { links.dismissLook() } })) {
            if let row = links.look { updateLookSheet(row) }
        }
        .keloSheet(item: $changedOf) { row in
            ScrollView { NestChangedSheet(title: row.title, changed: row.update?.changed ?? NestChanged()) }
        }
        .keloSheet(item: Binding(get: { model.connectFor }, set: { if $0 == nil { model.dismissConnect() } }), detents: [.large]) { item in
            connectSheet(item)
        }
    }

    @MainActor private func drain() {
        while let fx = model.nextEffect() {
            switch fx {
            case .openChat(let turn):   // Kelo asks where to put it: the chat follows that reply
                app.router.chatFollowTurn = turn
                app.router.go(.chat)
            }
        }
    }

    // ---- the list

    @ViewBuilder private var list: some View {
        SubPage(L(R.string.nest_title), onBack: pop) {
            linkSection
            installedSection
            SectionLabel(L(R.string.nest_made_here))
            MutedText(L(R.string.nest_made_hint), small: true)
            if !model.loaded { SkeletonRows(rows: 2) }
            else if model.items.isEmpty { EmptyNote(L(R.string.nest_made_empty)) }
            else {
                ForEach(model.items) { item in
                    MadeCard(item: item, busy: model.busy.contains(item.id), onOpen: { open(item) }, onShare: { Task { await model.share(item) } },
                             onDelete: { deleting = item }, onRepo: { url in if let u = URL(string: url) { openURL(u) } })
                }
            }
            tabsSection
        }
    }

    private func open(_ item: MadeItem) {
        let screen = item.draft
            ? nestState.byPlace.values.flatMap { $0 }.filter { $0.packageId == item.id && $0.draft }
                .min { ($0.place == NestPlace.tab ? 0 : 1) < ($1.place == NestPlace.tab ? 0 : 1) }
            : nil
        if let screen { at.append("screen:\(screen.key)") }
        else if item.draft { at.append("text:\(item.id)") }
        else { at.append("skill:\(item.id)") }
    }

    /// Sharing needs GitHub with write access: the existing connect panel (device code or web sign-in), then Share goes on by itself.
    @ViewBuilder private func connectSheet(_ item: MadeItem) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(L(R.string.nest_connect_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                MutedText(L(R.string.nest_connect_hint), small: true)
                if let panel = connectPanel { panel.make("github") { Task { await model.retryShare() } } }
                PrimaryButton(L(R.string.nest_share_github)) { Task { await model.retryShare() } }
                Text(item.title).font(KeloFont.small).foregroundStyle(c.muted)
            }
            .padding(20)
        }
    }

    // ---- install from a link, and the installed packages

    /// Paste a GitHub link, Look: the card says what it is and what it may do; Install puts it on. Nobody reviewed it: the sheet says so.
    @ViewBuilder private var linkSection: some View {
        SectionLabel(L(R.string.nest_link_title))
        MutedText(L(R.string.nest_link_hint), small: true)
        MyCard {
            KeloTextField("https://github.com/…", text: Binding(get: { links.link }, set: { links.link = $0 }), keyboard: .url,
                          onSubmit: { Task { await links.check() } })
            if let p = links.problem { MutedText(linkProblemText(p), small: true) }
            if !links.reason.isEmpty { MutedText(links.reason, small: true) }
            HStack(spacing: 8) {
                PrimaryButton(L(R.string.nest_link_check), enabled: !links.link.trimmingCharacters(in: .whitespaces).isEmpty && !links.checking) {
                    Task { await links.check() }
                }
                if links.checking { ProgressView().controlSize(.small) }
            }
        }
    }

    private func linkProblemText(_ p: NestLinkProblem) -> String {
        switch p {
        case .notLink: L(R.string.nest_link_not_link)
        case .notPackage: L(R.string.nest_link_not_package)
        case .expired: L(R.string.nest_link_expired)
        case .changed: L(R.string.nest_link_changed)
        case .already: L(R.string.nest_link_already)
        case .failed: L(R.string.nest_link_failed)
        }
    }

    @ViewBuilder private func linkCardSheet(_ card: NestLinkCard) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(card.name.isEmpty ? card.id : card.name).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                MutedText([card.repo, L(R.string.nest_version, card.version)].filter { !$0.isEmpty }.joined(separator: " · "), small: true)
                if !card.description.isEmpty { Text(card.description).font(KeloFont.body).foregroundStyle(c.ink) }
                KeloBadge(L(R.string.nest_unreviewed))
                SectionLabel(L(R.string.nest_may_do))
                ForEach(Array(card.permissions.words.enumerated()), id: \.offset) { _, w in wordLine(w) }
                if NestContentsRules.hasContents(card.contents) {
                    LinkButton(L(R.string.nest_contents_open)) { contentsOpen = true }
                }
                if let v = card.installed { MutedText(L(R.string.nest_link_already_version, v), small: true) }
                PrimaryButton(L(R.string.nest_install), enabled: !links.installing && card.installed == nil) { Task { await links.install() } }
            }
            .padding(20)
        }
        .keloSheet(isPresented: $contentsOpen) { ScrollView { NestContentsSheet(contents: card.contents) } }   // stacks on the card; closing it returns here
    }

    /// One line of what a package may do; a caution is in red.
    private func wordLine(_ w: NestWord) -> some View {
        Text("• \(w.text)").font(KeloFont.body).foregroundStyle(w.level == "caution" ? c.danger : c.ink)
    }

    /// An update that asks for more than before: the new list, then Update.
    @ViewBuilder private func updateLookSheet(_ row: NestInstalledRow) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(L(R.string.nest_update_asks_more, row.title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                ForEach(Array((row.update?.words ?? []).enumerated()), id: \.offset) { _, w in wordLine(w) }
                PrimaryButton(L(R.string.nest_update)) { Task { await links.update(row, accepted: true) } }
            }
            .padding(20)
        }
    }

    /// The installed packages: name, repository, version; Update when the repository has a newer commit; Remove.
    @ViewBuilder private var installedSection: some View {
        if !links.installed.isEmpty {
            SectionLabel(L(R.string.nest_installed))
            ForEach(links.installed) { row in
                MyCard {
                    Text(row.title).font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    MutedText([row.repo, L(R.string.nest_version, row.version)].filter { !$0.isEmpty }.joined(separator: " · "), small: true)
                    if let u = row.update { MutedText(L(R.string.nest_update_ready, u.version), small: true) }
                    let changed = row.update.map { NestContentsRules.changedCount($0.changed) } ?? 0
                    if changed > 0 { MutedText(L(R.string.nest_update_changed, changed), small: true) }
                    WalletWrap(spacing: 8) {
                        if row.update != nil && !row.pinned {
                            SecondaryButton(L(R.string.nest_update), enabled: !links.busy.contains(row.id)) { Task { await links.update(row) } }
                        }
                        if changed > 0 { LinkButton(L(R.string.nest_changed_open)) { changedOf = row } }
                        LinkButton(L(R.string.nest_remove), danger: true) { removing = row }
                        if links.busy.contains(row.id) { ProgressView().controlSize(.small) }
                    }
                }
            }
        }
    }

    // ---- the package tabs

    /// Which package tabs the bottom bar shows: every `tab` entry with a switch, at most as many as the server allows.
    @ViewBuilder private var tabsSection: some View {
        let tabs = nestState.byPlace[NestPlace.tab] ?? []
        if !tabs.isEmpty {
            let shown = tabs.filter(\.shown).map(\.key)
            SectionLabel(L(R.string.nest_tabs_title))
            MutedText(L(R.string.nest_tabs_hint, nestState.maxTabs), small: true)
            ForEach(tabs) { e in
                HStack(spacing: 12) {
                    NestIconView(e.icon, size: 24, tint: c.muted)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(e.title.isEmpty ? e.packageName : e.title).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                        MutedText(e.draft ? "\(e.packageName) · \(L(R.string.nest_draft))" : e.packageName, small: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    // at most max_tabs at once: a switch that would go past it stays off until another is turned off
                    Toggle("", isOn: Binding(get: { e.shown }, set: { on in
                        Task {
                            let words = await app.nest?.setTabs(on ? shown + [e.key] : shown.filter { $0 != e.key })
                            if let words { note = words.isEmpty ? L(R.string.nest_tabs_failed) : words }
                        }
                    }))
                    .labelsHidden().tint(c.accent).disabled(!e.shown && shown.count >= nestState.maxTabs)
                }
                .padding(.horizontal, 16).padding(.vertical, 6)
            }
        }
    }
}

/// One thing that was made: its name, kind, version, "Draft" and "Shared on GitHub" tags, and Open / Share / Delete.
struct MadeCard: View {
    let item: MadeItem
    let busy: Bool
    let onOpen: () -> Void
    let onShare: () -> Void
    let onDelete: () -> Void
    let onRepo: (String) -> Void
    @Environment(\.kelo) private var c

    private var kind: String {
        switch item.kind {
        case .skill: L(R.string.nest_kind_skill)
        case .extensionKind: L(R.string.nest_kind_extension)
        case .connector: L(R.string.nest_kind_connector)
        case .customSkill: L(R.string.nest_kind_custom_skill)
        }
    }

    var body: some View {
        MyCard {
            HStack(spacing: 8) {
                Text(item.title).font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                if item.draft { KeloBadge(L(R.string.nest_draft)) }
            }
            MutedText(item.version.isEmpty ? kind : "\(kind) · \(L(R.string.nest_version, item.version))", small: true)
            if item.shared {
                MutedText("\(L(R.string.nest_shared_on_github)) · \(item.repo)", small: true)
                if let url = item.repoUrl { LinkButton(L(R.string.nest_open_repo)) { onRepo(url) } }
            }
            HStack(spacing: 8) {
                SecondaryButton(L(R.string.nest_open), enabled: !busy, action: onOpen)
                SecondaryButton(L(R.string.nest_share), enabled: !busy, action: onShare)
                LinkButton(L(R.string.nest_delete), danger: true, action: onDelete)
                if busy { ProgressView().controlSize(.small) }
            }
        }
    }
}

/// A draft that has no screen: its SKILL.md (else its manifest), read only, with what the validator found.
private struct DraftTextPage: View {
    let api: KeloApi
    let id: String
    let title: String
    let onBack: () -> Void
    @State private var details: NestDraftDetails?
    @State private var failed = false
    @Environment(\.kelo) private var c
    private let mono = Font.system(.footnote, design: .monospaced)

    var body: some View {
        SubPage(title.isEmpty ? id : title, onBack: onBack) {
            KeloBadge(L(R.string.nest_draft))
            if failed { MutedText(L(R.string.nest_load_failed)) }
            else if let d = details {
                ForEach(d.findings, id: \.self) { MutedText($0, small: true) }
                Text(d.files["SKILL.md"] ?? d.files["kelo-package.json"] ?? "").font(mono).foregroundStyle(c.ink).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else { SkeletonRows(rows: 3) }
        }
        .task(id: id) { await read() }
        .task { for await ch in NestLive.ids() where ch == id { await read() } }
    }

    private func read() async {
        do { details = try await api.get("/api/nest/drafts/\(KeloApi.segment(id))", as: NestDraftDetails.self) }
        catch is CancellationError { return }
        catch { if details == nil { failed = true } }
    }
}
