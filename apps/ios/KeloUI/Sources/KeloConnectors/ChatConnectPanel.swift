import SwiftUI
import KeloCore
import KeloShared

/// The connect panel of one service by its id, as the chat's connect card shows it: loads the connector, then its own panel
/// (Android ConnectPanel(id)). Loads again after the panel changed something and when the user comes back to the app.
public struct ChatConnectPanel: View {
    let service: String
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @State private var tick = 0

    public init(service: String, onChanged: @escaping () -> Void) { self.service = service; self.onChanged = onChanged }

    public var body: some View {
        if let api = app.api { Loaded(service: service, api: api, tick: $tick, onChanged: onChanged).id(ObjectIdentifier(api)) }
    }

    private struct Loaded: View {
        let service: String
        let api: KeloApi
        @Binding var tick: Int
        let onChanged: () -> Void
        @State private var model: ConnectorInfoModel

        init(service: String, api: KeloApi, tick: Binding<Int>, onChanged: @escaping () -> Void) {
            self.service = service; self.api = api; self._tick = tick; self.onChanged = onChanged
            _model = State(initialValue: ConnectorInfoModel(api: api, service: service))
        }

        var body: some View { content.reloadOnReturn("\(service)#\(tick)") { await model.load() } }

        @ViewBuilder private var content: some View {
            if let info = model.info {
                if info.connect != nil {
                    ConnectPanel(connector: info, api: api, onChanged: { tick += 1; onChanged() })
                } else {
                    MutedText(info.status == "unsupported" ? info.description : L(R.string.connector_not_yet), small: true)
                }
            } else {
                SkeletonRows(rows: 2)
            }
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

    var body: some View {
        if let api = app.api { Loaded(service: service, size: size, api: api).id(ObjectIdentifier(api)) }
    }

    private struct Loaded: View {
        let service: String
        let size: CGFloat
        @Environment(\.kelo) private var c
        @State private var model: ConnectorInfoModel

        init(service: String, size: CGFloat, api: KeloApi) {
            self.service = service; self.size = size
            _model = State(initialValue: ConnectorInfoModel(api: api, service: service))
        }

        var body: some View {
            Group {
                if let info = model.info { ConnectorTile(x: info, size: size) } else { KeloIconView(.plug, size: 20, tint: c.ink) }
            }
            .task(id: service) { await model.load() }
        }
    }
}
