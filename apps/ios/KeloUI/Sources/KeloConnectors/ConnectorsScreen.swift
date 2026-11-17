import SwiftUI
import Observation
import KeloCore
import KeloShared

/// A pushed connector detail.
struct ConnectorRoute: Hashable { let id: String }

/// 커넥터 tab (Android connectors/ConnectorsScreen.kt): search, the connected and popular services (or the full list by group with
/// filter chips), the phone's own data, and each service's detail page with its connect panel. Opens `app.router.connectorOpen`.
public struct ConnectorsScreen: View {
    @Environment(AppModel.self) private var app

    public init() {}

    public var body: some View {
        // the model is built once per signed-in API (a new session starts a new one)
        if let api = app.api { ConnectorsStack(api: api, cache: app.cache).id(ObjectIdentifier(api)) }
    }
}

private struct ConnectorsStack: View {
    let api: KeloApi
    @Environment(AppModel.self) private var app
    @Environment(\.scenePhase) private var phase
    @State private var model: ConnectorsModel

    init(api: KeloApi, cache: ScreenCache) {
        self.api = api
        _model = State(initialValue: ConnectorsModel(api: api, cache: cache))
    }

    var body: some View {
        ListDetailStack<ConnectorRoute, _, _>(tab: .connectors, root: {
            ConnectorListView(model: model)
                .keloShellInsets()
                .keloHideNavigationBar()
        }, detail: { r in
            ConnectorDetailView(model: model, id: r.id, api: api)
        })
        .onAppear {
            model.warm()
            model.reload()
            openRequested()
        }
        // back from a browser sign-in: the list (and the open connector's page, which reads it) is read again
        .onChange(of: phase) { _, p in if p == .active { model.reload() } }
        .onChange(of: app.router.connectorOpen) { _, _ in openRequested() }
    }

    /// Another place asked for one connector (the chat's connect card): its detail, on top of the list.
    private func openRequested() {
        guard let id = app.router.connectorOpen else { return }
        app.router.connectorOpen = nil
        var p = NavigationPath()
        p.append(ConnectorRoute(id: id))
        app.router.paths[.connectors] = p
    }
}

/// The list.
struct ConnectorListView: View {
    @Bindable var model: ConnectorsModel
    @Environment(\.kelo) private var c
    @Environment(AppModel.self) private var app

