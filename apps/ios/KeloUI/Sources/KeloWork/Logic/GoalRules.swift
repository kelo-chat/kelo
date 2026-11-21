import Foundation
import SwiftUI
import KeloCore

// 다짐 (goals): the models the goal screens need (Android goals/GoalModels.kt + core/Models.kt Goal) and their small rules
// (GoalMeta.kt, GoalDetailRules). KeloCore's Goal has only the title, tag, note and status; the list and page also show the plan,
// the next step and whether Kelo is asking, so they are decoded here.

/// The plan's steps: how many are done of how many (0/0: no plan yet).
struct GoalPlan: Codable, Equatable, Hashable, Sendable {
    var done = 0
    var total = 0
}

/// GET /api/goals items.
struct GoalItem: Codable, Equatable, Hashable, Identifiable, Sendable {
    var id: Int64
    var title: String
    var category = ""
    var note = ""
    var status = "tracking"
    /// user | suggestion | agent
    var origin = "user"
    var plan = GoalPlan()
    var nextStep: String?
    /// Kelo asked for this goal and waits for the answer (the server says false while it works on it)
    var asking = false
    /// A reply or job on this goal is running now (an older server never says so)
    var working = false
    /// Linked schedules that are on: deleting the goal first asks whether to turn them off too (an older server says 0)
    var schedulesOn = 0
    /// goal | work: a work item is something Kelo carries for the user (listed with include=work)
    var kind = "goal"
    /// The top-level goal this smaller goal sits under (one level only); nil at the top
    var parentId: Int64?
    /// One line: where the goal stands now (empty on an older server)
    var summary = ""
    /// Two or three sentences Kelo wrote about the goal (the note stays the user's own words)
    var description = ""
    /// wellbeing | people | finances | job | hobbies | workload | other; empty when none is set
    var area = ""
    /// The reason given at the last status change
    var doneReason = ""
    /// How many smaller goals sit under this one
    var children = 0

    static let statusTracking = "tracking"
    static let statusDone = "done"
    var isDone: Bool { status == GoalItem.statusDone }
    var isWork: Bool { kind == "work" }

    init(id: Int64, title: String, category: String = "", note: String = "", status: String = "tracking", origin: String = "user",
         plan: GoalPlan = GoalPlan(), nextStep: String? = nil, asking: Bool = false, working: Bool = false, schedulesOn: Int = 0,
         kind: String = "goal", parentId: Int64? = nil, summary: String = "", description: String = "", area: String = "",
         doneReason: String = "", children: Int = 0) {
        self.id = id; self.title = title; self.category = category; self.note = note; self.status = status; self.origin = origin
        self.plan = plan; self.nextStep = nextStep; self.asking = asking; self.working = working; self.schedulesOn = schedulesOn
        self.kind = kind; self.parentId = parentId; self.summary = summary; self.description = description; self.area = area
        self.doneReason = doneReason; self.children = children
    }

    init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optInt64("id"))
        title = j.str("title")
        category = j.str("category")
        note = j.str("note")
        status = j.str("status", GoalItem.statusTracking)
        origin = j.str("origin", "user")
        let p = j.obj("plan")
        plan = GoalPlan(done: p.int("done"), total: p.int("total"))
        nextStep = j.optStr("next_step").flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
        asking = j.bool("asking")
        working = j.bool("working")
        schedulesOn = max(0, j.int("schedules_on"))
        kind = j.str("kind", "goal")
        parentId = j.optInt64("parent_id")
        summary = j.str("summary")
        description = j.str("description")
        area = j.str("area")
        doneReason = j.str("done_reason")
        children = max(0, j.int("children"))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(id, forKey: AnyKey("id"))
        try c.encode(title, forKey: AnyKey("title"))
        try c.encode(category, forKey: AnyKey("category"))
        try c.encode(note, forKey: AnyKey("note"))
        try c.encode(status, forKey: AnyKey("status"))
        try c.encode(origin, forKey: AnyKey("origin"))
        try c.encode(["done": plan.done, "total": plan.total], forKey: AnyKey("plan"))
        try c.encodeIfPresent(nextStep, forKey: AnyKey("next_step"))
        try c.encode(asking, forKey: AnyKey("asking"))
        try c.encode(working, forKey: AnyKey("working"))
        try c.encode(schedulesOn, forKey: AnyKey("schedules_on"))
        try c.encode(kind, forKey: AnyKey("kind"))
        try c.encodeIfPresent(parentId, forKey: AnyKey("parent_id"))
        try c.encode(summary, forKey: AnyKey("summary"))
        try c.encode(description, forKey: AnyKey("description"))
        try c.encode(area, forKey: AnyKey("area"))
        try c.encode(doneReason, forKey: AnyKey("done_reason"))
        try c.encode(children, forKey: AnyKey("children"))
    }
}

