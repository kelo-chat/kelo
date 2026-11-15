import Foundation
import KeloCore

// The rules of the model page, as the chat's model sheet and 마이 → 모델 both use them (ports of the Android objects of the same names).

/// A model as provider + id; "" + "" is none (the server's default, or no fallback).
public struct ModelChoice: Equatable, Hashable, Sendable {
    public var provider: String
    public var model: String
    public init(_ provider: String, _ model: String) { self.provider = provider; self.model = model }
    public static let none = ModelChoice("", "")
}

/// Android chat/ModelSheet.kt ModelRules: the main model and the fallback never share a provider.
public enum ModelRules {
    /// A fallback on the main model's own provider (from before that rule) is shown as empty.
    public static func initialFallback(_ p: Profile) -> ModelChoice {
        if !p.fallbackProvider.isEmpty && p.fallbackProvider == p.provider { return .none }
        return ModelChoice(p.fallbackProvider, p.fallbackModel)
    }

    /// Moving the main model onto the fallback's provider empties the fallback. Returns the new fallback and whether it was cleared.
    public static func afterMainChange(_ main: ModelChoice, _ fallback: ModelChoice) -> (ModelChoice, Bool) {
        if !main.provider.isEmpty && main.provider == fallback.provider { return (.none, true) }
        return (fallback, false)
    }

    /// A chosen provider needs a model name.
    public static func complete(_ c: ModelChoice) -> Bool { c.provider.isEmpty || !c.model.trimmingCharacters(in: .whitespaces).isEmpty }

    /// The providers the fallback may come from: any but the main one.
    public static func fallbackProviders(_ all: [ModelProvider], main: String) -> [ModelProvider] { all.filter { $0.name != main } }
}

/// Checks the address of an OpenAI-compatible server before it is sent (Android EndpointRules).
public enum EndpointRules {
    public enum Problem: Equatable, Sendable { case empty, spaces, scheme, host }

    public static func check(_ url: String) -> Problem? {
        let t = url.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .empty }
        if t.contains(where: \.isWhitespace) { return .spaces }
        guard let sep = t.range(of: "://") else { return .scheme }
        let scheme = t[..<sep.lowerBound].lowercased()
        if scheme != "http" && scheme != "https" { return .scheme }
        var rest = String(t[sep.upperBound...])
        for c in ["/", "?", "#"] { if let r = rest.range(of: c) { rest = String(rest[..<r.lowerBound]) } }
        if let at = rest.range(of: "@", options: .backwards) { rest = String(rest[at.upperBound...]) }
        let host = rest.hasPrefix("[") ? rest : (rest.split(separator: ":").first.map(String.init) ?? "")
        return host.trimmingCharacters(in: .whitespaces).isEmpty ? .host : nil
    }
}

/// Which API key box a provider gets (Android KeyRules).
public enum KeyRules {
    public enum State: Equatable, Sendable { case inApp, env, none }
    public static func state(_ keySource: String?) -> State {
        switch keySource { case "app": .inApp; case "env": .env; default: .none }
    }
    /// A pasted key must be at least this long before 저장 turns on.
    public static func keyReady(_ key: String) -> Bool { key.trimmingCharacters(in: .whitespacesAndNewlines).count >= 8 }
}

/// Unofficial providers need the user's agreement once per consent version (Android ProviderConsentRules).
public enum ProviderConsentRules {
    public enum Service: Equatable, Sendable { case claudeSubscription, chatgptSubscription, antigravity, qwen, minimax, other }

    public static func needs(_ p: ModelProvider) -> Bool { p.unofficial && (p.consent == nil || (p.consent?.version ?? 0) < p.consentVersion) }

    public static func group(_ p: ModelProvider) -> Int { p.unofficial ? 1 : 0 }

    public static func service(_ name: String) -> Service {
        if name.hasPrefix("claude-subscription") { return .claudeSubscription }
        if name.hasPrefix("antigravity") { return .antigravity }
        if name.hasPrefix("openai-codex") { return .chatgptSubscription }
        if name.hasPrefix("qwen") { return .qwen }
        if name.hasPrefix("minimax") { return .minimax }
        return .other
    }

    private static let known: [String: String] = ["anthropic": "이", "google": "이", "openai": "가", "alibaba": "가", "minimax": "가"]

    /// The Korean subject particle after a vendor's name, as read aloud.
    public static func subjectParticle(_ vendor: String) -> String {
        let v = vendor.trimmingCharacters(in: .whitespaces)
        if let k = known[v.lowercased()] { return k }
        guard let last = v.unicodeScalars.last else { return "가" }
        if (0xAC00...0xD7A3).contains(last.value) { return (last.value - 0xAC00) % 28 != 0 ? "이" : "가" }
        return "aeiouy".contains(Character(String(last).lowercased())) ? "가" : "이"
    }

    /// The providers among the chosen main and fallback that still need the user's agreement, main first.
    public static func pending(main: String, fallback: String, providers: [ModelProvider]) -> [ModelProvider] {
        var seen = Set<String>()
        return [main, fallback].filter { !$0.isEmpty && seen.insert($0).inserted }
            .compactMap { n in providers.first { $0.name == n } }.filter(needs)
    }

