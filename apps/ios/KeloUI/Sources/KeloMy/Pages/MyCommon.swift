import SwiftUI
import CoreImage
import CoreImage.CIFilterBuiltins
import LocalAuthentication
import KeloShared

// Building blocks the My pages share (Android my/Rows.kt bits that KeloShared does not have, ui/VaultGate.kt, the QR code).

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
