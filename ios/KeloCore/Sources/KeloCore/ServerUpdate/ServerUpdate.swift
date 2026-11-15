import Foundation

// The owner updating the Kelo server itself from the app (Android my/ServerUpdate.kt). The server hands the work to its updater
// container: it pulls the new signed images, makes a backup, restarts, checks the new version's health and goes back by itself when
// that check fails.
//
//   GET  /api/server-update                  -> ServerUpdateStatus
//   POST /api/server-update/check   {}       -> ServerUpdateStatus (asks the release list again)
//   POST /api/server-update/update  {}       -> 202 ServerUpdateStatus (job.state "running"); 409 {"detail"} when it cannot start
//   POST /api/server-update/rollback {}      -> 202 ServerUpdateStatus (job.action "rollback"); 409 {"detail"} without a rollback point
//   PUT  /api/server-update/settings {"auto_update": bool} -> ServerUpdateStatus
//
// Only the owner's device sees any of it: 403 (another device), 404 (a server from before this feature) and state "unavailable"
// all mean "show nothing". While an update runs the server restarts, so GET fails for a few minutes: the app keeps asking every
// 2 s through those failures and gives up only after 15 minutes.

/// The newest published release.
public struct ServerUpdateRelease: Codable, Equatable, Hashable, Sendable {
    public var version: String
    /// ISO 8601 ("2026-11-01T00:00:00Z"), "" when unknown
    public var date: String
    /// release notes, plain text with line breaks
    public var notes: String

    public init(version: String = "", date: String = "", notes: String = "") {
        self.version = version; self.date = date; self.notes = notes
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        version = j.str("version")
        date = j.str("date")
        notes = j.str("notes")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(version, forKey: AnyKey("version"))
        try c.encode(date, forKey: AnyKey("date"))
        try c.encode(notes, forKey: AnyKey("notes"))
    }

    /// The release date as seconds since 1970, nil when missing or unreadable.
    public var dateSeconds: Double? { ServerUpdateRules.parseDate(date) }
}

/// One update or rollback run, as the updater reports it.
public struct ServerUpdateJob: Codable, Equatable, Hashable, Sendable {
    public static let running = "running", done = "done", failed = "failed", rolledBack = "rolled_back", rollbackFailed = "rollback_failed"
    public static let actionUpdate = "update", actionRollback = "rollback"

    public var id: String
    /// "update" | "rollback"
    public var action: String
    /// started by the nightly auto-update
    public var auto: Bool
    /// running | done | failed | rolled_back | rollback_failed
    public var state: String
    /// preflight | download | verify | backup | restart | health | rollback | self_update | done
    public var step: String
    public var progress: Int
    public var from: String?
    public var to: String?
    /// English technical detail, or nil
    public var error: String?
    /// rolled_back: the data was put back from the backup made just before
    public var dataRestored: Bool
    public var startedAt: Double?
    public var finishedAt: Double?

    public init(id: String = "", action: String = ServerUpdateJob.actionUpdate, auto: Bool = false, state: String = ServerUpdateJob.running,
                step: String = "", progress: Int = 0, from: String? = nil, to: String? = nil, error: String? = nil,
                dataRestored: Bool = false, startedAt: Double? = nil, finishedAt: Double? = nil) {
        self.id = id; self.action = action; self.auto = auto; self.state = state; self.step = step; self.progress = progress
        self.from = from; self.to = to; self.error = error; self.dataRestored = dataRestored
        self.startedAt = startedAt; self.finishedAt = finishedAt
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.str("id")
        action = j.str("action", ServerUpdateJob.actionUpdate)
        auto = j.bool("auto")
        state = j.str("state", "")
        step = j.str("step", "")
        progress = min(max(j.int("progress"), 0), 100)
        from = j.optStr("from")
        to = j.optStr("to")
        error = j.optStr("error")
        dataRestored = j.bool("data_restored")
        startedAt = j.optDouble("started_at")
        finishedAt = j.optDouble("finished_at")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(id, forKey: AnyKey("id"))
        try c.encode(action, forKey: AnyKey("action"))
        try c.encode(auto, forKey: AnyKey("auto"))
        try c.encode(state, forKey: AnyKey("state"))
        try c.encode(step, forKey: AnyKey("step"))
        try c.encode(progress, forKey: AnyKey("progress"))
        try c.encodeIfPresent(from, forKey: AnyKey("from"))
        try c.encodeIfPresent(to, forKey: AnyKey("to"))
        try c.encodeIfPresent(error, forKey: AnyKey("error"))
        try c.encode(dataRestored, forKey: AnyKey("data_restored"))
        try c.encodeIfPresent(startedAt, forKey: AnyKey("started_at"))
        try c.encodeIfPresent(finishedAt, forKey: AnyKey("finished_at"))
    }

