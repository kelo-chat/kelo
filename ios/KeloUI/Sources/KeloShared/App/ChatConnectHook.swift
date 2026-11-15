import SwiftUI

/// The connector's own connect panel, for the chat's connect card (Android ChatScreen ConnectCard embeds connectors.ConnectPanel).
/// The chat cannot import the Connectors module, so the shell hands the panel down: `(service id, onChanged) -> panel`.
/// `icon`: the service's icon for the card's title, `(service id, size) -> view` (nil: the plug).
public struct ChatConnectPanelBuilder: @unchecked Sendable {
    public let make: (String, @escaping () -> Void) -> AnyView
    public let icon: ((String, CGFloat) -> AnyView)?
    public init(_ make: @escaping (String, @escaping () -> Void) -> AnyView, icon: ((String, CGFloat) -> AnyView)? = nil) {
        self.make = make; self.icon = icon
    }
}

private struct ChatConnectPanelKey: EnvironmentKey {
    static let defaultValue: ChatConnectPanelBuilder? = nil
}

public extension EnvironmentValues {
    var keloConnectPanel: ChatConnectPanelBuilder? {
        get { self[ChatConnectPanelKey.self] }
        set { self[ChatConnectPanelKey.self] = newValue }
    }
}