struct GoalFact: Equatable, Sendable { var key: String; var value: String; var skipped: Bool }

struct GoalStep: Equatable, Identifiable, Sendable {
    var id: Int64
    var title: String
    var detail = ""
    /// todo | doing | done
    var status = "todo"
    /// user | agent
    var owner = "user"
    /// YYYY-MM-DD
    var due: String?
    var isDone: Bool { status == "done" }
    var mine: Bool { owner != "agent" }
}

struct GoalMetricValue: Equatable, Sendable { var value: Double; var note = ""; var at = 0.0 }

struct GoalMetric: Equatable, Identifiable, Sendable {
    var id: Int64
    var name: String
    var unit = ""
    var start: Double?
    var target: Double?
    var values: [GoalMetricValue] = []
}

struct GoalRef: Equatable, Sendable { var id: Int64; var title: String }

struct GoalLogEntry: Equatable, Identifiable, Sendable {
    var id: Int64
    var at: Double
    var text: String
    /// A few words: what happened (empty on older entries)
    var title = ""
    /// One line: the main result
    var outcome = ""
    /// The day the entry is about, when it is not the day it was written
    var happenedAt: Double?
    /// The smaller goal the entry was written on (it shows on the goal above too)
    var fromGoal: GoalRef?
}
struct GoalDocument: Equatable, Identifiable, Sendable { var id: Int64; var path: String; var title: String; var exists: Bool }
struct GoalScheduleBrief: Equatable, Identifiable, Sendable { var id: Int64; var name: String; var description: String; var enabled: Bool }

/// GET /api/goals/{id}: one goal with everything built around it.
struct GoalWorkspace: Decodable, Equatable, Sendable {
    var goal: GoalItem
    var facts: [GoalFact] = []
    var steps: [GoalStep] = []
    var metrics: [GoalMetric] = []
    var log: [GoalLogEntry] = []
    /// The offset of the next page of `log` (GET /api/goals/{id}/log), or nil when `log` has every entry
    var logNext: Int?
    var documents: [GoalDocument] = []
    var schedules: [GoalScheduleBrief] = []
    /// The smaller goals under this one
    var children: [GoalItem] = []

    init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        guard let g = j.decode("goal", as: GoalItem.self) else { throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "goal")) }
        goal = g
        func list(_ k: String) -> [Lenient] { (j.has(k) ? (Lenient.items(j, k)) : []) }
        facts = list("facts").compactMap { o in
            let key = o.str("key"); return key.isEmpty ? nil : GoalFact(key: key, value: o.str("value"), skipped: o.bool("skipped"))
        }
        steps = list("steps").compactMap { o in
            guard let id = o.optInt64("id") else { return nil }
            return GoalStep(id: id, title: o.str("title"), detail: o.str("detail"), status: o.str("status", "todo"), owner: o.str("owner", "user"),
                            due: o.optStr("due"))
        }
        metrics = list("metrics").compactMap { o in
            guard let id = o.optInt64("id") else { return nil }
            let values = Lenient.items(o, "values").compactMap { v -> GoalMetricValue? in
                guard let n = v.optDouble("value") else { return nil }
                return GoalMetricValue(value: n, note: v.str("note"), at: v.double("at"))
            }
            return GoalMetric(id: id, name: o.str("name"), unit: o.str("unit"), start: o.optDouble("start"), target: o.optDouble("target"), values: values)
        }
        children = j.list("children", of: GoalItem.self)
        log = list("log").compactMap(GoalLogEntry.parse)
        logNext = j.optInt64("log_next").map { Int($0) }
        documents = list("documents").compactMap { o in
            guard let id = o.optInt64("id") else { return nil }
            return GoalDocument(id: id, path: o.str("path"), title: o.str("title"), exists: o.bool("exists", true))
        }
        schedules = list("schedules").compactMap { o in
            guard let id = o.optInt64("id") else { return nil }
            return GoalScheduleBrief(id: id, name: o.str("name"), description: o.str("description"), enabled: o.bool("enabled", true))
        }
    }
}

