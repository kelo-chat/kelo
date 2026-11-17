import Foundation

/// One topic Kelo was asked to forget (GET /api/forget items; Android agent/ForgetModels.kt). The words it was found by never leave the
/// server.
public struct ForgottenTopic: Equatable, Sendable, Identifiable {
    public var id: String
    public var label: String
    public var createdAt: Double
    /// What the cleanup removed, per everyday category (memory, notes, reminders, skills, history).
    public var removed: [String: Int]

    public init(id: String, label: String = "", createdAt: Double = 0, removed: [String: Int] = [:]) {
        self.id = id; self.label = label; self.createdAt = createdAt; self.removed = removed
    }

    public static func parse(_ e: JSON) -> ForgottenTopic? {
        guard e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let id = o.str("id").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty else { return nil }
        var removed: [String: Int] = [:]
        for (k, v) in o.obj("removed").object { if let n = v.intValue { removed[k] = n } }
        return ForgottenTopic(id: id, label: o.str("label"), createdAt: o.double("created_at"), removed: removed)
    }

    /// {"items": [...]} or a bare list.
    public static func parseList(_ e: JSON) -> [ForgottenTopic] { (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap(parse) }
}

/// One place the waiting cleanup would delete from: its category and a short preview of the owner's own text.
public struct ForgetPlanItem: Equatable, Sendable {
    public var category: String
    public var show: String
    public init(category: String = "", show: String = "") { self.category = category; self.show = show }
}

/// GET /api/forget/plans/{id}: the list under the forget card.
public struct ForgetPlanView: Equatable, Sendable {
    public var id: String
    public var label: String
    public var items: [ForgetPlanItem]

    public init(id: String = "", label: String = "", items: [ForgetPlanItem] = []) { self.id = id; self.label = label; self.items = items }

    public static func parse(_ e: JSON) -> ForgetPlanView? {
        guard e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let items = (o["items"]?.arrayValue ?? []).compactMap { i -> ForgetPlanItem? in
            guard i.objectValue != nil else { return nil }
            let x = Lenient(i)
            return ForgetPlanItem(category: x.str("category"), show: x.str("show"))
        }
        return ForgetPlanView(id: o.str("id"), label: o.str("label"), items: items)
    }
}

/// What the forgotten-topics page and the forget card decide, away from the screens so it can be tested.
public enum ForgottenRules {
    /// The everyday categories the server names (forget.CATEGORY_LABELS), in the order they are told.
    public static let categories = ["memory", "notes", "reminders", "skills", "history"]

    public static let previewChars = 60

    /// The newest topic first.
    public static func ordered(_ items: [ForgottenTopic]) -> [ForgottenTopic] { items.sorted { $0.createdAt > $1.createdAt } }

    public static func without(_ items: [ForgottenTopic], _ id: String) -> [ForgottenTopic] { items.filter { $0.id != id } }

    /// (category, count) for what was removed: known categories only, in a fixed order, zeros left out.
    public static func parts(_ removed: [String: Int]) -> [(category: String, count: Int)] {
        categories.compactMap { c in removed[c].flatMap { $0 > 0 ? (category: c, count: $0) : nil } }
    }

    /// A preview of at most `previewChars` characters on one line.
    public static func preview(_ show: String) -> String {
        let one = show.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard one.count > previewChars else { return one }
        return String(one.prefix(previewChars - 1)).trimmingCharacters(in: .whitespaces) + "…"
    }

    /// The lines under the forget card: "category · preview", the empty ones dropped.
    public static func lines(_ plan: ForgetPlanView, categoryWord: (String) -> String) -> [String] {
        plan.items.compactMap { i in
            let p = preview(i.show)
            return p.isEmpty ? nil : "\(categoryWord(i.category)) · \(p)"
        }
    }
}

public extension KeloApi {
    /// GET /api/forget: the topics Kelo was asked to forget, newest first.
    func forgottenTopics() async throws -> [ForgottenTopic] { ForgottenRules.ordered(ForgottenTopic.parseList(try await request("GET", "/api/forget"))) }

    /// DELETE /api/forget/{id} "Allow again": done, or already gone (404).
    func allowForgottenAgain(_ id: String) async throws {
        do { _ = try await request("DELETE", "/api/forget/\(KeloApi.segment(id))") }
        catch let e as ApiError where e.status == 404 {}
    }

    /// GET /api/forget/plans/{id}: the waiting plan's list; nil when it is gone or too old (404).
    func forgetPlan(_ id: String) async throws -> ForgetPlanView? {
        do { return ForgetPlanView.parse(try await request("GET", "/api/forget/plans/\(KeloApi.segment(id))")) }
        catch let e as ApiError where e.status == 404 { return nil }
    }
}
