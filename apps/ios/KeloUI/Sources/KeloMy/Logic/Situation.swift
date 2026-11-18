import Foundation
import KeloCore

// "What Kelo looked at" (마이 → 알림): the server's codes as what the screen says, the day grouping and the models
// (GET /api/proactive/research, GET /api/proactive/research/passes/{id}). Android my/SituationPage.kt SituationRules.
// Models ignore fields the server adds later; a code this app does not know falls into a generic line, never shown raw.

/// A thing the look covers now: a section (kind "section", id = its code) or a connected service or phone item.
public struct SituationLooked: Decodable, Equatable, Sendable {
    public var id = "", name = "", kind = ""
    public var hourly = false
    public init(id: String = "", name: String = "", kind: String = "", hourly: Bool = false) {
        self.id = id; self.name = name; self.kind = kind; self.hourly = hourly
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.str("id"); name = j.str("name"); kind = j.str("kind"); hourly = j.bool("hourly")
    }
}

/// What one pass read from one source: state new | changed | same | not_due | off | failed | timeout | skipped.
public struct SituationSource: Decodable, Equatable, Sendable {
    public var id = "", name = "", kind = "", state = ""
    public var entries = 0
    public init(id: String = "", name: String = "", kind: String = "", state: String = "", entries: Int = 0) {
        self.id = id; self.name = name; self.kind = kind; self.state = state; self.entries = entries
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.str("id"); name = j.str("name"); kind = j.str("kind"); state = j.str("state"); entries = j.int("entries")
    }
}

public struct SituationCounts: Decodable, Equatable, Sendable {
    public var proposed = 0, selected = 0, told = 0, dropped = 0
    public init(proposed: Int = 0, selected: Int = 0, told: Int = 0, dropped: Int = 0) {
        self.proposed = proposed; self.selected = selected; self.told = told; self.dropped = dropped
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        proposed = j.int("proposed"); selected = j.int("selected"); told = j.int("told"); dropped = j.int("dropped")
    }
}

/// A pass: status running | no_change | nothing | proposed | selected | delivered | failed | skipped.
public struct SituationPass: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int = 0
    public var at = 0.0
    public var status = ""
    public var counts = SituationCounts()
    public var sources: [SituationSource] = []
    public init(id: Int = 0, at: Double = 0, status: String = "", counts: SituationCounts = SituationCounts(), sources: [SituationSource] = []) {
        self.id = id; self.at = at; self.status = status; self.counts = counts; self.sources = sources
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.int("id"); at = j.double("at"); status = j.str("status")
        counts = (try? JSON.decode(SituationCounts.self, from: j["counts"] ?? .null)) ?? SituationCounts()
        sources = j.list("sources")
    }
}

/// GET /api/proactive/research (one page of passes).
public struct SituationList: Decodable, Equatable, Sendable {
    public var research = false
    public var keepDays = 90
    public var lookedAt: [SituationLooked] = []
    public var passes: [SituationPass] = []
    public var nextBefore: Int?
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        research = j.bool("research"); keepDays = j.int("keep_days", 90)
        lookedAt = j.list("looked_at"); passes = j.list("passes"); nextBefore = j.optInt("next_before")
    }
}

/// One status change of an item in a pass: proposed | updated | selected | told | dropped | expired | waiting.
public struct SituationEvent: Decodable, Equatable, Sendable {
    public var itemId = 0, title = "", status = "", why = "", priority = ""
    public var at = 0.0
    public init(itemId: Int = 0, title: String = "", status: String = "", why: String = "", priority: String = "", at: Double = 0) {
        self.itemId = itemId; self.title = title; self.status = status; self.why = why; self.priority = priority; self.at = at
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        itemId = j.int("item_id"); title = j.str("title"); status = j.str("status"); why = j.str("why")
        priority = j.str("priority"); at = j.double("at")
    }
}

/// GET /api/proactive/research/passes/{id}
public struct SituationDetail: Decodable, Equatable, Sendable {
    public var pass = SituationPass()
    public var sources: [SituationSource] = []
    public var events: [SituationEvent] = []
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        pass = (try? JSON.decode(SituationPass.self, from: j["pass"] ?? .null)) ?? SituationPass()
        sources = j.list("sources"); events = j.list("events")
    }
}

