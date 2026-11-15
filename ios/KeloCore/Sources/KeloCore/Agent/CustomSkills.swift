import Foundation

// Custom skills waiting for the owner (src/assistant/custom_skills.py, "Custom skills" in server.py; Android agent/SkillModels.kt):
// what Kelo proposed or drafted from a finished task, and skills whose files changed outside the app. Nothing here is used by Kelo
// until the owner approves it on the Records page. Decoding is lenient: unknown fields are ignored and missing ones take the
// defaults, so an older or newer server never breaks the page. (Hand-written beside the generated ApiModels.swift, like
// TaskPermission, because that file also carries hand edits a regeneration would drop.)

/// One line of a change: add | del | same | gap (lines left out between two changes) | file (which file the next lines are in).
public struct SkillDiffLine: Codable, Equatable, Hashable, Sendable {
    public var op: String
    public var text: String

    public init(op: String = "same", text: String = "") { self.op = op; self.text = text }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        op = j.str("op", "same"); text = j.str("text")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(op, forKey: AnyKey("op"))
        try c.encode(text, forKey: AnyKey("text"))
    }
}

/// Where a proposal came from: chat | job (Kelo proposed it while working) | draft ("save as skill") | disk (changed outside the app).
public struct SkillOrigin: Codable, Equatable, Hashable, Sendable {
    public var type: String
    public var taskId: Int64?
    public var workerId: String?

    public init(type: String = "chat", taskId: Int64? = nil, workerId: String? = nil) {
        self.type = type; self.taskId = taskId; self.workerId = workerId
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        type = j.str("type", "chat"); taskId = j.optInt64("task_id"); workerId = j.optStr("worker_id")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(type, forKey: AnyKey("type"))
        try c.encodeIfPresent(taskId, forKey: AnyKey("task_id"))
        try c.encodeIfPresent(workerId, forKey: AnyKey("worker_id"))
    }
}

/// GET /api/skills/proposals → items[].
public struct SkillProposal: Codable, Equatable, Hashable, Sendable, Identifiable {
    public static let new = "new"
    public static let change = "change"
    public static let edited = "edited"

    public var id: String
    /// The skill's id (lower-case words joined by '-'); never shown
    public var skill: String
    /// What the app calls it, in the owner's language (an older server sends none: SkillRules.name falls back to the id in words)
    public var name: String
    /// new | change | edited
    public var kind: String
    public var description: String
    /// The full text as it will be saved (Markdown, without the front matter)
    public var text: String
    public var diff: [SkillDiffLine]
    public var reason: String
    public var origin: SkillOrigin
    /// Text from web pages, mail or files reached the run that proposed it
    public var outsideText: Bool
    public var createdAt: Double?
    /// An edited skill: the files the owner looked at (sent back with the approval)
    public var contentHash: String?
    /// An edited skill that was never approved (a folder put there by hand): discarding removes it
    public var neverApproved: Bool
    public var secretsRemoved: Int

