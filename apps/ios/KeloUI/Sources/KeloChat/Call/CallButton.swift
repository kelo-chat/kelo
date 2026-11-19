import SwiftUI
import KeloCore
import KeloShared

/// The in-app call button in the chat header (brief §5.3, tag chat.call).
public struct CallButton: View {
    let calls: CallCenter
    @Environment(\.kelo) private var c

    public init(calls: CallCenter) {
        self.calls = calls
    }

    public var body: some View {
        if calls.available {
            Button(action: { calls.ringFromButton() }) {
                KeloIconView(.handset, size: 20, tint: c.ink)
                    .padding(10)
            }
            .accessibilityIdentifier("chat.call")
            .accessibilityLabel(L(R.string.call_button))
        }
    }
}
