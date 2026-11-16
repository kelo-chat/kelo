import Foundation

/// One choice of a SelectField (Android SelectOption).
public struct SelectOption: Equatable, Sendable, Identifiable {
    public var value: String
    public var label: String
    public var enabled = true
    public var rank = 1
    public var sortKey: String? = nil
    /// Always shown, first, whatever is searched for (the "server default" row)
    public var keep = false
    /// 0: the main list; 1: below a divider at the very end (unofficial providers)
    public var group = 0
    public var id: String { value }

    public init(value: String, label: String, enabled: Bool = true, rank: Int = 1, sortKey: String? = nil, keep: Bool = false, group: Int = 0) {
        self.value = value; self.label = label; self.enabled = enabled; self.rank = rank; self.sortKey = sortKey; self.keep = keep; self.group = group
    }

    public init(_ value: String, _ label: String, enabled: Bool = true, rank: Int = 1, sortKey: String? = nil, keep: Bool = false, group: Int = 0) {
        self.init(value: value, label: label, enabled: enabled, rank: rank, sortKey: sortKey, keep: keep, group: group)
    }
}

public enum SelectRules {
    public static let menuMax = 10

    public static func useSheet(_ count: Int) -> Bool { count > menuMax }

    public static func arrange(_ options: [SelectOption], query: String, sorted: Bool) -> [SelectOption] {
        let q = query.trimmingCharacters(in: .whitespaces)
        let shown = q.isEmpty ? options : options.filter {
            $0.keep || $0.label.localizedCaseInsensitiveContains(q) || $0.value.localizedCaseInsensitiveContains(q)
        }
        let kept = shown.filter(\.keep)
        let rest = shown.filter { !$0.keep }
        let ordered = !sorted ? rest : rest.enumerated().sorted { a, b in
            if a.element.rank != b.element.rank { return a.element.rank < b.element.rank }
            let ka = (a.element.sortKey ?? a.element.label).lowercased(), kb = (b.element.sortKey ?? b.element.label).lowercased()
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