/// The mapping from the server's codes to what the screen says. Pure.
public enum SituationRules {
    /// The sections in the order the look reads them: they always come first.
    public static let sections = ["conversation", "mail", "calendar", "goals", "schedules", "memory"]

    public enum Outcome: Equatable, Sendable { case noChange, nothing, proposed, selected, delivered, running, failed, other }

    public static func outcome(_ status: String) -> Outcome {
        switch status.trimmingCharacters(in: .whitespacesAndNewlines) {
        case "no_change": .noChange
        case "nothing": .nothing
        case "proposed": .proposed
        case "selected": .selected
        case "delivered": .delivered
        case "running": .running
        case "failed", "skipped": .failed
        default: .other
        }
    }

    public enum Bucket: Equatable, Sendable { case changed, same, failed, off, other }

    public static func bucket(_ state: String) -> Bucket {
        switch state.trimmingCharacters(in: .whitespacesAndNewlines) {
        case "new", "changed": .changed
        case "same", "not_due": .same
        case "failed", "timeout", "skipped": .failed
        case "off": .off
        default: .other
        }
    }

    public enum EventKind: Equatable, Sendable { case proposed, updated, selectedToday, selectedRegular, told, dropped, expired, waiting, other }

    public static func eventKind(status: String, priority: String) -> EventKind {
        switch status.trimmingCharacters(in: .whitespacesAndNewlines) {
        case "proposed": .proposed
        case "updated": .updated
        case "selected": priority.trimmingCharacters(in: .whitespacesAndNewlines) == "today" ? .selectedToday : .selectedRegular
        case "told": .told
        case "dropped": .dropped
        case "expired": .expired
        case "waiting": .waiting
        default: .other
        }
    }

    public static func isSection(_ s: SituationSource) -> Bool { s.kind.trimmingCharacters(in: .whitespacesAndNewlines) == "section" }

    /// The sort position of a cover: its section's place, after every section for anything else (the sort is stable).
    public static func sectionRank(_ l: SituationLooked) -> Int {
        guard l.kind.trimmingCharacters(in: .whitespacesAndNewlines) == "section" else { return sections.count }
        let i = sections.firstIndex(of: l.id.trimmingCharacters(in: .whitespacesAndNewlines)) ?? sections.count
        return i
    }

    /// The hourly covers, sections first in their own order, then the rest in the server's order.
    public static func hourly(_ looked: [SituationLooked]) -> [SituationLooked] {
        looked.enumerated().filter { $0.element.hourly }
            .sorted { a, b in
                let ra = sectionRank(a.element), rb = sectionRank(b.element)
                return ra != rb ? ra < rb : a.offset < b.offset
            }
            .map(\.element)
    }

    /// The covers Kelo reads only when a question needs them.
    public static func onDemand(_ looked: [SituationLooked]) -> [SituationLooked] { looked.filter { !$0.hourly } }

    /// The local day of epoch seconds, or nil when there is no time.
    public static func day(_ seconds: Double, zone: TimeZone = .current) -> LocalDay? {
        seconds <= 0 ? nil : LocalDay(seconds: seconds, zone: zone)
    }

    /// The passes by local day, newest day first as the server sends them, each pass in the server's order.
    public static func groupPasses(_ passes: [SituationPass], today: LocalDay, zone: TimeZone = .current) -> [DayGroups.Group<SituationPass>] {
        DayGroups.group(passes, today: today) { day($0.at, zone: zone) }
    }

    /// One item's events in the order they happened, with the item's title (the first non-empty one).
    public struct EventGroup: Equatable, Sendable {
        public let itemId: Int
        public let title: String
        public let events: [SituationEvent]
    }

    public static func groupEvents(_ events: [SituationEvent]) -> [EventGroup] {
        var order: [Int] = []
        var buckets: [Int: [SituationEvent]] = [:]
        for e in events {
            if buckets[e.itemId] == nil { order.append(e.itemId) }
            buckets[e.itemId, default: []].append(e)
        }
        return order.map { id in
            let list = buckets[id] ?? []
            let title = list.first { !$0.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }?.title
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return EventGroup(itemId: id, title: title, events: list)
        }
    }

    /// The sources of a detail by what they show, each in the server's order.
    public static func bucketed(_ sources: [SituationSource]) -> [Bucket: [SituationSource]] {
        var out: [Bucket: [SituationSource]] = [:]
        for s in sources { out[bucket(s.state), default: []].append(s) }
        return out
    }
}
