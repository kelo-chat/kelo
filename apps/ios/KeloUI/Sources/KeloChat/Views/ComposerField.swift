import SwiftUI
import KeloShared
#if os(iOS)
import UIKit
import UniformTypeIdentifiers
#endif

/// The chat's text field (Android chat/ChatComposer.kt Composer). On the iPhone a text view of its own, so that a long-press Paste
/// of a picture on the clipboard (a screenshot, a photo copied in Photos) attaches it like one picked with "+" (`onImages`: the
/// tray, the size limit, removable before sending). The system asks whether Kelo may paste, as for any paste. Text pastes as text.
struct ComposerField: View {
    @Binding var text: String
    @Binding var focused: Bool
    let placeholder: String
    let onImages: ([Data]) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        #if os(iOS)
        PasteTextView(text: $text, focused: $focused, ink: UIColor(c.ink), tint: UIColor(c.accent), onImages: onImages)
            .overlay(alignment: .topLeading) {
                if text.isEmpty {
                    Text(placeholder).font(KeloFont.bodyLarge).foregroundStyle(c.muted).allowsHitTesting(false).accessibilityHidden(true)
                }
            }
            .accessibilityLabel(placeholder)
        #else
        TextField("", text: $text, prompt: Text(placeholder).foregroundStyle(c.muted), axis: .vertical)
            .lineLimit(1...6).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
        #endif
    }
}

/// The pictures of a pasteboard's items: an image's own bytes when it has them (PNG, JPEG, HEIC ...), else a picture made from it.
enum PastedPictures {
    static func data(_ items: [[String: Any]]) -> [Data] {
        items.compactMap { item -> Data? in
            #if os(iOS)
            for (type, value) in item {
                guard let ut = UTType(type), ut.conforms(to: .image) else { continue }
                if let d = value as? Data, !d.isEmpty { return d }
                if let img = value as? UIImage { return img.jpegData(compressionQuality: 0.95) }
            }
            #endif
            return nil
        }
    }
}

#if os(iOS)
private struct PasteTextView: UIViewRepresentable {
    @Binding var text: String
    @Binding var focused: Bool
    let ink: UIColor
    let tint: UIColor
    let onImages: ([Data]) -> Void
    static let maxLines: CGFloat = 6

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> PictureTextView {
        let v = PictureTextView()
        v.delegate = context.coordinator
        v.font = UIFont.preferredFont(forTextStyle: .callout)
        v.adjustsFontForContentSizeCategory = true
        v.backgroundColor = .clear
        v.textContainerInset = .zero
        v.textContainer.lineFragmentPadding = 0
        v.isScrollEnabled = false
        v.autocapitalizationType = .sentences
        v.accessibilityIdentifier = "kelo.composer"
        v.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return v
    }

    func updateUIView(_ v: PictureTextView, context: Context) {
        context.coordinator.parent = self
        v.onImages = onImages
        v.textColor = ink
        v.tintColor = tint
        if v.text != text { v.text = text }
        // only a change of the binding moves the keyboard: the field's own focus changes (a tap, the list's swipe that dismisses
        // the keyboard) reach the binding through the delegate, and must not be undone here before they do
        let c = context.coordinator
        if focused != c.applied {
            c.applied = focused
            if focused, !v.isFirstResponder { DispatchQueue.main.async { if !v.isFirstResponder { v.becomeFirstResponder() } } }
            if !focused, v.isFirstResponder { DispatchQueue.main.async { v.resignFirstResponder() } }
        }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView v: PictureTextView, context: Context) -> CGSize? {
        let width = proposal.width ?? 300
        let fit = v.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
        let line = (v.font ?? UIFont.preferredFont(forTextStyle: .callout)).lineHeight
        let cap = ceil(line * Self.maxLines)
        let scroll = fit.height > cap
        if v.isScrollEnabled != scroll { DispatchQueue.main.async { v.isScrollEnabled = scroll } }
        return CGSize(width: width, height: max(ceil(line), min(fit.height, cap)))
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: PasteTextView
        /// The focus the binding last asked for (applied to the text view once)
        var applied = false
        init(_ parent: PasteTextView) { self.parent = parent }
        func textViewDidChange(_ v: UITextView) { if parent.text != v.text { parent.text = v.text } }
        func textViewDidBeginEditing(_ v: UITextView) { applied = true; if !parent.focused { parent.focused = true } }
        func textViewDidEndEditing(_ v: UITextView) { applied = false; if parent.focused { parent.focused = false } }
    }
}

/// A text view that also takes pictures from Paste (the edit menu shows Paste while the clipboard holds a picture).
final class PictureTextView: UITextView {
    var onImages: ([Data]) -> Void = { _ in }

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        if action == #selector(paste(_:)), UIPasteboard.general.hasImages { return true }
        return super.canPerformAction(action, withSender: sender)
    }

    override func paste(_ sender: Any?) {
        let board = UIPasteboard.general
        if board.hasImages {
            let pictures = PastedPictures.data(board.items)
            if !pictures.isEmpty { onImages(pictures); return }
        }
        super.paste(sender)
    }
}
#endif
