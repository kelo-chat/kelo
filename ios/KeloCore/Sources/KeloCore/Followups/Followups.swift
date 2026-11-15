import Foundation

/*
 * Follow-ups (server: followups.py, FOLLOW-UPS API): when something waits on the user (a login, a stopped job, an approval, an
 * approval that ran out, an answer), the agent writes a chat message about it, at most twice, then the item is on hold.
 *
 * GET  /api/followups?closed=true   {"items": [Followup]} open ones and the last closed ones; each has the chat `message_id` of its
 *                                   latest follow-up and the `actions` the app shows as buttons under that message
 * POST /api/followups/{id}/snooze {"minutes": 60}   POST /api/followups/{id}/drop   POST /api/followups/{id}/retry (approval_expired)
 * resume is the job's own call: POST /api/workers/{worker_id}/resume
 * GET/PUT /api/followups/settings {enabled, first_delay_min, second_delay_min}
 * A server without follow-ups answers 404: the app then shows none of this.
 * (Android followup/FollowupModels.kt, same rules.)
 */

/// One button of a follow-up, as the server sends it. Ids are kept as text, whatever JSON type they came in.
public struct FollowupAction: Equatable, Hashable, Sendable {
    public static let openLogin = "open_login"
    public static let approve = "approve"
    public static let deny = "deny"
    public static let resume = "resume"
    public static let drop = "drop"
    public static let snooze = "snooze"
    /// Start again a run whose approval ran out (kind approval_expired).
    public static let retry = "retry"

    public var kind: String
    public var name: String
    public var approvalId: String
    public var workerId: String
    public var followupId: String
    public var minutes: Int?
    /// The button's words in the app's language, from the server.
    public var label: String

    public init(kind: String, name: String = "", approvalId: String = "", workerId: String = "", followupId: String = "", minutes: Int? = nil, label: String = "") {
        self.kind = kind; self.name = name; self.approvalId = approvalId; self.workerId = workerId; self.followupId = followupId
        self.minutes = minutes; self.label = label
    }

    public static func parse(_ e: JSON?) -> FollowupAction? {
        guard let e, e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let kind = o.text("kind")
        if kind.isEmpty { return nil }
        return FollowupAction(kind: kind, name: o.text("name"), approvalId: o.text("approval_id"), workerId: o.text("worker_id"),
                              followupId: o.text("followup_id"), minutes: o.optInt("minutes"), label: o.text("label"))
    }

    public static func parseList(_ e: JSON?) -> [FollowupAction] { (e?.arrayValue ?? []).compactMap(parse) }
}

/// A thing the agent is asking about again.
public struct Followup: Equatable, Sendable {
    public static let first = "first"
    public static let second = "second"
    public static let onHoldStage = "on_hold"
    public static let approvalExpired = "approval_expired"
    public static let statusOpen = "open"
    public static let statusResolved = "resolved"
    public static let statusDropped = "dropped"

    public var id: String
    /// login | worker | approval | approval_expired | reply
    public var kind: String
    public var title: String
    public var stage: String
    public var nextAt: Double?
    public var createdAt: Double
    public var actions: [FollowupAction]
    /// worker | schedule | background | ""
    public var origin: String
    public var scheduleId: Int64?
    /// The chat message of its latest follow-up: its buttons go under that message.
    public var messageId: Int64?
    /// open | resolved | dropped
    public var status: String

    public init(id: String, kind: String, title: String, stage: String, nextAt: Double?, createdAt: Double, actions: [FollowupAction],
                origin: String = "", scheduleId: Int64? = nil, messageId: Int64? = nil, status: String = Followup.statusOpen) {
        self.id = id; self.kind = kind; self.title = title; self.stage = stage; self.nextAt = nextAt; self.createdAt = createdAt
        self.actions = actions; self.origin = origin; self.scheduleId = scheduleId; self.messageId = messageId; self.status = status
    }

    public var onHold: Bool { stage == Followup.onHoldStage }
    public var open: Bool { status == Followup.statusOpen }