/// GET /api/goals/{id}/log?offset=: the next entries; `next` is the offset of the page after, or nil at the end.
struct GoalLogPage: Decodable, Equatable, Sendable {
    var items: [GoalLogEntry] = []
    var next: Int?

    init(items: [GoalLogEntry], next: Int?) { self.items = items; self.next = next }

    init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = Lenient.items(j, "items").compactMap(GoalLogEntry.parse)
        next = j.optInt64("next").map { Int($0) }
    }
}

extension GoalLogEntry {
    /// One log entry as the server sends it; nil without an id.
    static func parse(_ o: Lenient) -> GoalLogEntry? {
        guard let id = o.optInt64("id") else { return nil }
        let from = o.obj("from_goal")
        let ref = from.optInt64("id").map { GoalRef(id: $0, title: from.str("title")) }
        return GoalLogEntry(id: id, at: o.double("at"), text: o.str("text"), title: o.str("title"), outcome: o.str("outcome"),
                            happenedAt: o.optDouble("happened_at"), fromGoal: ref)
    }
}

extension Lenient {
    /// The objects of the list at `key`.
    static func items(_ j: Lenient, _ key: String) -> [Lenient] {
        guard let v: [JSON] = j.decode(key, as: [JSON].self) else { return [] }
        return v.map { Lenient($0) }
    }
}

/// GET /api/goals/suggestions items: goals Kelo found in the conversations.
struct GoalSuggestion: Decodable, Equatable, Identifiable, Sendable {
    var id: Int64
    var title: String
    var reason = ""
    var evidence = ""
    var source = "nightly"

    init(id: Int64, title: String, reason: String = "") { self.id = id; self.title = title; self.reason = reason }

    init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optInt64("id"))
        title = j.str("title")
        reason = j.str("reason")
        evidence = j.str("evidence")
        source = j.str("source", "nightly")
    }
}

struct GoalSuggestions: Decodable, Sendable {
    var items: [GoalSuggestion] = []
    init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = j.list("items", of: GoalSuggestion.self)
    }
}

/// A goal's optional due date and progress, kept on the last line of its note: "[due:2026-11-20 progress:40]" (Android GoalMeta).
struct GoalMeta: Equatable, Sendable {
    var note: String
    var due: LocalDay? = nil
    var progress: Int? = nil
}

enum GoalMetaCodec {
    private static let pattern = #"\n?\[due:([0-9-]*)(?: progress:(\d{1,3}))?\]\s*$|\n?\[progress:(\d{1,3})\]\s*$"#