    public init(id: String, skill: String = "", name: String = "", kind: String = SkillProposal.new, description: String = "", text: String = "",
                diff: [SkillDiffLine] = [], reason: String = "", origin: SkillOrigin = SkillOrigin(), outsideText: Bool = false,
                createdAt: Double? = nil, contentHash: String? = nil, neverApproved: Bool = false, secretsRemoved: Int = 0) {
        self.id = id; self.skill = skill; self.name = name; self.kind = kind; self.description = description; self.text = text; self.diff = diff
        self.reason = reason; self.origin = origin; self.outsideText = outsideText; self.createdAt = createdAt
        self.contentHash = contentHash; self.neverApproved = neverApproved; self.secretsRemoved = secretsRemoved
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id"))
        skill = j.str("skill"); name = j.str("name"); kind = j.str("kind", SkillProposal.new); description = j.str("description"); text = j.str("text")
        diff = j.list("diff"); reason = j.str("reason"); origin = j.decode("origin") ?? SkillOrigin()
        outsideText = j.bool("outside_text"); createdAt = j.optDouble("created_at"); contentHash = j.optStr("content_hash")
        neverApproved = j.bool("never_approved"); secretsRemoved = j.int("secrets_removed")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(id, forKey: AnyKey("id"))
        try c.encode(skill, forKey: AnyKey("skill"))
        try c.encode(name, forKey: AnyKey("name"))
        try c.encode(kind, forKey: AnyKey("kind"))
        try c.encode(description, forKey: AnyKey("description"))
        try c.encode(text, forKey: AnyKey("text"))
        try c.encode(diff, forKey: AnyKey("diff"))
        try c.encode(reason, forKey: AnyKey("reason"))
        try c.encode(origin, forKey: AnyKey("origin"))
        try c.encode(outsideText, forKey: AnyKey("outside_text"))
        try c.encodeIfPresent(createdAt, forKey: AnyKey("created_at"))
        try c.encodeIfPresent(contentHash, forKey: AnyKey("content_hash"))
        try c.encode(neverApproved, forKey: AnyKey("never_approved"))
        try c.encode(secretsRemoved, forKey: AnyKey("secrets_removed"))
    }
}

/// A draft being written now ("save as skill" on a chat task or a background job).
public struct SkillDrafting: Codable, Equatable, Hashable, Sendable {
    public var taskId: Int64?
    public var workerId: String?

    public init(taskId: Int64? = nil, workerId: String? = nil) { self.taskId = taskId; self.workerId = workerId }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        taskId = j.optInt64("task_id"); workerId = j.optStr("worker_id")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encodeIfPresent(taskId, forKey: AnyKey("task_id"))
        try c.encodeIfPresent(workerId, forKey: AnyKey("worker_id"))
    }
}

/// GET /api/skills/proposals.
public struct SkillProposals: Codable, Equatable, Hashable, Sendable {
    public var items: [SkillProposal]
    public var drafting: [SkillDrafting]
    /// False when the server has no custom skills (an older server answers 404)
    public var available: Bool

    public init(items: [SkillProposal] = [], drafting: [SkillDrafting] = [], available: Bool = true) {
        self.items = items; self.drafting = drafting; self.available = available
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = j.list("items"); drafting = j.list("drafting"); available = j.bool("available", true)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(items, forKey: AnyKey("items"))
        try c.encode(drafting, forKey: AnyKey("drafting"))
        try c.encode(available, forKey: AnyKey("available"))
    }
}

/// The small rules of the skills awaiting approval and of "Save as skill", apart from the UI (Android SkillRules).
public enum SkillRules {
    /// Lines of text or of a change shown before "Show all".
    public static let previewLines = 12

    /// How long "approved" / "discarded" stays on a card before it leaves the list.
    public static let doneSeconds = 2.5

    /// The name to show: the server's name for it, else its id in words.
    public static func name(_ p: SkillProposal) -> String {
        let n = p.name.trimmingCharacters(in: .whitespacesAndNewlines)
        return n.isEmpty ? title(p.skill) : n
    }

