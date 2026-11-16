import Foundation
import KeloCore
import KeloShared

// A message the server held back: the secret sheet (key or password in the text) and the AI-service consent question.

extension ChatModel {
    // ---- the secret sheet (a message held back for a key or password; SecretGuard) -------------------------------------------

    /// The save step kept `value` in the Keychain (straight from the app to the vault): in the composer, every span holding that
    /// value becomes `reference` ("(saved to Keychain: GitHub)"), so the rest of the message can still be sent; the sheet closes.
    public func secretSaved(value: String, reference: String) {
        guard let a = secretAsk else { return }
        secretAsk = nil
        input = referenced(input, a, reference: reference, value: value)
    }

    /// A card number goes on to the wallet's card form, filled in there from `handoff` (memory only); the sheet closes.
    public func secretToCard(_ handoff: SecretHandoff) {
        guard let a = secretAsk else { return }
        handoff.offer(Array(a.values.prefix(1)))
        cardRef = a
        secretAsk = nil
    }

    /// The card form kept the card: the composer gets `reference` in place of the number.
    public func secretCardSaved(reference: String) {
        guard let a = cardRef else { return }
        cardRef = nil
        input = referenced(input, a, reference: reference, value: nil)
    }

    /// The composer is changed only while it still holds the text that was put back (the owner may have rewritten it since).
    private func referenced(_ input: String, _ a: SecretAsk, reference: String, value: String?) -> String {
        guard a.fromComposer, input == a.composer else { return input }
        return SecretGuard.replace(input, a.composerSpans, with: reference, value: value)
    }

    /// "Send as is": the very same text again, with `"secrets": "allow"` (only ever after this tap).
    public func secretSendAsIs() {
        guard let a = secretAsk else { return }
        secretAsk = nil
        if busy { return }
        let files = trayFiles(a.tookAttachments)
        start(a.text, fromComposer: a.fromComposer, tookAttachments: a.tookAttachments, answers: a.answers, secrets: SecretGuard.allow,
              files: files, autoText: !files.isEmpty && a.composer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    /// "Edit" (or the sheet dismissed): back to the composer with the text intact; a button's text is put there to be changed.
    public func secretEdit() {
        guard let a = secretAsk else { return }
        secretAsk = nil
        if !a.fromComposer && input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { input = a.text }
    }

    // ---- asking once before chats go to the AI service (AiConsent) ----------------------------------------------------------
    /// "Agree": the server keeps it (once, for every device), then the very same message goes.
    public func consentAgree() {
        guard let a = consentAsk, !consentBusy, let api = api() else { return }
        consentBusy = true
        consentError = nil
        Task { [weak self] in
            do {
                _ = try await api.request("PUT", "/api/ai-consent", body: ["accepted": .bool(true)])
                guard let self else { return }
                self.consentBusy = false
                self.consentAsk = nil
                if !self.busy {
                    let files = self.trayFiles(a.tookAttachments)
                    self.start(a.text, fromComposer: a.fromComposer, tookAttachments: a.tookAttachments, answers: a.answers, secrets: a.secrets,
                               files: files, autoText: !files.isEmpty && self.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            } catch {
                self?.consentBusy = false
                self?.consentError = (error as? ApiError)?.detail ?? String(describing: error)
            }
        }
    }

    /// "Not now" (or the sheet dismissed): nothing is sent; the text stays in the composer (a button's text is put there).
    public func consentNotNow() {
        guard let a = consentAsk else { return }
        consentAsk = nil
        consentError = nil
        if !a.fromComposer && input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { input = a.text }
    }
}