    public static func parse(_ e: JSON?) -> Followup? {
        guard let e, e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let id = o.text("id")
        if id.isEmpty { return nil }
        let status = o.text("status")
        return Followup(id: id, kind: o.text("kind"), title: o.text("title"), stage: o.text("stage"), nextAt: o.optDouble("next_at"),
                        createdAt: (o["began_at"] ?? o["created_at"])?.doubleValue ?? 0, actions: FollowupAction.parseList(o["actions"]),
                        origin: o.text("origin"), scheduleId: Int64(o.text("schedule_id")), messageId: Int64(o.text("message_id")),
                        status: status.isEmpty ? Followup.statusOpen : status)
    }

    /// {"items": [...]} or a bare list.
    public static func parseList(_ e: JSON?) -> [Followup] {
        (e?.arrayValue ?? e?["items"]?.arrayValue ?? []).compactMap(parse)
    }
}

/// GET /api/followups: `available` is false on a server without follow-ups (404).
public struct FollowupList: Equatable, Sendable {
    public var items: [Followup] = []
    public var available = true
    public init(items: [Followup] = [], available: Bool = true) { self.items = items; self.available = available }
}

/// What pressing a follow-up button does.
public enum FollowupStep: Equatable, Hashable, Sendable {
    /// Opens the sign-in sheet for the Kelo keychain item `name` (done on the phone, nothing is sent yet).
    case openLogin(name: String)
    case answer(approvalId: String, approve: Bool)
    /// POST /api/workers/{id}/resume: the job goes on.
    case resumeWorker(workerId: String)
    case drop(followupId: String)
    case snooze(followupId: String, minutes: Int)
    /// Starts the run again whose approval ran out; a fresh approval card follows the usual way.
    case retry(followupId: String)
}

/// How a step went.
public enum StepResult: Equatable, Sendable { case done, gone, failed }

/// The follow-ups' small rules, apart from the UI.
public enum FollowupRules {
    /// Snooze when the server names no time.
    public static let defaultSnoozeMinutes = 60
    /// The most buttons under one message or card.
    public static let maxButtons = 4
    public static let firstChoices = [5, 15, 30, 60, 120, 240]
    public static let secondChoices = [30, 60, 120, 180, 360, 720, 1440]