    public var isRunning: Bool { state == ServerUpdateJob.running }
    public var isRollback: Bool { action == ServerUpdateJob.actionRollback }
}

/// The version kept from before the last update, to go back to.
public struct ServerUpdateRollback: Codable, Equatable, Hashable, Sendable {
    public var available: Bool
    public var version: String?
    public var createdAt: Double?

    public init(available: Bool = false, version: String? = nil, createdAt: Double? = nil) {
        self.available = available; self.version = version; self.createdAt = createdAt
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        available = j.bool("available")
        version = j.optStr("version")
        createdAt = j.optDouble("created_at")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(available, forKey: AnyKey("available"))
        try c.encodeIfPresent(version, forKey: AnyKey("version"))
        try c.encodeIfPresent(createdAt, forKey: AnyKey("created_at"))
    }
}

/// GET /api/server-update and every action's answer. Every field may be missing or null.
public struct ServerUpdateStatus: Codable, Equatable, Hashable, Sendable {
    public static let ok = "ok", local = "local", unavailable = "unavailable"

    /// ok | local (images built from source on the server machine: no button, show `message`) | unavailable (show nothing)
    public var state: String
    /// already in the user's language, or ""
    public var message: String
    public var current: String?
    public var latest: ServerUpdateRelease?
    public var updateAvailable: Bool
    public var checkedAt: Double?
    public var autoUpdate: Bool
    /// "03:50", the server machine's local time the nightly run starts
    public var autoTime: String?
    public var job: ServerUpdateJob?
    public var rollback: ServerUpdateRollback

    public init(state: String = ServerUpdateStatus.unavailable, message: String = "", current: String? = nil, latest: ServerUpdateRelease? = nil,
                updateAvailable: Bool = false, checkedAt: Double? = nil, autoUpdate: Bool = false, autoTime: String? = nil,
                job: ServerUpdateJob? = nil, rollback: ServerUpdateRollback = ServerUpdateRollback()) {
        self.state = state; self.message = message; self.current = current; self.latest = latest
        self.updateAvailable = updateAvailable; self.checkedAt = checkedAt; self.autoUpdate = autoUpdate; self.autoTime = autoTime
        self.job = job; self.rollback = rollback
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        state = j.str("state", ServerUpdateStatus.unavailable)
        message = j.str("message")
        current = j.optStr("current")
        latest = j.decode("latest", as: ServerUpdateRelease.self).flatMap { $0.version.isEmpty ? nil : $0 }
        updateAvailable = j.bool("update_available")
        checkedAt = j.optDouble("checked_at")
        autoUpdate = j.bool("auto_update")
        autoTime = j.optStr("auto_time")
        job = j.decode("job", as: ServerUpdateJob.self).flatMap { $0.state.isEmpty ? nil : $0 }
        rollback = j.decode("rollback", as: ServerUpdateRollback.self) ?? ServerUpdateRollback()
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(state, forKey: AnyKey("state"))
        try c.encode(message, forKey: AnyKey("message"))
        try c.encodeIfPresent(current, forKey: AnyKey("current"))
        try c.encodeIfPresent(latest, forKey: AnyKey("latest"))
        try c.encode(updateAvailable, forKey: AnyKey("update_available"))
        try c.encodeIfPresent(checkedAt, forKey: AnyKey("checked_at"))
        try c.encode(autoUpdate, forKey: AnyKey("auto_update"))
        try c.encodeIfPresent(autoTime, forKey: AnyKey("auto_time"))
        try c.encodeIfPresent(job, forKey: AnyKey("job"))
        try c.encode(rollback, forKey: AnyKey("rollback"))
    }

    public var isRunning: Bool { job?.isRunning ?? false }
}

/// What the top of 마이 shows for the server update (nil: no card).
public enum ServerUpdateCard: Equatable, Sendable {
    /// an update or a rollback is under way
    case running(rollback: Bool, progress: Int, to: String?)
    /// a newer version is out
    case available(String)
    /// the last run (within a day) stopped before changing anything
    case failed(rollback: Bool)
    /// the new version failed its health check and the server went back to `from` by itself
    case rolledBack(from: String?, to: String?)
    /// going back did not finish: the owner should look at the server machine
    case rollbackFailed
}