    static func parse(_ note: String) -> GoalMeta {
        guard let re = try? NSRegularExpression(pattern: pattern),
              let m = re.firstMatch(in: note, range: NSRange(note.startIndex..., in: note)) else {
            return GoalMeta(note: note.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        func group(_ i: Int) -> String {
            guard let r = Range(m.range(at: i), in: note) else { return "" }
            return String(note[r])
        }
        let due = group(1).isEmpty ? nil : LocalDay(parsing: group(1))
        let p = group(2).isEmpty ? group(3) : group(2)
        let progress = Int(p).map { min(max($0, 0), 100) }
        let head = Range(m.range, in: note).map { String(note[..<$0.lowerBound]) } ?? note
        return GoalMeta(note: head.trimmingCharacters(in: .whitespacesAndNewlines), due: due, progress: progress)
    }

    static func encode(_ meta: GoalMeta) -> String {
        let text = meta.note.trimmingCharacters(in: .whitespacesAndNewlines)
        var parts: [String] = []
        if let d = meta.due { parts.append("due:\(d)") }
        if let p = meta.progress { parts.append("progress:\(min(max(p, 0), 100))") }
        if parts.isEmpty { return text }
        let line = "[" + parts.joined(separator: " ") + "]"
        return text.isEmpty ? line : text + "\n" + line
    }
}

enum GoalsRules {
    /// In progress: the nearest due date first, then those without one (in the server's order).
    static func ordered(_ goals: [GoalItem]) -> [GoalItem] {
        let far = LocalDay(year: 9999, month: 12, day: 31)
        return goals.enumerated().sorted { a, b in
            let da = GoalMetaCodec.parse(a.element.note).due ?? far
            let db = GoalMetaCodec.parse(b.element.note).due ?? far
            return da != db ? da < db : a.offset < b.offset
        }.map(\.element)
    }

    static func overdue(_ due: LocalDay?, today: LocalDay) -> Bool { due.map { $0 < today } ?? false }

    /// The user's goals with their smaller goals under them, in the list's order; a smaller goal whose parent is not in the list shows at the top.
    static func grouped(_ goals: [GoalItem]) -> [(goal: GoalItem, children: [GoalItem])] {
        let own = goals.filter { !$0.isWork }
        let ids = Set(own.map(\.id))
        let tops = own.filter { $0.parentId == nil || !ids.contains($0.parentId!) }
        return ordered(tops).map { g in (g, ordered(own.filter { $0.parentId == g.id })) }
    }

    /// The open work items Kelo carries, in the server's order (newest first).
    static func work(_ goals: [GoalItem]) -> [GoalItem] { goals.filter { $0.isWork && !$0.isDone } }

    /// The day a log entry is about: the day it happened, else the day it was written.
    static func logDay(_ e: GoalLogEntry) -> Double { e.happenedAt ?? e.at }

    /// How many smaller goals a delete takes with it (the list asks first), or nil when there are none.
    static func deleteNotice(_ g: GoalItem) -> Int? { g.children > 0 ? g.children : nil }

    /// The free-form tag as typed, trimmed.
    static func tag(_ text: String) -> String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// The slider's value snapped to tens.
    static func snapProgress(_ v: Double) -> Int { min(max(Int((v / 10).rounded()) * 10, 0), 100) }

    /// The goal's live state on its row and page: Kelo works on it, or waits for the user's answer (only while nothing runs).
    static func activity(_ g: GoalItem) -> GoalActivity? {
        if g.isDone { return nil }
        if g.working { return .working }
        return g.asking ? .asking : nil
    }

    /// How soon the goals are fetched again while on screen: soon while Kelo works on one, less often while one waits for an answer,
    /// else only when the screen comes back (Android GoalsRules.refreshMs).
    static func refreshMs(_ goals: [GoalItem]) -> Int? {
        if goals.contains(where: { activity($0) == .working }) { return workingRefreshMs }
        if goals.contains(where: { activity($0) == .asking }) { return askingRefreshMs }
        return nil
    }

    /// Deleting a goal with schedules on asks first whether to turn them off too; without any, no question.
    static func asksAboutSchedules(_ g: GoalItem) -> Bool { g.schedulesOn > 0 }

    /// DELETE /api/goals/{id}?schedules=delete|keep: what the user chose (keep when nothing was asked, as older apps do).
    static func deletePath(_ id: Int64, deleteSchedules: Bool) -> String {
        "/api/goals/\(id)?schedules=\(deleteSchedules ? "delete" : "keep")"
    }

    static let workingRefreshMs = 3_000
    static let askingRefreshMs = 15_000
}

enum GoalActivity: Equatable, Sendable { case working, asking }

extension View {
    /// While the view shows, runs `load` every `ms` milliseconds (nil: never), so "Kelo is working" / "waiting for you" follow the server.
    func goalRefresh(every ms: Int?, _ load: @escaping @MainActor () -> Void) -> some View {
        task(id: ms) {
            guard let ms else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(ms) * 1_000_000)
                if Task.isCancelled { break }
                await MainActor.run { load() }
            }
        }
    }
}

