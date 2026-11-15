import Foundation
import SwiftUI
import KeloCore
import KeloShared

/// What a model row may say about the model: only what the id's tier words or the server's facts tell.
public enum ModelTrait: Sendable { case fast, reasoning, large, images, long }

public enum ModelTraits {
    public static let longContext: Int64 = 400_000
    private static let fastWords: Set<String> = ["haiku", "flash", "mini", "nano", "lite", "small", "fast", "instant", "turbo"]
    private static let reasoningWords: Set<String> = ["thinking", "reasoner", "reasoning", "r1"]
    private static let largeWords: Set<String> = ["opus", "pro", "ultra", "large"]

    public static func of(_ id: String, facts: ModelEffort?) -> [ModelTrait] {
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

public enum ModelChooserRules {
    /// The current provider first, then every other ready one (official before unofficial, then by name).
    public static func sections(current: String, providers: [ModelProvider]) -> [String] {
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
    public static func rows(provider: String, list: ModelList?, current: ModelChoice) -> [String] {
        let ids = list?.models ?? []
        if current.provider == provider && !current.model.isEmpty && !ids.contains(current.model) { return [current.model] + ids }
        return ids
    }
}

/// The model sheet's rules (Android ModelRules).