/// How a finished run ended, for the result card on the page.
public enum ServerUpdateResult: Equatable, Sendable {
    case updated(to: String?)
    case wentBack(to: String?)
    case failed(rollback: Bool, error: String?)
    case rolledBack(from: String?, to: String?, dataRestored: Bool, error: String?)
    case rollbackFailed(error: String?)
}

/// A refusal or failure of an action: the server's own words (409 detail, already localized) or a technical line.
public struct ServerUpdateFailure: Error, Equatable, Sendable {
    public let status: Int
    public let detail: String
    public init(status: Int, detail: String) { self.status = status; self.detail = detail }
    /// 403 / 404: this device or this server has no server update (nothing to show)
    public var hides: Bool { ServerUpdateRules.hides(status) }
    public var isNetwork: Bool { status == ApiError.noResponse }
}

public enum ServerUpdateRules {
    public static let path = "/api/server-update"
    public static let pollSeconds: Double = 2
    /// A server restart takes a few minutes; the app stops following after 15.
    public static let giveUpSeconds: Double = 15 * 60
    /// A failed or rolled-back run keeps its card on 마이 (and its result on the page) for a day.
    public static let recentSeconds: Double = 24 * 3600
    /// A server that answers again but names no run (or another run) this many times in a row: stop following.
    public static let lostAnswers = 30

    // ---- what shows ----------------------------------------------------------------------------------------------------------------

    /// 403 (not the owner's device) and 404 (a server from before this feature): show nothing.
    public static func hides(_ status: Int) -> Bool { status == 403 || status == 404 }

    /// The 마이 row and the page exist for this answer (state ok or local).
    public static func visible(_ st: ServerUpdateStatus?) -> Bool {
        guard let st else { return false }
        return st.state == ServerUpdateStatus.ok || st.state == ServerUpdateStatus.local
    }

    /// Images built from source on the server machine: only the current version and the server's message.
    public static func isLocal(_ st: ServerUpdateStatus) -> Bool { st.state == ServerUpdateStatus.local }

    /// The row's trailing text: the current version ("" when unknown).
    public static func rowValue(_ st: ServerUpdateStatus) -> String { (st.current ?? "").trimmingCharacters(in: .whitespaces) }

    /// The newer release, when one is out and the server can be updated from the app.
    public static func available(_ st: ServerUpdateStatus) -> ServerUpdateRelease? {
        guard st.state == ServerUpdateStatus.ok, st.updateAvailable, let l = st.latest, !l.version.isEmpty else { return nil }
        return l
    }

    /// The [Update] button: a newer release, state ok, nothing running.
    public static func showUpdateButton(_ st: ServerUpdateStatus) -> Bool { available(st) != nil && !st.isRunning }

    /// The quieter "Go back to <version>": a rollback point, state ok, nothing running. The version it names, else nil.
    public static func rollbackVersion(_ st: ServerUpdateStatus) -> String? {
        guard st.state == ServerUpdateStatus.ok, !st.isRunning, st.rollback.available,
              let v = st.rollback.version?.trimmingCharacters(in: .whitespaces), !v.isEmpty else { return nil }
        return v
    }

    /// "Check now": state ok and nothing running.
    public static func canCheck(_ st: ServerUpdateStatus) -> Bool { st.state == ServerUpdateStatus.ok && !st.isRunning }

    /// The nightly toggle: state ok (it stays usable while a run is under way).
    public static func showAutoToggle(_ st: ServerUpdateStatus) -> Bool { st.state == ServerUpdateStatus.ok }

    /// A finished run that still counts as recent (finished within a day; a run without a finish time counts as recent).
    public static func recent(_ job: ServerUpdateJob, now: Double) -> Bool {
        guard !job.isRunning else { return false }
        guard let f = job.finishedAt ?? job.startedAt, f > 0 else { return true }
        return now - f < recentSeconds
    }

    /// How the last run ended, while it is recent; nil while it runs or when there is none.
    public static func result(_ st: ServerUpdateStatus, now: Double) -> ServerUpdateResult? {
        guard st.state == ServerUpdateStatus.ok, let j = st.job, recent(j, now: now) else { return nil }
        return result(j)
    }