/// The goal list's state (Android GoalsState).
struct GoalsState: Equatable, Sendable {
    var goals: [GoalItem] = []
    var loaded = false
    var failed = false
    var offline = false
    var removing: Set<Int64> = []
    /// Goals being removed whose linked schedules the user chose to turn off with them
    var pausing: Set<Int64> = []
    var suggestions: [GoalSuggestion] = []
    var settling: Set<Int64> = []
    /// The one list of what Kelo carries (GET /api/carried), and whether the server answered it (an older one does not: the work
    /// section then shows its own work items)
    var carried: [CarriedItem] = []
    var carriedKnown = false

    var shownSuggestions: [GoalSuggestion] { suggestions.filter { !settling.contains($0.id) } }
    var tracking: [GoalItem] { GoalsRules.ordered(goals.filter { !$0.isDone && !$0.isWork && !removing.contains($0.id) }) }
    /// The open work items Kelo carries for the user
    var work: [GoalItem] { GoalsRules.work(goals.filter { !removing.contains($0.id) }) }
    var done: [GoalItem] { goals.filter { $0.isDone && !removing.contains($0.id) } }
}

enum GoalDetailRules {
    /// A number as people write it: 72, 72.5 (never 72.0 or 1.0E3).
    static func number(_ v: Double?) -> String {
        guard let v else { return "-" }
        if v == v.rounded() && abs(v) < 1e12 { return String(Int64(v)) }
        var s = String(format: "%.2f", v)
        while s.hasSuffix("0") { s.removeLast() }
        if s.hasSuffix(".") { s.removeLast() }
        return s
    }

    static func withUnit(_ v: Double?, _ unit: String) -> String {
        unit.trimmingCharacters(in: .whitespaces).isEmpty || v == nil ? number(v) : number(v) + " " + unit
    }

    /// The workspace with the next page of entries added; an entry already shown is not added twice.
    static func withMoreLog(_ w: GoalWorkspace, _ page: GoalLogPage) -> GoalWorkspace {
        var out = w
        let seen = Set(w.log.map(\.id))
        out.log += page.items.filter { !seen.contains($0.id) }
        out.logNext = page.next
        return out
    }

    /// A fresh first page keeps the older entries already loaded, so a refresh does not fold the list back up.
    static func refreshed(prev: GoalWorkspace?, fresh: GoalWorkspace) -> GoalWorkspace {
        guard let prev, prev.log.count > fresh.log.count else { return fresh }
        var out = fresh
        let seen = Set(fresh.log.map(\.id))
        out.log += prev.log.filter { !seen.contains($0.id) }
        out.logNext = prev.logNext
        return out
    }

    /// More entries can be asked for: there is a next page and no request for one is running.
    static func canLoadMore(workspace: GoalWorkspace?, loading: Bool) -> Bool { workspace?.logNext != nil && !loading }

    static func canTick(_ s: GoalStep) -> Bool { s.mine }
    static func toggled(_ s: GoalStep) -> String { s.isDone ? "todo" : "done" }

    /// Points of a small chart, 0...1 on both axes (oldest first); the target, when known, is part of the range.
    static func chart(_ m: GoalMetric) -> [(Double, Double)] {
        let vs = m.values.map(\.value)
        guard !vs.isEmpty else { return [] }
        let all = vs + (m.target.map { [$0] } ?? [])
        let lo = all.min()!, hi = all.max()!
        let span = hi - lo > 0 ? hi - lo : 1
        return vs.enumerated().map { i, v in (vs.count == 1 ? 0.5 : Double(i) / Double(vs.count - 1), (v - lo) / span) }
    }

    static func targetLevel(_ m: GoalMetric) -> Double? {
        guard let t = m.target else { return nil }
        let vs = m.values.map(\.value)
        guard !vs.isEmpty else { return nil }
        let all = vs + [t]
        let span = all.max()! - all.min()!
        guard span > 0 else { return 0.5 }
        return (t - all.min()!) / span
    }
}
