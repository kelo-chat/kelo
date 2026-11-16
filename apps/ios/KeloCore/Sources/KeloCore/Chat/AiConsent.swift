import Foundation

// Asking once before chats go to an AI service (server ai_consent.py; App Store guideline 5.1.2(i); Android chat/AiConsent.kt).
// Every message goes with "ai_consent": "check"; while nobody has agreed on this server, the server holds it back with
// 428 {"detail": {"code": "ai_consent_required", "company": "Anthropic", "privacy_url": "https://kelo.chat/privacy"}}, before it is
// stored or sent. The chat then asks: Agree (PUT /api/ai-consent, then the same message again) or Not now (the text back in the
// composer, nothing sent). One answer covers the whole server; changing the model does not ask again (the model picker says where
// chats go instead).

public struct AiConsentNeed: Equatable, Sendable {
    public var company: String
    public var privacyUrl: String
    public init(company: String, privacyUrl: String) { self.company = company; self.privacyUrl = privacyUrl }
}

/// Where chats go with a provider: to a company, or nowhere beyond the owner's own network.
public enum ChatsGo: Equatable, Sendable {
    case to(String)
    case local
}

public enum AiConsent {
    public static let code = "ai_consent_required"
    /// The request field value that asks the server to check
    public static let check = "check"
    public static let privacyUrl = "https://kelo.chat/privacy"
    /// Providers whose address the owner types: without a company they stay on the owner's own network.
    static let local: Set<String> = ["custom", "lmstudio"]

    /// The 428 answer of a chat send, or nil for any other failure.
    public static func of(_ error: Error) -> AiConsentNeed? {
        guard let e = error as? ApiError, e.status == 428 else { return nil }
        return parse(e.detailJson)
    }

    public static func parse(_ detail: [String: JSON]?) -> AiConsentNeed? {
        guard let detail, detail["code"]?.stringValue == code else { return nil }
        let url = detail["privacy_url"]?.stringValue ?? ""
        return AiConsentNeed(company: detail["company"]?.stringValue ?? "", privacyUrl: url.isEmpty ? privacyUrl : url)
    }

    /// The line under a chosen provider ("Your chats go to Google"); nil when no provider is chosen or an older server does not say.
    public static func chatsGo(provider: String?, company: String?) -> ChatsGo? {
        guard let provider, !provider.isEmpty else { return nil }
        let c = (company ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !c.isEmpty { return .to(c) }
        return local.contains(provider) ? .local : nil
    }
}