    public static func result(_ j: ServerUpdateJob) -> ServerUpdateResult? {
        switch j.state {
        case ServerUpdateJob.done: return j.isRollback ? .wentBack(to: j.to) : .updated(to: j.to)
        case ServerUpdateJob.failed: return .failed(rollback: j.isRollback, error: j.error)
        case ServerUpdateJob.rolledBack: return .rolledBack(from: j.from, to: j.to, dataRestored: j.dataRestored, error: j.error)
        case ServerUpdateJob.rollbackFailed: return .rollbackFailed(error: j.error)
        default: return nil
        }
    }

    /// The highlighted card at the top of 마이: a run under way, a recent run that went wrong, or a newer version. nil: none.
    public static func card(_ st: ServerUpdateStatus?, now: Double) -> ServerUpdateCard? {
        guard let st, st.state == ServerUpdateStatus.ok else { return nil }
        if let j = st.job {
            if j.isRunning { return .running(rollback: j.isRollback, progress: j.progress, to: j.to) }
            if recent(j, now: now) {
                switch j.state {
                case ServerUpdateJob.rollbackFailed: return .rollbackFailed
                case ServerUpdateJob.rolledBack: return .rolledBack(from: j.from, to: j.to)
                case ServerUpdateJob.failed: return .failed(rollback: j.isRollback)
                default: break
                }
            }
        }
        if let l = available(st) { return .available(l.version) }
        return nil
    }

    /// The progress bar's fraction, 0…1.
    public static func fraction(_ job: ServerUpdateJob) -> Double { Double(min(max(job.progress, 0), 100)) / 100 }

    /// The steps the page names; anything else shows without a step line.
    public static let steps: [String] = ["preflight", "download", "verify", "backup", "restart", "health", "rollback", "self_update", "done"]

    /// The step to name, nil for an unknown or empty step.
    public static func step(_ job: ServerUpdateJob) -> String? { steps.contains(job.step) ? job.step : nil }

    /// "03:50" -> (3, 50); nil for anything else.
    public static func clock(_ text: String?) -> (hour: Int, minute: Int)? {
        guard let t = text?.trimmingCharacters(in: .whitespaces) else { return nil }
        let parts = t.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count >= 2, let h = Int(parts[0]), let m = Int(parts[1]), (0..<24).contains(h), (0..<60).contains(m) else { return nil }
        return (h, m)
    }

