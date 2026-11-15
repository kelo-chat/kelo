import Foundation
import Observation
import KeloCore
#if canImport(UserNotifications)
import UserNotifications
#endif

/// The number on the app icon: only what waits for the owner (WorkBadge: approvals, follow-ups, secure entries asked), never
/// finished work or notifications nobody looked at. Set on each status poll where it changed and on each notification check.
public enum AppIconBadge {
    @MainActor public static func set(_ count: Int) {
        #if os(iOS) && canImport(UserNotifications)
        UNUserNotificationCenter.current().setBadgeCount(max(0, count)) { _ in }
        #endif
    }
}

/// What the agent is doing right now, for the floating header (Android chat/AgentActivity.kt AgentActivity + the shell's
/// pollAgentStatus). The chat sets the followed reply's fields; the shell's poll loop sets the rest.
@MainActor @Observable
public final class AgentActivityModel {
    public var following = false
    public var turnLine: String?
    public var polledLine: String?
    /// The newest GET /api/agent/status answer (the header's dot and the work badge read it).
    public var status: AgentStatus?
    public var turnKind: TurnKind = .thinking
    public var polledBrowsing = false
    public var offline = false
    public var stuck = false
    /// The chat's microphone is listening.
    public var listening = false
    /// When work last finished (monotonic seconds; nil = not since launch): the character cheers for `AgentActivityRules.doneMs`.
    public var doneAt: Double?
    /// The night (outside the owner's contact hours when known, else 23:00–07:00) puts the character to sleep when nothing happens.
    public var night = AgentActivityRules.isNight(hour: Calendar.current.component(.hour, from: Date()))
    /// When the owner or the agent last did something (monotonic seconds; nil = nothing since launch): the app opened, a message
    /// typed, sent or come in, a reply, work, an approval or the microphone. At night the character falls asleep only
    /// `AgentActivityRules.sleepAfterIdleMs` after it.
    public var activeAt: Double?
    /// The owner's contact hours (GET /api/proactive); nil = not known, the night is then 23:00–07:00.
    public var sleepHours: SleepHours?
    /// Called with every status the poll reads (AppModel sets it: a tone changed in the chat or on another device reaches the
    /// profile, Android MainShell's ToneRules.synced).
    @ObservationIgnored public var onStatus: (@MainActor (AgentStatus) -> Void)?

    public init() {}

    /// The name pill's second line: the followed reply's own while the chat follows one, else the polled one.
    public var line: String? { AgentActivityRules.shown(following: following, turnLine: turnLine, polled: polledLine) }

    /// What the header's character shows (`AgentActivityRules.mood`).
    public func mood(now: Double = ProcessInfo.processInfo.systemUptime) -> KeloMood {
        let since = doneAt.map { Int64((now - $0) * 1000) }.flatMap { $0 < AgentActivityRules.doneMs ? $0 : nil }
        return AgentActivityRules.mood(.init(offline: offline, status: status, stuck: stuck, listening: listening, following: following,
                                             turnKind: turnKind, polledBrowsing: polledBrowsing, sinceDoneMs: since, night: night,
                                             idleMs: AgentActivityRules.idleMs(now: now, activeAt: activeAt)))
    }

    /// Something happened at `now` (monotonic seconds): the character stays awake a while. At most one change a second, so typing
    /// does not redraw the header per key.
    public func touch(now: Double = ProcessInfo.processInfo.systemUptime) {
        if let last = activeAt, now >= last, now - last < 1 { return }
        activeAt = now
    }

    public var badge: WorkBadge.Shown? { WorkBadge.of(status) }

    public func reset() {
        following = false; turnLine = nil; polledLine = nil; status = nil; turnKind = .thinking
        polledBrowsing = false; offline = false; stuck = false; listening = false; doneAt = nil
        activeAt = nil; sleepHours = nil
    }

    /// A followed reply's event moves the line and the kind (the chat calls this for every event).
    public func follow(_ ev: TurnEvent) {
        turnLine = AgentActivityRules.next(turnLine, ev)
        turnKind = AgentActivityRules.turnKind(turnKind, ev)
    }

    public func startFollowing() { following = true; turnLine = nil; turnKind = .thinking; touch() }

    public func stopFollowing(finished: Bool) {
        following = false; turnLine = nil; turnKind = .thinking
        touch()   // the reply's end starts the quiet time before the character may sleep
        if finished { doneAt = ProcessInfo.processInfo.systemUptime }
    }

    /// GET /api/agent/status every few seconds while the app is in the foreground. Run from the shell's `.task`; ends when the
    /// task is cancelled (the app goes to the background or signs out).
    public func poll(api: @escaping @MainActor () -> KeloApi?) async {
        var tabsBefore: [String: Double] = [:]
        var before: AgentStatus?
        var failures = 0
        var round = 0
        let stuckEvery = 10
        let hoursEvery = 100   // about five minutes
        touch()   // the owner opened the app: the character is awake
        while !Task.isCancelled {
            night = AgentActivityRules.nightAt(Date(), hours: sleepHours)
            // the microphone, a followed reply, work or a question waiting keeps the character up
            if listening || following { touch() }
            if let api = api() {
                do {
                    let s = try await api.agentStatus()
                    failures = 0
                    offline = false
                    if WorkBadge.waiting(s) != WorkBadge.waiting(status) || status == nil { AppIconBadge.set(WorkBadge.waiting(s)) }
                    status = s
                    onStatus?(s)
                    if s.busy || s.pendingApprovals > 0 { touch() }
                    // the owner's contact hours decide the character's night; asked when the app comes up and every few minutes
                    if round % hoursEvery == 0, let h = try? await api.get("/api/proactive", as: SleepHours.self) {
                        sleepHours = h
                        night = AgentActivityRules.nightAt(Date(), hours: h)
                    }
                    if !following { polledLine = AgentActivityRules.line(s) }
                    let nowS = Date().timeIntervalSince1970
                    if s.busy && !following {
                        if let t: BrowserTabsBrief = try? await api.get("/api/browser/sessions?thumbnails=false") {
                            polledBrowsing = AgentActivityRules.polledBrowsing(s, tabs: t.items, before: tabsBefore, nowS: nowS)
                            tabsBefore = Dictionary(t.items.compactMap { b in b.lastActivity.map { (b.id, $0) } }, uniquingKeysWith: { $1 })
                        }
                    } else {
                        polledBrowsing = false
                        tabsBefore = [:]
                    }
                    let ended = before?.busy == true && !s.busy
                    if before == nil || ended || before?.unseenTasks != s.unseenTasks || round % stuckEvery == 0 {
                        let newest = (try? await api.get("/api/tasks?limit=1", as: TasksBrief.self))?.items.first
                        let waiting = (try? await api.get("/api/workers?status=needs_input&limit=5", as: WorkersBrief.self))?.items ?? []
                        stuck = AgentActivityRules.needsUser(newest: newest, waiting: waiting, nowS: nowS)
                        if AgentActivityRules.finished(before: before, now: s, newest: newest) && !following {
                            doneAt = ProcessInfo.processInfo.systemUptime
                        }
                    }
                    before = s
                    round += 1
                } catch let e as ApiError {
                    if e.isNetwork {
                        failures += 1
                        if failures >= AgentActivityRules.offlineAfter { offline = true }
                    }
                    if e.status == 404 { polledLine = nil; return }
                } catch {
                    if Task.isCancelled { return }
                }
            }
            try? await Task.sleep(nanoseconds: AgentActivityRules.pollMs * 1_000_000)
        }
    }
}
