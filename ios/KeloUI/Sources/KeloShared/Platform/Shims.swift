import SwiftUI
#if os(iOS)
import UIKit
#elseif os(macOS)
import AppKit
#endif

// iPhone-only SwiftUI and UIKit calls wrapped so the screens also type-check against the macOS SDK (`swift build` without Xcode).
// On macOS these are no-ops or the AppKit equivalent; the app itself only ever runs on iOS.

#if os(iOS)
public typealias PlatformImage = UIImage
#else
public typealias PlatformImage = NSImage
#endif

public extension Image {
    init(platformImage: PlatformImage) {
        #if os(iOS)
        self.init(uiImage: platformImage)
        #else
        self.init(nsImage: platformImage)
        #endif
    }
}

public extension PlatformImage {
    /// Encoded JPEG bytes (for uploads).
    func keloJPEG(quality: Double = 0.85) -> Data? {
        #if os(iOS)
        return jpegData(compressionQuality: quality)
        #else
        guard let tiff = tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .jpeg, properties: [.compressionFactor: quality])
        #endif
    }
}

public enum KeloKeyboard { case text, url, email, number, ascii, phone }

public extension View {
    /// A small title in the navigation bar (iOS); nothing on macOS.
    func keloInlineTitle() -> some View {
        #if os(iOS)
        return navigationBarTitleDisplayMode(.inline)
        #else
        return self
        #endif
    }

    /// Keyboard type and no auto-capitalisation / correction for addresses, keys and codes.
    func keloKeyboard(_ kind: KeloKeyboard) -> some View {
        #if os(iOS)
        let type: UIKeyboardType = switch kind {
        case .text: .default
        case .url: .URL
        case .email: .emailAddress
        case .number: .numberPad
        case .ascii: .asciiCapable
        case .phone: .phonePad
        }
        return keyboardType(type)
            .textInputAutocapitalization(kind == .text ? .sentences : .never)
            .autocorrectionDisabled(kind != .text)
        #else
        return autocorrectionDisabled(kind != .text)
        #endif
    }

    /// Hides the system navigation bar (pages draw their own back bar like Android's BackTitleBar).
    func keloHideNavigationBar() -> some View {
        #if os(iOS)
        return toolbar(.hidden, for: .navigationBar)
        #else
        return self
        #endif
    }

    /// A full-screen page over everything (iOS fullScreenCover; a sheet on macOS).
    func keloFullScreen<Content: View>(isPresented: Binding<Bool>, @ViewBuilder content: @escaping () -> Content) -> some View {
        #if os(iOS)
        return fullScreenCover(isPresented: isPresented, content: content)
        #else
        return sheet(isPresented: isPresented, content: content)
        #endif
    }

    /// Keeps the screen out of screenshots and the app switcher where iOS allows (sensitive pages: keychain, card entry). iOS has
    /// no FLAG_SECURE; the app switcher snapshot is covered by `PrivacyCover` in the shell instead.
    func keloSensitive() -> some View { privacySensitive() }
}

/// The clipboard.
public enum Clipboard {
    @MainActor public static func copy(_ text: String) {
        #if os(iOS)
        UIPasteboard.general.string = text
        #else
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        #endif
    }

    @MainActor public static func text() -> String? {
        #if os(iOS)
        return UIPasteboard.general.string
        #else
        return NSPasteboard.general.string(forType: .string)
        #endif
    }
}

/// Light taps under the finger (Android ui/Haptics.kt).
public enum Haptics {
    @MainActor public static func tick() {
        #if os(iOS)
        UISelectionFeedbackGenerator().selectionChanged()
        #endif
    }

    @MainActor public static func success() {
        #if os(iOS)
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        #endif
    }

    @MainActor public static func warning() {
        #if os(iOS)
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
        #endif
    }
}

/// The phone's model name for the device name the server keeps ("iPhone · Kelo").
public enum DeviceInfo {
    @MainActor public static var model: String {
        #if os(iOS)
        return UIDevice.current.model
        #else
        return Host.current().localizedName ?? "Mac"
        #endif
    }

    /// Opens the app's page in the iPhone's Settings (permissions turned off earlier).
    @MainActor public static func openAppSettings() {
        #if os(iOS)
        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
        #endif
    }

    /// Hides the keyboard.
    @MainActor public static func endEditing() {
        #if os(iOS)
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
        #endif
    }
}
