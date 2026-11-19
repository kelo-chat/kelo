#if os(iOS)
import SwiftUI
import UIKit
import KeloCore
import KeloShared

/// The keyboard for the page while the owner holds the tab (live typing, server live_v 3; Android agent/RemoteKeyboard.kt): a hidden
/// one-line field that catches what the owner's keyboard does (letters, a Korean syllable forming, deletions, Return) and hands each
/// change to `live` as steps for the page's own field, so the page shows every keystroke at once. Nothing is drawn and nothing typed is
/// kept or logged.
struct RemoteKeyboard: UIViewRepresentable {
    let live: LiveModel
    /// The focused field of the page (nil: none has focus).
    let typing: Typing?
    /// Counts the owner's taps on the keyboard key: each one shows or hides the keyboard.
    let toggles: Int

    func makeCoordinator() -> Coordinator { Coordinator(live) }

    func makeUIView(context: Context) -> CaptureField {
        let f = CaptureField(frame: CGRect(x: 0, y: 0, width: 1, height: 1))
        f.delegate = context.coordinator
        f.addTarget(context.coordinator, action: #selector(Coordinator.changed(_:)), for: .editingChanged)
        f.onEmptyDelete = { [weak live = context.coordinator.live] in live?.typingKeys(["Backspace"]) }
        f.onKey = { [weak live = context.coordinator.live] name in live?.typingKeys([name]) }
        context.coordinator.field = f
        context.coordinator.setEmpty(f)
        return f
    }

    func updateUIView(_ f: CaptureField, context: Context) {
        let c = context.coordinator
        f.apply(kind: typing?.kind)
        // the page's field changed or the app asked to start again: the buffer starts from empty
        if c.resets != live.typingResets { c.resets = live.typingResets; c.setEmpty(f) }
        if c.focusId != typing?.field {
            c.focusId = typing?.field
            if live.typingReset() { c.setEmpty(f) }
        }
        let editing = typing != nil
        if editing && !c.wasEditing { f.becomeFirstResponder() }                      // a field took focus: the keyboard comes up, once per focus
        if !editing && c.wasEditing && !c.openByHand {                                // gone 400 ms after the focus left
            c.hideTask?.cancel()
            c.hideTask = Task { [weak f] in
                try? await Task.sleep(nanoseconds: 400_000_000)
                if !Task.isCancelled { f?.resignFirstResponder() }
            }
        }
        if editing { c.hideTask?.cancel() }
        c.wasEditing = editing
        if c.toggles != toggles {
            c.toggles = toggles
            if f.isFirstResponder { c.openByHand = false; f.resignFirstResponder() } else { c.openByHand = true; f.becomeFirstResponder() }
        }
    }

    static func dismantleUIView(_ f: CaptureField, coordinator: Coordinator) {
        coordinator.hideTask?.cancel()
        coordinator.live.typingCommit()   // the page keeps what the owner saw
    }

    @MainActor
    final class Coordinator: NSObject, UITextFieldDelegate {
        let live: LiveModel
        weak var field: CaptureField?
        var resets = 0
        var focusId: String?
        var wasEditing = false
        var openByHand = false
        var toggles = 0
        var hideTask: Task<Void, Never>?
        private var lastText = RemoteTyping.mark
        private var lastSelEnd = 1
        private var quiet = false   // our own changes to the field are not the owner's

        init(_ live: LiveModel) { self.live = live; resets = live.typingResets }

        func setEmpty(_ f: CaptureField) {
            quiet = true
            f.text = RemoteTyping.mark
            lastText = RemoteTyping.mark; lastSelEnd = 1
            quiet = false
        }

        private func offset(_ f: UITextField, _ p: UITextPosition) -> Int { f.offset(from: f.beginningOfDocument, to: p) }

        @objc func changed(_ f: UITextField) {
            guard !quiet else { return }
            let text = f.text ?? ""
            let selEnd = f.selectedTextRange.map { offset(f, $0.end) } ?? text.utf16.count
            var cs: Int?, ce: Int?
            if let m = f.markedTextRange { cs = offset(f, m.start); ce = offset(f, m.end) }
            let typed = RemoteTyping.typed(text: text, selEnd: selEnd, compStart: cs, compEnd: ce)
            live.typingUpdate(typed)
            lastText = text; lastSelEnd = selEnd
            guard typed.composing.isEmpty, let cf = f as? CaptureField else { return }   // never touch the field while a composition is shown
            if !text.hasPrefix(RemoteTyping.mark) || RemoteTyping.mustReset(typed) {
                _ = live.typingReset()
                setEmpty(cf)
            } else if selEnd != text.utf16.count, let end = f.endOfDocument as UITextPosition? {
                quiet = true
                f.selectedTextRange = f.textRange(from: end, to: end)
                lastSelEnd = text.utf16.count
                quiet = false
            }
        }

        // the caret moved with no text change (a spacebar swipe on the keyboard): arrows for the page, then start again
        func textFieldDidChangeSelection(_ f: UITextField) {
            guard !quiet, f.markedTextRange == nil, (f.text ?? "") == lastText, let r = f.selectedTextRange else { return }
            let end = offset(f, r.end)
            guard end != lastSelEnd else { return }
            let delta = end - lastSelEnd
            live.typingKeys(Array(repeating: delta < 0 ? "ArrowLeft" : "ArrowRight", count: min(abs(delta), RemoteTyping.maxSteps)))
            if let cf = f as? CaptureField { setEmpty(cf) }
        }

        // the return key: commit what composes, then Enter (a textarea takes it as a new line)
        func textFieldShouldReturn(_ f: UITextField) -> Bool {
            live.typingKeys(["Enter"])
            return false
        }
    }
}

/// The hidden capture field: 1 x 1, almost transparent, no autocorrection or smart punctuation (the page must get exactly what was typed).
final class CaptureField: UITextField {
    var onEmptyDelete: (() -> Void)?
    var onKey: ((String) -> Void)?
    private var kind: String?

