import Foundation
import KeloCore

/// The thinking level on My → Model, as the chat's model sheet offers it (KeloChat ModelChooserRules.effort; Android
/// my/ModelPage.kt uses chat/ModelChooserRules.kt itself). KeloMy does not depend on KeloChat, so the rule is restated here.
public enum ModelEffortChoice: Equatable, Sendable {
    /// "" first: the model's own default (nothing is sent)
    case levels([String])
    /// No level to pick, with the server's sentence why (agy's model names, a custom server that refused it)
    case note(String)
    case none
}

public enum ModelEffortRules {
    /// `facts`: what the model list says about the chosen model; its levels win over the provider's own
    public static func effort(_ p: MyProvider?, facts: ModelEffort?) -> ModelEffortChoice {
        guard let p else { return .none }
        if let listed = facts?.effortLevels {
            if !listed.isEmpty { return .levels([""] + listed) }
            if let n = facts?.effortNote, !n.trimmingCharacters(in: .whitespaces).isEmpty { return .note(n) }
            return .none
        }
        if !p.effortLevels.isEmpty { return .levels([""] + p.effortLevels) }
        if let n = p.effortNote, !n.trimmingCharacters(in: .whitespaces).isEmpty { return .note(n) }
        return .none
    }

    private static let order = ["low", "medium", "high", "xhigh", "max"]

    /// The level to show as chosen: `effort` when offered, else the highest one below it (what the server sends); "" = the default
    public static func shownLevel(_ levels: [String], _ effort: String) -> String {
        if levels.contains(effort) { return effort }
        guard let at = order.firstIndex(of: effort) else { return "" }
        let lower = levels.filter { !$0.isEmpty && (order.firstIndex(of: $0).map { $0 <= at } ?? false) }
        return lower.last ?? levels.first { !$0.isEmpty } ?? ""
    }
}