    /// The release date ("2026-11-01T00:00:00Z", with or without fractions, or a plain "2026-11-01") as seconds since 1970.
    public static func parseDate(_ text: String) -> Double? {
        let t = text.trimmingCharacters(in: .whitespaces)
        if t.isEmpty { return nil }
        let f = ISO8601DateFormatter()
        if let d = f.date(from: t) { return d.timeIntervalSince1970 }
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: t) { return d.timeIntervalSince1970 }
        f.formatOptions = [.withFullDate]
        if let d = f.date(from: String(t.prefix(10))) { return d.timeIntervalSince1970 }
        return nil
    }

    // ---- calls ---------------------------------------------------------------------------------------------------------------------

    /// What GET answered: the status to show, or why there is nothing.
    public enum Load: Equatable, Sendable {
        /// state ok or local
        case shown(ServerUpdateStatus)
        /// 403, 404 or state unavailable: no row, no card, no page
        case hidden
        /// could not be asked (no network, a server error): keep what was shown before
        case failed(ServerUpdateFailure)
    }

    public static func load(_ api: KeloApi) async -> Load {
        do {
            let st = try await api.get(path, as: ServerUpdateStatus.self)
            return visible(st) ? .shown(st) : .hidden
        } catch {
            let f = failure(error)
            return f.hides ? .hidden : .failed(f)
        }
    }

    public static func failure(_ error: Error) -> ServerUpdateFailure {
        if let e = error as? ApiError { return ServerUpdateFailure(status: e.status, detail: e.detail) }
        if let f = error as? ServerUpdateFailure { return f }
        return ServerUpdateFailure(status: ApiError.noResponse, detail: "\(error)")
    }

    public static func check(_ api: KeloApi) async throws -> ServerUpdateStatus {
        do { return try await api.post(path + "/check", body: .object([:]), as: ServerUpdateStatus.self) } catch { throw failure(error) }
    }

    /// Starts an update; a 409 comes back as `ServerUpdateFailure` with the server's localized detail.
    public static func startUpdate(_ api: KeloApi) async throws -> ServerUpdateStatus {
        do { return try await api.post(path + "/update", body: .object([:]), as: ServerUpdateStatus.self) } catch { throw failure(error) }
    }

    /// Goes back to the rollback point; a 409 (no rollback point) comes back as `ServerUpdateFailure`.
    public static func startRollback(_ api: KeloApi) async throws -> ServerUpdateStatus {
        do { return try await api.post(path + "/rollback", body: .object([:]), as: ServerUpdateStatus.self) } catch { throw failure(error) }
    }

    public static func setAuto(_ api: KeloApi, _ on: Bool) async throws -> ServerUpdateStatus {
        do { return try await api.put(path + "/settings", body: .object(["auto_update": .bool(on)]), as: ServerUpdateStatus.self) }
        catch { throw failure(error) }
    }

    // ---- following a run -----------------------------------------------------------------------------------------------------------

    /// One step of following: the newest status, and whether the last ask failed (the server is restarting: "reconnecting").
    public struct Progress: Equatable, Sendable {
        public var status: ServerUpdateStatus
        public var reconnecting: Bool
        public init(status: ServerUpdateStatus, reconnecting: Bool) { self.status = status; self.reconnecting = reconnecting }
    }

    /// How following ended.
    public enum Followed: Equatable, Sendable {
        /// the run finished (its job is no longer running), or the server stopped naming it
        case finished(ServerUpdateStatus)
        /// 15 minutes passed without an answer that ends it: the last status seen
        case gaveUp(ServerUpdateStatus)
        /// the server turned this device away (401 / 403)
        case refused(ServerUpdateFailure)
    }

    /// While following, an answer that ends it at once (everything else is a restart passing by: keep asking).
    public static func stopsFollowing(_ status: Int) -> Bool { status == 401 || status == 403 }

    /// The run is over in this answer: `jobId`'s job (or any job, without an id) no longer runs.
    public static func finished(_ st: ServerUpdateStatus, jobId: String?) -> Bool {
        guard let j = st.job, !j.isRunning else { return false }
        guard let id = jobId, !id.isEmpty, !j.id.isEmpty else { return true }
        return j.id == id
    }

    /// The answer names no run, or another finished one, while state is ok (the server came back without this run).
    static func lost(_ st: ServerUpdateStatus, jobId: String?) -> Bool {
        guard st.state == ServerUpdateStatus.ok else { return false }
        guard let j = st.job else { return true }
        if let id = jobId, !id.isEmpty, !j.id.isEmpty, j.id != id { return !j.isRunning }
        return false
    }

    /// Asks GET every `poll` seconds until the run that `start` shows is over. Failures in between (the server restarting: no answer,
    /// 5xx from the proxy, even a 404 or a state "unavailable" while the new version starts) are passed by until `giveUp` seconds
    /// have gone; only 401 / 403 end it early. `onProgress` runs after every ask (a screen hops to the main actor in it).
    public static func follow(_ api: KeloApi, from start: ServerUpdateStatus,
                              poll: Double = pollSeconds, giveUp: Double = giveUpSeconds,
                              now: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 },
                              sleep: @escaping @Sendable (Double) async -> Void = { s in try? await Task.sleep(nanoseconds: UInt64(s * 1_000_000_000)) },
                              onProgress: @escaping @Sendable (Progress) async -> Void = { _ in }) async -> Followed {
        var last = start
        let jobId = start.job?.id
        if finished(start, jobId: jobId) || start.job == nil { return .finished(start) }
        let until = now() + giveUp
        var lostCount = 0
        while now() < until {
            await sleep(poll)
            if Task.isCancelled { return .gaveUp(last) }
            do {
                let st = try await api.get(path, as: ServerUpdateStatus.self)
                if st.state == ServerUpdateStatus.unavailable {   // the updater itself restarting: like no answer
                    await onProgress(Progress(status: last, reconnecting: true))
                    continue
                }
                if finished(st, jobId: jobId) { await onProgress(Progress(status: st, reconnecting: false)); return .finished(st) }
                if lost(st, jobId: jobId) {
                    lostCount += 1
                    if lostCount >= lostAnswers { return .finished(st) }
                } else {
                    lostCount = 0
                    last = st
                }
                await onProgress(Progress(status: lost(st, jobId: jobId) ? last : st, reconnecting: false))
            } catch {
                let f = failure(error)
                if stopsFollowing(f.status) { return .refused(f) }
                if error is CancellationError { return .gaveUp(last) }
                await onProgress(Progress(status: last, reconnecting: true))
            }
        }
        return .gaveUp(last)
    }
}