    /// "consent_required:<provider>" in a 428 from PUT /api/profile, or nil.
    public static func requiredBy(_ e: ApiError) -> String? {
        guard e.status == 428, let r = e.detail.range(of: "consent_required:") else { return nil }
        let n = e.detail[r.upperBound...].trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? nil : n
    }

    public static func askedKey(_ p: ModelProvider) -> String { "consent.asked.\(p.name).\(p.consentVersion)" }
}

/// When the phone sign-in shows and what the server's answers mean (Android SignInRules). The box is offered only when the server
/// says the app may sign this provider in (`signin`), never otherwise.
public enum SignInRules {
    public enum Problem: Equatable, Sendable { case consent, already, off, failed }

    public static func offered(signin: Bool, signedIn: Bool?) -> Bool { signin && signedIn != true }
    public static func isDeviceCode(_ s: SignInSession) -> Bool { s.kind == "device_code" }
    public static func pasteReady(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return !t.isEmpty && !t.contains(where: \.isWhitespace) && t.count <= 4096
    }
    public static func finished(_ s: SignInSession) -> Bool { s.state == "done" }
    public static func ended(_ s: SignInSession) -> Bool { s.state == "failed" || s.state == "expired" }

    public static func problem(_ e: ApiError) -> Problem {
        if e.status == 428 { return .consent }
        if e.status == 409 && e.detail.hasPrefix("already_signed_in") { return .already }
        if e.status == 503 { return .off }
        return .failed
    }

    public static func readable(_ e: ApiError) -> String {
        if e.isNetwork { return "" }
        if let r = e.detail.range(of: "already_signed_in: ") { return String(e.detail[r.upperBound...]).trimmingCharacters(in: .whitespaces) }
        return e.detail.trimmingCharacters(in: .whitespaces)
    }

    /// "4:05" from seconds left.
    public static func mmss(_ seconds: Int64) -> String { ConnectorRules.mmss(seconds) }
}

/// Android SignOutRules.
public enum SignOutRules {
    public enum Outcome: Equatable, Sendable { case done, doneFallbackCleared, doneEnvLeft, mainModel, failed }

    public static func offered(_ signedIn: Bool?) -> Bool { signedIn == true }
    public static func isMain(_ name: String, mainProvider: String) -> Bool { !mainProvider.isEmpty && mainProvider == name }
    public static func done(_ o: Outcome) -> Bool { o != .failed && o != .mainModel }

    public static func outcome(_ result: SignOutResult?, _ error: ApiError?) -> Outcome {
        if let e = error {
            if e.status == 409 { return .mainModel }
            if e.status == 404 { return .done }
            return .failed
        }
        if result?.stillSignedIn == true { return .doneEnvLeft }
        if result?.fallbackCleared == true { return .doneFallbackCleared }
        return .done
    }
}


/// How the thinking level can be chosen for the current provider.
public enum EffortChoice: Equatable, Sendable {
    case levels([String])
    /// The provider takes it from the model name: the server's sentence says how
    case fromModel(String)
    /// The server answered 400 to the level (a custom endpoint that does not take it): none is sent, the server's sentence says so
    case refused(String)
    case none
}

public enum EffortRules {
    /// `facts`: the chosen model's facts; the levels its provider's list states for it win over the provider's own levels
    public static func effort(_ p: ModelProvider?, facts: ModelEffort? = nil) -> EffortChoice {
        guard let p else { return .none }
        if let listed = facts?.effortLevels {
            if !listed.isEmpty { return .levels([""] + listed) }
            if let n = facts?.effortNote, !n.trimmingCharacters(in: .whitespaces).isEmpty { return .refused(n) }
            return .none
        }
        if !p.effortLevels.isEmpty { return .levels([""] + p.effortLevels) }   // "" = the model's own default
        if let n = p.effortNote, !n.trimmingCharacters(in: .whitespaces).isEmpty { return .fromModel(n) }
        return .none
    }

    private static let levelOrder = ["low", "medium", "high", "xhigh", "max"]

    /// The level to show as chosen: `effort` itself when the provider has it, else the highest one below it (what the server sends,
    /// effort.clamp: a custom server's levels skip xhigh, so a saved "xhigh" is sent and shown as high); "" = the default.
    public static func shownLevel(_ levels: [String], _ effort: String) -> String {
        if levels.contains(effort) { return effort }
        guard let at = levelOrder.firstIndex(of: effort) else { return "" }
        let lower = levels.filter { !$0.isEmpty && (levelOrder.firstIndex(of: $0).map { $0 <= at } ?? false) }
        return lower.last ?? levels.first { !$0.isEmpty } ?? ""
    }

    /// The server's sentence for a provider or model with no levels to pick (agy's model names, a server that refused it); else nil
    public static func effortNote(_ effort: EffortChoice) -> String? {
        switch effort {
        case .fromModel(let n), .refused(let n): n
        default: nil
        }
    }
}
