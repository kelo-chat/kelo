import SwiftUI
import CoreImage
import CoreImage.CIFilterBuiltins
import LocalAuthentication
import KeloCore
import KeloShared

// Building blocks the My pages share (Android my/Rows.kt bits that KeloShared does not have, ui/VaultGate.kt, the QR code).

/// A short message floating near the bottom for two seconds (Android's Toast).
struct ToastModifier: ViewModifier {
    @Binding var message: String?
    @Environment(\.kelo) private var c

    func body(content: Content) -> some View {
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

extension View {
    func toast(_ message: Binding<String?>) -> some View { modifier(ToastModifier(message: message)) }
}

/// Two or more choices side by side (.segmented).
struct Segmented: View {
    let options: [String]
    let selected: Int
    let onSelect: (Int) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options.indices, id: \.self) { i in
                Button { onSelect(i) } label: {
                    Text(options[i]).font(i == selected ? KeloFont.titleSmall : KeloFont.body).foregroundStyle(c.ink)
                        .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity).padding(.vertical, 10).padding(.horizontal, 4)
                        .background(RoundedRectangle(cornerRadius: 11).fill(i == selected ? c.card : c.bubble))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(i == selected ? [.isSelected, .isButton] : .isButton)
            }
        }
        .padding(3)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.bubble))
    }
}

/// A label and its value on one line (wrapping when long).
struct KeyLine: View {
    let label: String
    let value: String
    @Environment(\.kelo) private var c
    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(label).font(KeloFont.small).foregroundStyle(c.muted)
                Spacer(minLength: 8)
                Text(value.isEmpty ? "—" : value).font(KeloFont.small).foregroundStyle(c.ink).multilineTextAlignment(.trailing)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(KeloFont.small).foregroundStyle(c.muted)
                Text(value.isEmpty ? "—" : value).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A label and a bold value (usage numbers).
struct StatLine: View {
    let label: String
    let value: String
    @Environment(\.kelo) private var c
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Text(value).font(KeloFont.button).foregroundStyle(c.ink)
        }
        .padding(.vertical, 4)
    }
}

/// Rows straight on the page without a card, with the page's gutter (Android MenuGroup).
struct MenuGroup<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View { VStack(spacing: 0) { content }.padding(.horizontal, -16) }
}

/// A mono code in a tinted block (sign-in codes).
struct CodeBlock: View {
    let code: String
    @Environment(\.kelo) private var c
    var body: some View {
        Text(code).font(.system(size: 26, weight: .bold, design: .monospaced)).tracking(3).foregroundStyle(c.ink)
            .multilineTextAlignment(.center).textSelection(.enabled)
            .frame(maxWidth: .infinity).padding(.vertical, 14).padding(.horizontal, 8)
            .background(RoundedRectangle(cornerRadius: 14).fill(c.bubble))
            .accessibilityLabel(L(R.string.device_code_label) + ", " + code)
    }
}

/// The link as a QR code (error correction M, like the server's own), drawn crisp at any size.
enum QRCode {
    static func image(_ text: String) -> CGImage? {
        let f = CIFilter.qrCodeGenerator()
        f.message = Data(text.utf8)
        f.correctionLevel = "M"
        guard let out = f.outputImage?.transformed(by: CGAffineTransform(scaleX: 10, y: 10)) else { return nil }
        return CIContext().createCGImage(out, from: out.extent)
    }
}

/// What opening the vault takes on this phone (Android ui/VaultGate.kt, on iOS LocalAuthentication).
public enum GateDecision: Equatable, Sendable {
    /// Ask for Face ID / Touch ID, or the passcode
    case prompt
    /// The phone has no passcode at all: open it, and say so on the page
    case openNoLock
}

public enum VaultGateRules {
    /// `.deviceOwnerAuthentication` covers biometrics with the passcode as the fallback; without a passcode it cannot be evaluated.
    public static func decide(canEvaluate: Bool, errorCode: Int?) -> GateDecision {
        if canEvaluate { return .prompt }
        return errorCode == LAError.passcodeNotSet.rawValue ? .openNoLock : .prompt
    }

    /// The user backed out: nothing to say.
    public static func isCancel(_ code: Int) -> Bool {
        [LAError.userCancel.rawValue, LAError.appCancel.rawValue, LAError.systemCancel.rawValue, LAError.userFallback.rawValue].contains(code)
    }

    public static func meansNoLock(_ code: Int) -> Bool { code == LAError.passcodeNotSet.rawValue }
}

/// Asks the owner to prove it is them (Face ID, Touch ID or the passcode). Returns nil when not opened, else whether the phone has
/// no lock (true: nothing could be checked).
@MainActor enum VaultGate {
    static func open(reason: String) async -> Bool? {
        let ctx = LAContext()
        var err: NSError?
        let can = ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err)
        switch VaultGateRules.decide(canEvaluate: can, errorCode: err?.code) {
        case .openNoLock: return true
        case .prompt:
            do {
                return try await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) ? false : nil
            } catch let e as NSError {
                if VaultGateRules.meansNoLock(e.code) { return true }
                return nil
            }
        }
    }
}

/// A page-level helper: the text to show for a failed call.
@MainActor func errorText(_ e: Error, app: AppModel) -> String { app.message(for: e) }

/// The JSON body helper.
func body(_ pairs: [String: JSON]) -> JSON { .object(pairs) }
