import Foundation
import KeloCore

/// The storage warning, worst first (Android my/StorageRules.kt StorageState): the Kelo server's own disk almost full (chat stops when
/// it fills), 95% of the storage cap, 80%, nothing.
public enum StorageState: String, Sendable, CaseIterable {
    case ok, warn, critical, hostLow = "host_low"
}

/// The storage warning and the cleanup screen's choices, without any UI (Android StorageRules).
public enum StorageRules {
    /// The group ids the server sends, in the order they are shown.
    public static let groups = ["uploads", "images", "browser_history", "results", "backups"]

    public static func state(_ alert: StorageAlert?) -> StorageState {
        guard let a = alert else { return .ok }
        if a.hostLow { return .hostLow }
        switch a.level {
        case "critical": return .critical
        case "warn": return .warn
        default: return .ok
        }
    }

    /// As kept in the My overview; nil = nothing to say.
    public static func key(_ s: StorageState) -> String? { s == .ok ? nil : s.rawValue }
    public static func fromKey(_ k: String?) -> StorageState { k.flatMap { StorageState(rawValue: $0) } ?? .ok }

    /// Red for the states where something stops soon; the warning colour for 80%.
    public static func urgent(_ s: StorageState) -> Bool { s == .critical || s == .hostLow }

    /// Groups with something in them, known ones in `groups` order, then any newer kind the server sends.
    public static func shown(_ list: CleanupList?) -> [CleanupGroup] {
        let gs = (list?.groups ?? []).filter { !$0.items.isEmpty }
        func rank(_ g: CleanupGroup) -> Int { groups.firstIndex(of: g.id) ?? groups.count }
        return gs.enumerated().sorted { (rank($0.element), $0.offset) < (rank($1.element), $1.offset) }.map(\.element)
    }

    public static func toggle(_ selected: Set<String>, _ id: String) -> Set<String> {
        var s = selected
        if s.contains(id) { s.remove(id) } else { s.insert(id) }
        return s
    }

    /// Every item of the group, or none when all of them were already chosen.
    public static func toggleGroup(_ selected: Set<String>, _ g: CleanupGroup) -> Set<String> {
        let ids = Set(g.items.map(\.id))
        return ids.isSubset(of: selected) ? selected.subtracting(ids) : selected.union(ids)
    }

    public static func groupChecked(_ selected: Set<String>, _ g: CleanupGroup) -> Bool {
        !g.items.isEmpty && g.items.allSatisfy { selected.contains($0.id) }
    }

    public static func selectedBytes(_ list: CleanupList?, _ selected: Set<String>) -> Int64 {
        (list?.groups ?? []).flatMap(\.items).filter { selected.contains($0.id) }.reduce(0) { $0 + $1.bytes }
    }

    /// The choices still on a fresh list.
    public static func keep(_ selected: Set<String>, _ list: CleanupList?) -> Set<String> {
        selected.intersection(Set((list?.groups ?? []).flatMap { $0.items.map(\.id) }))
    }
}
