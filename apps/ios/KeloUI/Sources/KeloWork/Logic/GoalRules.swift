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

    static let statusTracking = "tracking"
    static let statusDone = "done"
    var isDone: Bool { status == GoalItem.statusDone }

    init(id: Int64, title: String, category: String = "", note: String = "", status: String = "tracking", origin: String = "user",
         plan: GoalPlan = GoalPlan(), nextStep: String? = nil, asking: Bool = false, working: Bool = false, schedulesOn: Int = 0) {
        self.id = id; self.title = title; self.category = category; self.note = note; self.status = status; self.origin = origin
        self.plan = plan; self.nextStep = nextStep; self.asking = asking; self.working = working; self.schedulesOn = schedulesOn
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

struct GoalLogEntry: Equatable, Identifiable, Sendable { var id: Int64; var at: Double; var text: String }
struct GoalDocument: Equatable, Identifiable, Sendable { var id: Int64; var path: String; var title: String; var exists: Bool }
struct GoalScheduleBrief: Equatable, Identifiable, Sendable { var id: Int64; var name: String; var description: String; var enabled: Bool }

/// GET /api/goals/{id}: one goal with everything built around it.
struct GoalWorkspace: Decodable, Equatable, Sendable {
    var goal: GoalItem
    var facts: [GoalFact] = []
    var steps: [GoalStep] = []
    var metrics: [GoalMetric] = []
    var log: [GoalLogEntry] = []
    var documents: [GoalDocument] = []
    var schedules: [GoalScheduleBrief] = []

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
        log = list("log").compactMap { o in
            guard let id = o.optInt64("id") else { return nil }
            return GoalLogEntry(id: id, at: o.double("at"), text: o.str("text"))
        }
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

    /// The free-form tag as the server takes it (at most 40 characters).
    static func tag(_ text: String) -> String { String(text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(40)) }

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

    var shownSuggestions: [GoalSuggestion] { suggestions.filter { !settling.contains($0.id) } }
    var tracking: [GoalItem] { GoalsRules.ordered(goals.filter { !$0.isDone && !removing.contains($0.id) }) }
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