    override init(frame: CGRect) {
        super.init(frame: frame)
        alpha = 0.02
        autocorrectionType = .no
        spellCheckingType = .no
        smartQuotesType = .no
        smartDashesType = .no
        smartInsertDeleteType = .no
        autocapitalizationType = .none
        returnKeyType = .go
        keyboardType = .default   // never .asciiCapable: the Korean and Japanese keyboards must be there
        accessibilityLabel = L(R.string.live_keyboard_desc)
    }
    required init?(coder: NSCoder) { fatalError() }

    /// The keyboard that fits the focused field: a password field gets the secure one (nothing is shown in the app either way).
    func apply(kind: String?) {
        guard kind != self.kind else { return }
        self.kind = kind
        isSecureTextEntry = kind == "secret"
        switch kind {
        case "number": keyboardType = .numbersAndPunctuation
        case "tel": keyboardType = .phonePad
        case "email": keyboardType = .emailAddress
        case "url": keyboardType = .URL
        case "search": keyboardType = .default
        default: keyboardType = .default
        }
        if isFirstResponder { reloadInputViews() }
    }

    /// Delete on a field the system already sees as empty gives no change event: the page still gets its Backspace.
    override func deleteBackward() {
        let wasEmpty = (text ?? "").isEmpty
        super.deleteBackward()
        if wasEmpty { onEmptyDelete?() }
    }

    // a hardware keyboard: the keys that are not typing go to the page as keys
    override var keyCommands: [UIKeyCommand]? {
        let keys: [(String, String)] = [
            (UIKeyCommand.inputLeftArrow, "ArrowLeft"), (UIKeyCommand.inputRightArrow, "ArrowRight"), (UIKeyCommand.inputUpArrow, "ArrowUp"),
            (UIKeyCommand.inputDownArrow, "ArrowDown"), (UIKeyCommand.inputEscape, "Escape"), ("\t", "Tab"),
            (UIKeyCommand.inputHome, "Home"), (UIKeyCommand.inputEnd, "End"), (UIKeyCommand.inputPageUp, "PageUp"),
            (UIKeyCommand.inputPageDown, "PageDown"), (UIKeyCommand.inputDelete, "Delete"),
        ]
        return keys.map { input, name in
            let c = UIKeyCommand(title: name, action: #selector(hardwareKey(_:)), input: input, modifierFlags: [], propertyList: name)
            c.wantsPriorityOverSystemBehavior = true
            return c
        }
    }

    @objc private func hardwareKey(_ c: UIKeyCommand) {
        if let name = c.propertyList as? String { onKey?(name) }
    }
}
#endif