    /// What an action does, or nil when it lacks what it needs (an approve without an approval, a login without a name) or is a kind
    /// this app does not know. A follow-up's own id stands in when the action names none (`ownId`).
    public static func step(_ a: FollowupAction, ownId: String = "") -> FollowupStep? {
        let fid = a.followupId.isEmpty ? ownId : a.followupId
        func has(_ s: String) -> Bool { !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        switch a.kind {
        case FollowupAction.openLogin: return has(a.name) ? .openLogin(name: a.name) : nil
        case FollowupAction.approve: return has(a.approvalId) ? .answer(approvalId: a.approvalId, approve: true) : nil
        case FollowupAction.deny: return has(a.approvalId) ? .answer(approvalId: a.approvalId, approve: false) : nil
        case FollowupAction.resume: return has(a.workerId) ? .resumeWorker(workerId: a.workerId) : nil
        case FollowupAction.drop: return has(fid) ? .drop(followupId: fid) : nil
        case FollowupAction.retry: return has(fid) ? .retry(followupId: fid) : nil
        case FollowupAction.snooze: return has(fid) ? .snooze(followupId: fid, minutes: max(a.minutes ?? defaultSnoozeMinutes, 1)) : nil
        default: return nil
        }
    }

    /// The buttons to show: the usable actions, each step once, at most `maxButtons`.
    public static func buttons(_ actions: [FollowupAction], ownId: String = "") -> [(action: FollowupAction, step: FollowupStep)] {
        var seen = Set<FollowupStep>()
        var out: [(action: FollowupAction, step: FollowupStep)] = []
        for a in actions {
            guard let s = step(a, ownId: ownId), !seen.contains(s) else { continue }
            seen.insert(s)
            out.append((a, s))
            if out.count == maxButtons { break }
        }
        return out
    }

    /// The buttons of a card in the "needs your answer" list: exactly the actions the server sent.
    public static func holdButtons(_ f: Followup) -> [(action: FollowupAction, step: FollowupStep)] { buttons(f.actions, ownId: f.id) }

    /// Which follow-up each chat message carries: by `message_id`, an open item before a closed one.
    public static func byMessage(_ items: [Followup]) -> [Int64: Followup] {
        var out: [Int64: Followup] = [:]
        // a stable sort: open ones first, otherwise the server's order
        let ordered = items.enumerated().sorted { a, b in
            a.element.open != b.element.open ? a.element.open : a.offset < b.offset
        }.map(\.element)
        for f in ordered { if let id = f.messageId, out[id] == nil { out[id] = f } }
        return out
    }

    /// Follow-ups that go in the "needs your answer" list, oldest first.
    public static func onHold(_ items: [Followup]) -> [Followup] {
        items.enumerated().filter { needsYou($0.element) }
            .sorted { $0.element.createdAt != $1.element.createdAt ? $0.element.createdAt < $1.element.createdAt : $0.offset < $1.offset }
            .map(\.element)
    }

    /// The second ask comes after the first: a second delay shorter than the first is raised to it.
    public static func delays(first: Int, second: Int) -> (first: Int, second: Int) {
        let f = max(first, 1)
        return (f, max(second, f))
    }

    /// The choices of a delay picker, with the current value in them even when it is not one of the usual ones.
    public static func choices(_ usual: [Int], current: Int) -> [Int] {
        Array(Set((usual + [current]).filter { $0 > 0 })).sorted()
    }

    /// The HTTP call of a step that talks to the server (nil for one done on the phone).
    public static func call(_ s: FollowupStep) -> (method: String, path: String, body: JSON?)? {
        switch s {
        case .openLogin: return nil
        case .answer(let id, let approve): return ("POST", "/api/approvals/\(KeloApi.segment(id))", ["approve": .bool(approve)])
        case .resumeWorker(let id): return ("POST", "/api/workers/\(KeloApi.segment(id))/resume", nil)
        case .drop(let id): return ("POST", "/api/followups/\(KeloApi.segment(id))/drop", nil)
        case .snooze(let id, let minutes): return ("POST", "/api/followups/\(KeloApi.segment(id))/snooze", ["minutes": .number(Double(minutes))])
        case .retry(let id): return ("POST", "/api/followups/\(KeloApi.segment(id))/retry", nil)
        }
    }

    /// On hold, or a run whose approval ran out (it waits for "try again").
    public static func needsYou(_ f: Followup) -> Bool { f.open && (f.onHold || f.kind == Followup.approvalExpired) }

    /// 404 / 409 / 410: the thing is already settled (answered elsewhere, expired, dropped).
    public static func gone(_ status: Int) -> Bool { status == 404 || status == 409 || status == 410 }
}

/// Runs follow-up steps against the server; shared by the chat and the work page.
public struct FollowupCalls: Sendable {
    public let api: KeloApi
    public init(api: KeloApi) { self.api = api }

    public func list() async throws -> FollowupList {
        do {
            return FollowupList(items: Followup.parseList(try await api.request("GET", "/api/followups?closed=true")))
        } catch let e as ApiError where e.status == 404 {
            return FollowupList(available: false)
        }
    }

    /// Nil on a server without follow-ups.
    public func settings() async throws -> FollowupSettings? {
        do { return try await api.get("/api/followups/settings") } catch let e as ApiError where e.status == 404 { return nil }
    }

    /// PUT any of enabled, first_delay_min (5..1440), second_delay_min (5..2880).
    public func putSettings(enabled: Bool? = nil, firstMinutes: Int? = nil, secondMinutes: Int? = nil) async throws -> FollowupSettings {
        var body: [String: JSON] = [:]
        if let enabled { body["enabled"] = .bool(enabled) }
        if let firstMinutes { body["first_delay_min"] = .number(Double(firstMinutes)) }
        if let secondMinutes { body["second_delay_min"] = .number(Double(secondMinutes)) }
        return try await api.put("/api/followups/settings", body: .object(body))
    }

    /// Does `s`: done, already settled elsewhere (gone), or failed with the server's reason.
    public func run(_ s: FollowupStep) async throws -> (StepResult, String) {
        guard let c = FollowupRules.call(s) else { return (.done, "") }
        do {
            try await api.request(c.method, c.path, body: c.body)
            return (.done, "")
        } catch let e as ApiError {
            if case .retry = s, e.status == 409 { return (.failed, e.detail) }   // a run that cannot start again says why
            if FollowupRules.gone(e.status) { return (.gone, "") }
            return (.failed, e.isNetwork ? "" : e.detail)
        }
    }
}

extension Lenient {
    /// Text trimmed, "" for missing or null (ids sent as numbers read as text).
    func text(_ k: String) -> String { (self[k]?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
}
