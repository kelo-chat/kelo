import SwiftUI
import KeloCore
import KeloShared

/// The line under the address field for each way a move can fail (nil: it went through). Android ServerAddressRules.
public enum ServerAddressRules {
    @MainActor public static func problem(_ r: AddressMove) -> String? {
        switch r {
        case .moved: return nil
        case .sameAddress: return L(R.string.server_address_same)
        case .invalid, .notSupported: return L(R.string.server_address_invalid)
        case .unreachable: return L(R.string.server_address_unreachable)
        case .otherServer: return L(R.string.server_address_other)
        }
    }
}

/// My -> Server address -> Change address: the same Kelo server at a new https address. The app checks that the new address answers
/// with this server's own key; only then it switches, keeping the device token and the key.
struct ServerAddressSheet: View {
    let current: String
    let move: (String) async -> AddressMove
    let onMoved: () -> Void
    let onClose: () -> Void
    @State private var text = ""
    @State private var busy = false
    @State private var problem: String?
    @Environment(\.kelo) private var c

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(L(R.string.server_address_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                    .accessibilityAddTraits(.isHeader)
                MutedText(L(R.string.server_address_body, current), small: true)
                KeloTextField("https://", text: $text, keyboard: .url, maxLength: 512)
                    .onChange(of: text) { _, _ in problem = nil }
                if let problem { ErrorText(problem, small: true) }
                PrimaryButton(L(busy ? R.string.server_address_checking : R.string.server_address_switch),
                              enabled: !busy && !text.trimmingCharacters(in: .whitespaces).isEmpty, busy: busy) {
                    busy = true; problem = nil
                    Task {
                        let r = await move(text)
                        busy = false
                        problem = ServerAddressRules.problem(r)
                        if case .moved = r { onMoved() }
                    }
                }
            }
            .padding(20)
        }
    }
}
