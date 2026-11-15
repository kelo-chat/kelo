import SwiftUI
import KeloCore
import KeloShared

/// One connector, full screen (Android ConnectorDetail): its tile, name and state, what Kelo can do with it, how it is connected and
/// who is, a warning when it needs connecting again, then the connect panel. The header and tab bar step aside (a pushed page).
struct ConnectorDetailView: View {
    @Bindable var model: ConnectorsModel
    let id: String
    let api: KeloApi
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var askDisconnect = false

    var body: some View {
        let x = model.state.find(id)
        SubPage(x?.name ?? "", onBack: back) {
            if let x {
                content(x)
            } else if !model.state.loaded {
                SkeletonRows(rows: 4)
            } else {
                MutedText(L(R.string.connector_gone))
            }
        }
    }

    private func back() {
        var p = app.router.paths[.connectors] ?? NavigationPath()
        if !p.isEmpty { p.removeLast() }
        app.router.paths[.connectors] = p
    }

    @ViewBuilder private func content(_ x: ConnectorInfo) -> some View {
        let creds = model.state.creds
        let broken = ConnectorLogic.reconnect(x, creds: creds)
        let account = ConnectorLogic.account(x, creds: creds)
        HStack(spacing: 14) {
            ConnectorTile(x: x, size: 64)
            VStack(alignment: .leading, spacing: 6) {
                Text(TokenText.fit(x.name)).font(KeloFont.titleLarge.bold()).foregroundStyle(c.ink).lineLimit(2)
                RowBadgeView(badge: ConnectorLogic.rowBadge(x, creds: creds))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        if let broken {
            KeloCard {
                HStack(alignment: .top, spacing: 10) {
                    KeloIconView(.alert, size: 20, tint: c.danger)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L(R.string.connector_reconnect_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        SmallText(text: LS(ConnectorLogic.reconnectKey(broken)))
                        let detail = creds.first { $0.name == x.item }?.lastResultDetail ?? ""
                        if broken == .refused && !detail.isEmpty {
                            MutedText(L(R.string.connector_reconnect_detail, detail), small: true)
                        }
                    }
                }
            }
        }
        if !x.description.isEmpty {
            DetailSection(title: L(R.string.connector_can_do)) {
                Text(x.description).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
        }
        DetailSection(title: L(R.string.connector_facts)) {
            FactRow(label: L(R.string.connector_method), value: LS(ConnectorLogic.wayKey(ConnectWay.shown(x))))
            if !account.isEmpty {
                FactRow(label: L(R.string.connector_account), value: account)
            } else if x.status == "connected" {
                FactRow(label: L(R.string.connector_account), value: L(R.string.connector_account_unknown))
            }
            if !x.site.isEmpty {
                let host = ConnectorRules.host(x.site)
                FactRow(label: L(R.string.connector_site), value: host.isEmpty ? x.site : host)
            }
            if x.connect == "device" && x.status == "connected" { DeviceScopes(provider: x.via.isEmpty ? x.id : x.via, connection: x.item, api: api) }
        }
        if x.status == "connected" { PermissionsSection(id: x.id, api: api) }
        if x.connect != nil {
            // needing a new connection: the panel offers connecting again (the stored one is replaced), and it can still go
            KeloCard {
                ConnectPanel(connector: broken != nil ? x.with(status: "available") : x, api: api, onChanged: { model.reload() })
            }
            if broken != nil && !x.item.isEmpty {
                LinkButton(L(R.string.connector_disconnect), danger: true) { askDisconnect = true }
                    .keloConfirm(L(R.string.mcp_disconnect_confirm, x.name), isPresented: $askDisconnect, yes: L(R.string.connector_disconnect)) {
                        model.disconnect(item: x.item)
                    }
            }
        }
        if x.status == "coming_soon" { MutedText(L(R.string.connector_coming_soon_note), small: true) }
        if x.status == "unsupported" { MutedText(L(R.string.connector_unsupported_note), small: true) }
    }
}

struct DetailSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.leading, 2)
                .accessibilityAddTraits(.isHeader)
            KeloCard { content }
        }
    }
}

/// A label and its value; at small widths or large text the value goes under the label instead of squeezing.
struct FactRow: View {
    let label: String
    let value: String
    @Environment(\.kelo) private var c

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(label).font(KeloFont.body).foregroundStyle(c.muted).frame(width: 110, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                Text(TokenText.fit(value, max: 24)).font(KeloFont.body).foregroundStyle(c.ink)
                    .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                Text(TokenText.fit(value, max: 24)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// The permission a connected code-connected service was given (GET /api/oauth/providers: its `granted_scope` among its `scopes`).
/// Nothing when that choice is not known; `connection` (the stored item) fetches again after a new connection.
struct DeviceScopes: View {
    let provider: String
    var connection: String = ""
    @Environment(\.kelo) private var c
    @State private var model: DeviceScopesModel

    init(provider: String, connection: String = "", api: KeloApi) {
        self.provider = provider; self.connection = connection
        _model = State(initialValue: DeviceScopesModel(api: api))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let s = model.granted {
                Text(L(R.string.connector_permissions)).font(KeloFont.body).foregroundStyle(c.muted).padding(.top, 4)
                HStack(alignment: .top, spacing: 8) {
                    KeloIconView(.check, size: 16, tint: c.accent).padding(.top, 2)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(TokenText.fit(s.label.isEmpty ? s.id : s.label)).font(KeloFont.body).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        if !s.detail.isEmpty { Text(s.detail).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
                    }
                }
            }
        }
        .task(id: provider + "|" + connection) { await model.load(provider: provider) }
    }
}
