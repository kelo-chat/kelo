import SwiftUI
import KeloCore
import KeloShared

/// The connect panel of one service by its id, as the chat's connect card shows it: loads the connector, then its own panel
/// (Android ConnectPanel(id)). Loads again after the panel changed something and when the user comes back to the app.
public struct ChatConnectPanel: View {
    let service: String
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @State private var info: ConnectorInfo?
    @State private var tick = 0

    public init(service: String, onChanged: @escaping () -> Void) { self.service = service; self.onChanged = onChanged }

    public var body: some View {
        Group {
            if let info {
                if info.connect != nil {
                    ConnectPanel(connector: info, onChanged: { tick += 1; onChanged() })
                } else {
                    MutedText(info.status == "unsupported" ? info.description : L(R.string.connector_not_yet), small: true)
                }
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .reloadOnReturn("\(service)#\(tick)") {
            if let l = try? await app.requireApi().connectorList() { info = l.first { $0.id == service } }
        }
    }

    /// What the shell puts in the environment for the chat.
    public static var builder: ChatConnectPanelBuilder {
        ChatConnectPanelBuilder({ id, changed in AnyView(ChatConnectPanel(service: id, onChanged: changed)) },
                                icon: { id, size in AnyView(ChatConnectorIcon(service: id, size: size)) })
    }
}

/// The service's tile for the chat's connect card title: the plug until its row is loaded.
struct ChatConnectorIcon: View {
    let service: String
    let size: CGFloat
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var info: ConnectorInfo?

    var body: some View {
        Group {
            if let info { ConnectorTile(x: info, size: size) } else { KeloIconView(.plug, size: 20, tint: c.ink) }
        }
        .task(id: service) {
            if let l = try? await app.requireApi().connectorList() { info = l.first { $0.id == service } }
        }
    }
}
