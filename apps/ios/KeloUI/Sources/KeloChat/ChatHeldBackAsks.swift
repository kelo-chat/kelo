import Foundation
import KeloCore
import KeloShared

/// A message the server refused with 409 `secret_in_message`: `text` is exactly what was sent (the spans point into it), with the
/// `answers` it carried. `fromComposer`: it was the typed text (put back in the composer, with the attachments when
/// `tookAttachments`). Memory only; never printed (the description leaves the text out).
public final class SecretAsk: Identifiable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let id = UUID()
    public let found: SecretFound
    let text: String
    let answers: [ReplyAnswer]
    public let fromComposer: Bool
    let tookAttachments: Bool
    /// The composer's text as it was put back, and the spans moved onto it (empty for a button's text)
    let composer: String
    let composerSpans: [SecretSpan]

    init(found: SecretFound, text: String, answers: [ReplyAnswer], fromComposer: Bool, tookAttachments: Bool,
         composer: String = "", composerSpans: [SecretSpan] = []) {
        self.found = found; self.text = text; self.answers = answers; self.fromComposer = fromComposer
        self.tookAttachments = tookAttachments; self.composer = composer; self.composerSpans = composerSpans
    }

    /// The detected values, for the save step's field (cut from `text`; memory only)
    public var values: [String] { SecretGuard.values(text, found.spans) }
    public var description: String { "SecretAsk(\(found.target.type), \(found.spans.count) spans)" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["target": found.target.type, "spans": found.spans.count]) }
}

/// A message held back until someone agrees that chats go to the AI service (`AiConsent`). Memory only; never printed.
public final class ConsentAsk: Identifiable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let id = UUID()
    public let need: AiConsentNeed
    let text: String
    let answers: [ReplyAnswer]
    public let fromComposer: Bool
    let tookAttachments: Bool
    let secrets: String?

    init(need: AiConsentNeed, text: String, answers: [ReplyAnswer], fromComposer: Bool, tookAttachments: Bool, secrets: String?) {
        self.need = need; self.text = text; self.answers = answers; self.fromComposer = fromComposer
        self.tookAttachments = tookAttachments; self.secrets = secrets
    }

    public var description: String { "ConsentAsk(\(need.company))" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["company": need.company]) }
}
