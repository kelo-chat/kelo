import Foundation
import KeloCore

/// What a model row may say about the model: only what the id's tier words or the server's facts tell.
public enum ModelTrait: Sendable { case fast, reasoning, large, images, long }

public enum ModelTraits {
    public static let longContext: Int64 = 400_000
    private static let fastWords: Set<String> = ["haiku", "flash", "mini", "nano", "lite", "small", "fast", "instant", "turbo"]
    private static let reasoningWords: Set<String> = ["thinking", "reasoner", "reasoning", "r1"]
    private static let largeWords: Set<String> = ["opus", "pro", "ultra", "large"]

    public static func of(_ id: String, facts: ModelFacts?) -> [ModelTrait] {
        let last = id.lowercased().components(separatedBy: "/").last ?? id.lowercased()
        let words = Set(last.split(whereSeparator: { "-_ :.".contains($0) }).map(String.init))
        var out: [ModelTrait] = []
        if !words.isDisjoint(with: fastWords) { out.append(.fast) }
        if !words.isDisjoint(with: reasoningWords) { out.append(.reasoning) }
        if !words.isDisjoint(with: largeWords) && !out.contains(.fast) { out.append(.large) }
        if facts?.images == true { out.append(.images) }
        if (facts?.context ?? 0) >= longContext { out.append(.long) }
        return out
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

/// A provider + model pair as the model sheet edits it.
public struct ModelChoice: Equatable, Hashable, Sendable {
    public var provider: String
    public var model: String
    public init(_ provider: String, _ model: String) { self.provider = provider; self.model = model }
    public static let none = ModelChoice("", "")
}

public enum ModelChooserRules {
    /// `facts`: the chosen model's facts; the levels its provider's list states for it win over the provider's own levels
    public static func effort(_ p: ChatProvider?, facts: ModelFacts? = nil) -> EffortChoice {
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

    /// The current provider first, then every other ready one (official before unofficial, then by name).
    public static func sections(current: String, providers: [ChatProvider]) -> [String] {
        let others = providers.filter { $0.name != current && $0.configured == true }
            .sorted { a, b in
                let ga = ProviderConsentRules.group(a), gb = ProviderConsentRules.group(b)
                return ga != gb ? ga < gb : a.displayName.lowercased() < b.displayName.lowercased()
            }
            .map(\.name)
        return (current.isEmpty ? [] : [current]) + others
    }

    /// The most of the folded model row the model's name may take (the rest: "Model", the provider, the chevron)
    public static let nameShare: CGFloat = 0.6

    /// The model list is one folding row, like the thinking level, so the level and the detailed settings are always in reach
    /// however many models there are. It starts folded; only with no model chosen yet does it start open.
    public static func listStartsOpen(currentModel: String, sections: [String]) -> Bool {
        currentModel.trimmingCharacters(in: .whitespaces).isEmpty && !sections.isEmpty
    }

    /// After a pick: the sheet closes when that provider has no thinking level to pick; otherwise the list folds and the sheet
    /// stays, the level one tap away.
    public static func closeAfterPick(_ effort: EffortChoice) -> Bool {
        if case .levels = effort { return false }
        return true
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

    public enum Fold: Sendable { case list, levels }

    /// A folding row's chevron points right when folded and turns a quarter down when open (animated between the two).
    public static let chevronOpen: Double = 90
    public static func chevronTurn(_ open: Bool) -> Double { open ? chevronOpen : 0 }

    /// The two folding rows open one at a time: opening one folds the other. Returns (model list open, levels open).
    public static func toggle(listOpen: Bool, levelsOpen: Bool, _ which: Fold) -> (list: Bool, levels: Bool) {
        switch which {
        case .list: let l = !listOpen; return (l, levelsOpen && !l)
        case .levels: let v = !levelsOpen; return (listOpen && !v, v)
        }
    }

    /// The models a section lists, with the current model on top when the list lacks it (typed by hand).
    public static func rows(provider: String, list: ChatModelList?, current: ModelChoice) -> [String] {
        let ids = list?.models ?? []
        if current.provider == provider && !current.model.isEmpty && !ids.contains(current.model) { return [current.model] + ids }
        return ids
    }
}

/// The model sheet's rules (Android ModelRules).
public enum ModelSheetRules {
    /// A fallback on the main model's own provider (from before that rule) is shown as empty.
    public static func initialFallback(_ p: Profile) -> ModelChoice {
        !p.fallbackProvider.isEmpty && p.fallbackProvider == p.provider ? .none : ModelChoice(p.fallbackProvider, p.fallbackModel)
    }

    /// Moving the main model onto the fallback's provider empties the fallback.
    public static func afterMainChange(_ main: ModelChoice, _ fallback: ModelChoice) -> (ModelChoice, Bool) {
        !main.provider.isEmpty && main.provider == fallback.provider ? (.none, true) : (fallback, false)
    }

    /// A chosen provider needs a model name.
    public static func complete(_ c: ModelChoice) -> Bool { c.provider.isEmpty || !c.model.trimmingCharacters(in: .whitespaces).isEmpty }
}

/// Which service name the consent text uses for a provider.
public enum ConsentService: Sendable { case claudeSubscription, antigravity, chatgptSubscription, qwen, minimax, other }

/// Unofficial providers (Android ProviderConsent.kt ProviderConsentRules).
public enum ProviderConsentRules {
    public static func needs(_ p: ChatProvider) -> Bool { p.unofficial && (p.consent == nil || p.consent!.version < p.consentVersion) }

    public static func group(_ p: ChatProvider) -> Int { p.unofficial ? 1 : 0 }

    public static func service(_ name: String) -> ConsentService {
        if name.hasPrefix("claude-subscription") { return .claudeSubscription }
        if name.hasPrefix("antigravity") { return .antigravity }
        if name.hasPrefix("openai-codex") { return .chatgptSubscription }
        if name.hasPrefix("qwen") { return .qwen }
        if name.hasPrefix("minimax") { return .minimax }
        return .other
    }

    private static let known = ["anthropic": "이", "google": "이", "openai": "가", "alibaba": "가", "minimax": "가"]

    private static func isHangul(_ c: Character) -> Bool {
        guard let u = c.unicodeScalars.first?.value else { return false }
        return (0xAC00...0xD7A3).contains(u)
    }

    private static func hasFinal(_ c: Character) -> Bool { ((c.unicodeScalars.first!.value - 0xAC00) % 28) != 0 }

    /// The Korean subject particle after a vendor's name, as it is read aloud.
    public static func subjectParticle(_ vendor: String) -> String {
        let v = vendor.trimmingCharacters(in: .whitespaces)
        if let k = known[v.lowercased()] { return k }
        guard let last = v.last else { return "가" }
        if isHangul(last) { return hasFinal(last) ? "이" : "가" }
        return "aeiouy".contains(last.lowercased()) ? "가" : "이"
    }

    /// `word` as it reads inside "Allow …?": an English capitalized "-ing" phrase is lowered ("Allow adding an event?").
    public static func askWord(_ word: String, language: String) -> String {
        guard language == "en" else { return word }
        let first = word.components(separatedBy: " ").first ?? word
        guard first.count >= 4, first.hasSuffix("ing"), let f = first.first, f.isUppercase,
              !first.dropFirst().contains(where: \.isUppercase) else { return word }
        return f.lowercased() + word.dropFirst()
    }

    /// The Korean object particle after `word` (사이트 열기를, 로그인 정보 입력을).
    public static func objectParticle(_ word: String) -> String {
        guard let last = word.trimmingCharacters(in: .whitespaces).last(where: { $0.isLetter || $0.isNumber }) else { return "를" }
        if isHangul(last) { return hasFinal(last) ? "을" : "를" }
        if last.isNumber { return "013678".contains(last) ? "을" : "를" }
        return "aeiouy".contains(last.lowercased()) ? "를" : "을"
    }

    /// "consent_required:<provider>" in a 428 from PUT /api/profile, or nil.
    public static func requiredBy(_ e: ApiError) -> String? {
        guard e.status == 428, let r = e.detail.range(of: "consent_required:") else { return nil }
        let n = e.detail[r.upperBound...].trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? nil : n
    }

    public static func askedKey(_ p: ChatProvider) -> String { "consent.asked.\(p.name).\(p.consentVersion)" }
}

/// One choice of a SelectField (Android SelectOption).
public struct SelectOption: Equatable, Sendable, Identifiable {
    public var value: String
    public var text: String
    public var enabled = true
    public var rank = 1
    public var sortKey: String? = nil
    public var keep = false
    /// 0: the main list; 1: below a divider at the very end (unofficial providers)
    public var group = 0
    public var id: String { value }

    public init(_ value: String, _ text: String, enabled: Bool = true, rank: Int = 1, sortKey: String? = nil, keep: Bool = false, group: Int = 0) {
        self.value = value; self.text = text; self.enabled = enabled; self.rank = rank; self.sortKey = sortKey; self.keep = keep; self.group = group
    }
}

public enum SelectRules {
    public static let menuMax = 10

    public static func useSheet(_ count: Int) -> Bool { count > menuMax }

    public static func arrange(_ options: [SelectOption], query: String, sorted: Bool) -> [SelectOption] {
        let q = query.trimmingCharacters(in: .whitespaces)
        let shown = q.isEmpty ? options : options.filter {
            $0.keep || $0.text.localizedCaseInsensitiveContains(q) || $0.value.localizedCaseInsensitiveContains(q)
        }
        let kept = shown.filter(\.keep)
        let rest = shown.filter { !$0.keep }
        let ordered = !sorted ? rest : rest.enumerated().sorted { a, b in
            if a.element.rank != b.element.rank { return a.element.rank < b.element.rank }
            let ka = (a.element.sortKey ?? a.element.text).lowercased(), kb = (b.element.sortKey ?? b.element.text).lowercased()
            return ka != kb ? ka < kb : a.offset < b.offset
        }.map(\.element)
        if !sorted && !rest.contains(where: { $0.group > 0 }) { return shown }
        // a later group always goes to the very end (stable)
        let grouped = ordered.enumerated().sorted { a, b in a.element.group != b.element.group ? a.element.group < b.element.group : a.offset < b.offset }
            .map(\.element)
        return kept + grouped
    }

    public static func dividerBefore(_ arranged: [SelectOption], _ index: Int) -> Bool {
        index > 0 && !arranged[index].keep && arranged[index].group > arranged[index - 1].group
    }

    public static func nothingFound(_ shown: [SelectOption], query: String) -> Bool {
        !query.trimmingCharacters(in: .whitespaces).isEmpty && !shown.contains { !$0.keep }
    }
}

/// When the phone sign-in shows and what the server's answers mean (Android SignInRules).
public enum SignInRules {
    public enum Problem: Sendable { case consent, already, off, failed }

    /// A provider the app can sign in (the server says `signin`) that is not signed in now. This is the only condition: a server
    /// that does not enable phone sign-in never sends `signin: true`, so nothing shows.
    public static func offered(signin: Bool, signedIn: Bool?) -> Bool { signin && signedIn != true }

    public static func isDeviceCode(_ s: SignInSession) -> Bool { s.kind == "device_code" }

    /// The pasted code (or the address the page ended on) can be sent: one line without spaces.
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

    /// The server's own sentence, without a leading machine word.
    public static func readable(_ e: ApiError) -> String {
        if e.isNetwork { return "" }
        if let r = e.detail.range(of: "already_signed_in: ") { return e.detail[r.upperBound...].trimmingCharacters(in: .whitespaces) }
        return e.detail.trimmingCharacters(in: .whitespaces)
    }

    /// "4:32" for the time left.
    public static func mmss(_ seconds: Int64) -> String { ConnectorRules.mmss(seconds) }
}

/// When the sign-out shows, and what an answer means (Android SignOutRules).
public enum SignOutRules {
    public enum Outcome: Sendable { case done, doneFallbackCleared, doneEnvLeft, mainModel, failed }

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