    var body: some View {
        let s = model.state
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                    TabTitle(L(R.string.tab_connectors)).padding(.bottom, 10).id("top")
                    Section {
                        if !s.loaded {
                            SkeletonRows(rows: 6)
                        } else {
                            ForEach(s.sections) { section in sectionView(section) }
                            if s.showsPhoneData {
                                PhoneDataSection().padding(.bottom, 14)
                            }
                            if s.showsShowAll {
                                Button { model.state.setShowAll(true) } label: {
                                    Text(P(R.plurals.connectors_show_all, s.hiddenCount, s.hiddenCount)).font(KeloFont.body)
                                        .foregroundStyle(c.muted).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                                        .frame(maxWidth: .infinity).padding(.vertical, 14).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                            if s.shownCount == 0 {
                                if s.failed {
                                    VStack(alignment: .leading, spacing: 10) {
                                        MutedText(L(R.string.connect_failed))
                                        SecondaryButton(L(R.string.retry)) { model.reload() }
                                    }
                                } else {
                                    MutedText(L(R.string.connectors_empty))
                                }
                            }
                        }
                    } header: {
                        searchBar(s)
                    }
                }
                .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 24)
                .keloPageWidth()
            }
            .keloTopScroll(root: true)
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await model.load() }
            .onTabReselect(.connectors) { withAnimation { proxy.scrollTo("top", anchor: .top) } }
        }
        .background(c.bg.ignoresSafeArea())
        .overlay(alignment: .bottom) {
            StaleHint(visible: s.offline) { model.reload() }.padding(.bottom, 12)
        }
    }

    /// The search field, the "back to popular" link and the group chips: pinned at the top while the list scrolls under them.
    @ViewBuilder private func searchBar(_ s: ConnectorsState) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            KeloTextField(L(R.string.connectors_search), text: $model.state.query, keyboard: .ascii)
                .submitLabel(.search)
            if s.showsBackToPopular {
                LinkButton(L(R.string.connectors_back_to_popular)) { model.state.setShowAll(false) }
            }
            if s.showsChips {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ChoiceChip(L(R.string.connectors_filter_all), selected: s.filter == nil) { model.state.filter = nil }
                        ForEach(s.chips) { ch in
                            ChoiceChip(ch.titleKey.map { LS($0) } ?? ch.title, selected: s.filter == ch.key) {
                                model.state.filter = s.filter == ch.key ? nil : ch.key
                            }
                        }
                    }
                }
            }
        }
        .padding(.bottom, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(c.bg)
    }

    @ViewBuilder private func sectionView(_ section: ConnectorsState.Section) -> some View {
        let color: Color = switch section.key {
        case "reconnect": c.danger
        case "connected", "popular": c.accent
        default: c.muted
        }
        Text(section.titleKey.map { LS($0) } ?? section.title).font(KeloFont.titleSmall).foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.leading, 4).padding(.top, 6).padding(.bottom, 8)
            .accessibilityAddTraits(.isHeader)
        VStack(spacing: 0) {
            ForEach(Array(section.items.enumerated()), id: \.element.id) { i, x in
                if i > 0 { Hairline().padding(.horizontal, 16) }
                // the tab's path is the router's, so the page opens in whichever pane shows the pushed pages
                Button { app.router.paths[.connectors, default: NavigationPath()].append(ConnectorRoute(id: x.id)) } label: {
                    ConnectorRow(x: x, badge: ConnectorLogic.rowBadge(x, creds: model.state.creds))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.vertical, 2)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .padding(.bottom, 14)
    }
}

struct ConnectorRow: View {
    let x: ConnectorInfo
    let badge: ConnectorLogic.RowBadge?
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 12) {
            ConnectorTile(x: x, size: 40)
            VStack(alignment: .leading, spacing: 2) {
                Text(TokenText.fit(x.name)).font(KeloFont.bodyLarge.weight(.medium)).foregroundStyle(c.ink).lineLimit(2)
                if !x.description.isEmpty {
                    Text(x.description).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 4) {
                RowBadgeView(badge: badge)
                if x.limited { LimitedChip() }
            }
            .frame(maxWidth: 120, alignment: .trailing)
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .contentShape(Rectangle())
    }
}

/// The phone's own data (Android "이 기기에서"): on an iPhone the calendar, contacts and the phone dialer. The sync and its permission live in
/// 마이 → 권한, so a row opens that page.
struct PhoneDataSection: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(L(R.string.device_section)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
                .padding(.leading, 4).padding(.top, 6).padding(.bottom, 8)
                .accessibilityAddTraits(.isHeader)
            VStack(spacing: 0) {
                ForEach(Array(PhoneDataRow.shown().enumerated()), id: \.element) { i, row in
                    if i > 0 { Hairline().padding(.leading, 68).padding(.trailing, 16) }
                    Button { app.router.openMyPage(.permissions) } label: {
                        HStack(spacing: 12) {
                            RoundedRectangle(cornerRadius: 11).fill(c.infoSoft).frame(width: 40, height: 40)
                                .overlay(KeloIconView(row == .calendar ? .calendar : (row == .contacts ? .contacts : .phone), size: 22, tint: c.infoInk))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(LS(row.titleKey)).font(KeloFont.bodyLarge.weight(.medium)).foregroundStyle(c.ink)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text(L(R.string.device_row_sub)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            KeloIconView(.chevronRight, size: 18, tint: c.muted)
                        }
                        .padding(.horizontal, 16).padding(.vertical, 12)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(LS(row.titleKey))
                }
            }
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        }
    }
}
