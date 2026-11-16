import SwiftUI

/// A short message floating near the bottom for two seconds (Android's Toast). Cleared by itself when it has shown.
public struct KeloToastModifier: ViewModifier {
    @Binding var message: String?
    @Environment(\.kelo) private var c

    public func body(content: Content) -> some View {
        content.overlay(alignment: .bottom) {
            if let m = message {
                Text(m).font(KeloFont.small).foregroundStyle(c.bg).multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Capsule().fill(c.ink.opacity(0.92)))
                    .padding(.horizontal, 24).padding(.bottom, 28)
                    .transition(.opacity)
                    .task(id: m) {
                        try? await Task.sleep(nanoseconds: 2_200_000_000)
                        if message == m { message = nil }
                    }
                    .accessibilityAddTraits(.updatesFrequently)
            }
        }
        .animation(.easeOut(duration: 0.15), value: message)
    }
}

public extension View {
    func keloToast(_ message: Binding<String?>) -> some View { modifier(KeloToastModifier(message: message)) }
}