    /// A skill's name to show: its id in words ("renew-library-book" → "Renew library book"); never the raw id.
    public static func title(_ skill: String) -> String {
        let words = skill.split(whereSeparator: { $0 == "-" || $0 == "_" })
            .map(String.init).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }.joined(separator: " ")
        guard let first = words.first else { return skill }
        return first.uppercased() + words.dropFirst()
    }

    /// Changes made outside the app first (Kelo is not using those skills now), then the newest proposals; entries without an id
    /// or a skill are left out.
    public static func ordered(_ items: [SkillProposal]) -> [SkillProposal] {
        items.filter { !$0.id.trimmingCharacters(in: .whitespaces).isEmpty && !$0.skill.trimmingCharacters(in: .whitespaces).isEmpty }
            .enumerated()
            .sorted { a, b in
                let ea = a.element.kind == SkillProposal.edited ? 0 : 1, eb = b.element.kind == SkillProposal.edited ? 0 : 1
                if ea != eb { return ea < eb }
                let ta = a.element.createdAt ?? 0, tb = b.element.createdAt ?? 0
                if ta != tb { return ta > tb }
                return a.offset < b.offset
            }
            .map(\.element)
    }

    /// The list without one entry (once answered).
    public static func without(_ p: SkillProposals, id: String) -> SkillProposals {
        var c = p
        c.items.removeAll { $0.id == id }
        return c
    }

    /// A change is shown as its lines; a new skill as its full text.
    public static func showsDiff(_ p: SkillProposal) -> Bool { p.kind != SkillProposal.new && !p.diff.isEmpty }

    /// (added, removed) line counts of a change.
    public static func counts(_ p: SkillProposal) -> (added: Int, removed: Int) {
        (p.diff.filter { $0.op == "add" }.count, p.diff.filter { $0.op == "del" }.count)
    }

    /// The lines shown: all of them when `all`, else the first `previewLines`.
    public static func preview<T>(_ lines: [T], all: Bool) -> [T] { all ? lines : Array(lines.prefix(previewLines)) }

    /// Whether there is more than the preview.
    public static func hasMore<T>(_ lines: [T]) -> Bool { lines.count > previewLines }

    /// The text split into lines for the preview (blank lines kept, trailing ones dropped).
    public static func textLines(_ text: String) -> [String] {
        var t = Substring(text)
        while let last = t.last, last.isWhitespace { t = t.dropLast() }
        return t.split(separator: "\n", omittingEmptySubsequences: false).map { $0.hasSuffix("\r") ? String($0.dropLast()) : String($0) }
    }

    /// Whether "Save as skill" shows on a finished chat task or background job: only when it ended well.
    public static func canSave(_ status: String) -> Bool { status == "done" }

    /// Whether a draft from this task or job is being written now. A job's draft (worker id set) shows on the job's page only.
    public static func drafting(_ p: SkillProposals?, taskId: Int64? = nil, workerId: String? = nil) -> Bool {
        p?.drafting.contains { d in
            (taskId != nil && d.taskId == taskId && d.workerId == nil) || (workerId != nil && d.workerId == workerId)
        } ?? false
    }

    public static func approvePath(_ p: SkillProposal) -> String { "/api/skills/proposals/\(KeloApi.segment(p.id))/approve" }
    public static func discardPath(_ p: SkillProposal) -> String { "/api/skills/proposals/\(KeloApi.segment(p.id))/discard" }

    /// The approve request's body: an edited skill is approved only as the owner saw it (its hash goes along).
    public static func approveBody(_ p: SkillProposal) -> JSON? { p.contentHash.map { ["expected_hash": .string($0)] } }

    public static func draftPath(taskId: Int64? = nil, workerId: String? = nil) -> String {
        if let w = workerId { return "/api/workers/\(KeloApi.segment(w))/skill-draft" }
        return "/api/tasks/\(taskId.map(String.init) ?? "")/skill-draft"
    }
}

public extension KeloApi {
    /// GET /api/skills/proposals; an older server without custom skills (404) gives `available: false`.
    func skillProposals() async throws -> SkillProposals {
        do { return try await get("/api/skills/proposals") }
        catch let e as ApiError where e.status == 404 { return SkillProposals(available: false) }
    }

    /// Approve (written exactly as shown and used from now on) or discard a proposal.
    func answerSkill(_ p: SkillProposal, approve: Bool) async throws {
        if approve {
            try await request("POST", SkillRules.approvePath(p), body: SkillRules.approveBody(p))
        } else {
            try await request("POST", SkillRules.discardPath(p))
        }
    }

    /// "Save as skill": Kelo drafts a skill from a finished chat task or background job (202). 409 already drafting or still running,
    /// 404 unknown (or an older server), 503 no model.
    func draftSkill(taskId: Int64? = nil, workerId: String? = nil) async throws {
        try await request("POST", SkillRules.draftPath(taskId: taskId, workerId: workerId))
    }
}
