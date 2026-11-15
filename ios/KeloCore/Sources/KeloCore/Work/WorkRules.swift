import Foundation

/*
 * The work page's one timeline, worked out apart from the UI so it can be tested (Android work/WorkRules.kt): what runs now, what
 * needs an answer, what comes next (an agenda of the schedules) and what is done (by day, with a filter). Background jobs and their
 * runs are nested under the task that handed them off.
 */

/// A background job with the tasks that are its runs (oldest first).
public struct JobNode: Equatable, Sendable {
    public var worker: Worker
    public var runs: [AgentTask] = []
    public init(worker: Worker, runs: [AgentTask] = []) { self.worker = worker; self.runs = runs }
    public var key: String { "job:" + worker.id }
}

/// A task with the jobs it handed off and the other tasks that name it as their parent.
public struct TaskNode: Equatable, Sendable {
    public var task: AgentTask
    public var jobs: [JobNode] = []
    public var children: [AgentTask] = []
    public init(task: AgentTask, jobs: [JobNode] = [], children: [AgentTask] = []) { self.task = task; self.jobs = jobs; self.children = children }
    public var key: String { "task:\(task.id)" }
    /// The task itself, or anything under it, is still going.
    public var live: Bool {
        task.running || children.contains { $0.running } || jobs.contains { $0.worker.running || $0.runs.contains { $0.running } }
    }
    /// The task itself is over but a job it handed off still goes on (running, waiting for the owner, or blocked).
    public var inBackground: Bool {
        !task.running && jobs.contains { j in
            Timeline.jobGoing.contains(j.worker.status) || j.runs.contains { $0.running }
        }
    }
    public var problem: Bool {
        task.status == AgentTask.statusFailed || task.status == AgentTask.statusStopped ||
            jobs.contains { $0.worker.status == Worker.statusFailed || $0.worker.status == Worker.statusStopped }
    }
}

/// Something that waits for the user.
public enum Ask: Equatable, Sendable {
    /// An approval card; a payment may also need this phone to send its card.
    case approval(PendingApproval)
    /// A background job stopped for a login, the tab, an answer, or a restart.
    case job(Worker)
    /// Something the agent asked about twice without an answer, now on hold.
    case hold(Followup)
    /// A secure entry waiting for its value (a key, a sign-in, a card, a payment PIN): a short row; the card itself is in the chat
    /// (`chatCard`: the agent asked there) and a tap goes to it.
    case entry(SecretRequest)

    public var key: String {
        switch self {
        case .approval(let a): return "ask:" + a.id
        case .job(let w): return "askjob:" + w.id
        case .hold(let f): return "askhold:" + f.id
        case .entry(let r): return "askentry:" + r.id
        }
    }

    /// An entry the agent asked for in the chat (its card is there); false: the Keychain's own waiting item.
    public var chatCard: Bool {
        if case .entry(let r) = self { return r.fromAgent }
        return false
    }

    /// A payment that also needs this phone to send its card.
    public var deliver: Bool {
        if case .approval(let a) = self { return a.event.card?.needsCard == true }
        return false
    }
}

/// The past list's filter chips.
public enum PastFilter: Sendable { case all, done, problems, automatic }

public enum Timeline {
    /// A job that is not over yet: running, waiting for the owner's answer, or blocked; its browser tab can still be open.
    public static let jobGoing: Set<String> = [Worker.statusRunning, Worker.statusNeedsInput, Worker.statusBlocked]

    /// Sources that mean "the agent started this by itself" rather than a chat message.
    static let autoSources: Set<String> = ["api", "background", "worker", "handoff"]

    public static func automatic(_ t: AgentTask) -> Bool { t.source.hasPrefix("schedule") || autoSources.contains(t.source) }

    public struct Nested: Equatable, Sendable {
        public let roots: [TaskNode]
        public let orphanJobs: [JobNode]
    }

    public struct Now: Equatable, Sendable {
        public let tasks: [TaskNode]
        public let jobs: [JobNode]
        public var empty: Bool { tasks.isEmpty && jobs.isEmpty }
        public var count: Int { tasks.count + jobs.count }
    }

    /// Nests `tasks` (newest first) and `workers`: a job sits under the task that handed it off, a task that is a run of a known job
    /// sits under that job, and a task naming a parent sits under it. What has no parent in the lists stays at the top in the tasks'
    /// order; a job whose parent task is not loaded becomes a node of its own.
    public static func nest(_ tasks: [AgentTask], _ workers: [Worker]) -> Nested {
        let workerIds = Set(workers.map(\.id))
        let taskIds = Set(tasks.map(\.id))
        let runsBy = Dictionary(grouping: tasks.filter { $0.workerId.map(workerIds.contains) == true }, by: { $0.workerId! })
        let jobsByParent = Dictionary(grouping: workers.filter { $0.parentTaskId.map(taskIds.contains) == true }, by: { $0.parentTaskId! })
        let childrenBy = Dictionary(grouping: tasks.filter { t in
            !(t.workerId.map(workerIds.contains) ?? false) && t.parentId.map(taskIds.contains) == true && t.parentId != t.id
        }, by: { $0.parentId! })
        var nested = Set<Int64>()
        runsBy.values.joined().forEach { nested.insert($0.id) }
        childrenBy.values.joined().forEach { nested.insert($0.id) }
        func runs(_ w: Worker) -> [AgentTask] { stableSorted(runsBy[w.id] ?? [], by: \.startedAt) }
        let roots = tasks.filter { !nested.contains($0.id) }.map { t in
            TaskNode(task: t, jobs: (jobsByParent[t.id] ?? []).map { JobNode(worker: $0, runs: runs($0)) },
                     children: stableSorted(childrenBy[t.id] ?? [], by: \.startedAt))
        }
        let orphans = workers.filter { $0.parentTaskId == nil || !taskIds.contains($0.parentTaskId!) }.map { JobNode(worker: $0, runs: runs($0)) }
        return Nested(roots: roots, orphanJobs: orphans)
    }

    /// What runs now: tasks with anything still going, and jobs of their own that are running; newest first.
    public static func now(_ n: Nested) -> Now {
        Now(tasks: n.roots.filter(\.live), jobs: n.orphanJobs.filter { $0.worker.running || $0.runs.contains { $0.running } })
    }

    /// Needs your answer: approvals (payments that need this phone's card first), secure entries waiting for a value, stopped jobs,
    /// then follow-ups on hold. A follow-up
    /// about an approval or a job already listed is left out (the card above settles it).
    public static func asks(pending: [PendingApproval], workers: [Worker], followups: [Followup] = [], entries: [SecretRequest] = []) -> [Ask] {
        let approvals = pending.enumerated().sorted { a, b in
            let da = a.element.event.card?.needsCard == true, db = b.element.event.card?.needsCard == true
            if da != db { return da }
            if a.element.askedAt != b.element.askedAt { return a.element.askedAt < b.element.askedAt }
            return a.offset < b.offset
        }.map { Ask.approval($0.element) }
        let stopped = stableSorted(workers.filter { $0.needsYou || $0.status == Worker.statusInterrupted }, by: \.updatedAt)
        let shownApprovals = Set(pending.map(\.event.approvalId))
        let shownJobs = Set(stopped.map(\.id))
        let holds = FollowupRules.onHold(followups).filter { f in
            !f.actions.contains { (!$0.approvalId.isEmpty && shownApprovals.contains($0.approvalId)) || (!$0.workerId.isEmpty && shownJobs.contains($0.workerId)) }
        }.map(Ask.hold)
        let waiting = stableSorted(entries.filter(\.open), by: \.createdAt).map(Ask.entry)
        return approvals + waiting + stopped.map(Ask.job) + holds
    }

    /// What is done: finished tasks (with what hangs under them) passing `filter`, by day, newest first.
    public static func past(_ n: Nested, filter: PastFilter, today: LocalDay, zone: TimeZone = .current) -> [DayGroups.Group<TaskNode>] {
        let done = n.roots.filter { !$0.live }.filter { node in
            switch filter {
            case .all: return true
            case .done: return node.task.status == AgentTask.statusDone && !node.problem
            case .problems: return node.problem
            case .automatic: return automatic(node.task)
            }
        }
        return DayGroups.group(done, today: today) { DayGroups.dayOf($0.task, zone: zone) }
    }

    /// The browser tab a task works in: one the server ties to it, or (for a job's run) the job's own tab.
    public static func tabFor(_ task: AgentTask, tabs: [BrowserTab], workers: [Worker] = []) -> String? {
        if let t = tabs.first(where: { $0.taskId == task.id && $0.status != "ended" }) { return t.id }
        guard let wid = task.workerId else { return nil }
        return workers.first { $0.id == wid }?.tab
    }

    /// A job's tab, when it works in a browser: only while the job is going: a finished job's tab is closed.
    public static func tabFor(_ worker: Worker) -> String? {
        guard let tab = worker.tab, !tab.trimmingCharacters(in: .whitespaces).isEmpty,
              worker.kind == "browser" || worker.thumbnail != nil,
              jobGoing.contains(worker.status) else { return nil }
        return tab
    }

    /// How far a job is through its step budget (0...1), or nil when the server gave no budget.
    public static func progress(_ worker: Worker) -> Double? {
        worker.maxSteps <= 0 ? nil : min(max(Double(worker.steps) / Double(worker.maxSteps), 0), 1)
    }
}

/// What comes next: the schedules as an agenda, by the day of their next run.
public enum Agenda {
    public struct Entry: Equatable, Sendable { public let schedule: Schedule; public let at: Double? }
    public struct Day: Equatable, Sendable { public let date: LocalDay; public let entries: [Entry] }
    /// `days` in date order with their runs in time order; `unscheduled`: on but with no next run; `paused`: switched off or paused.
    public struct Plan: Equatable, Sendable {
        public let days: [Day], unscheduled: [Schedule], paused: [Schedule]
        public var empty: Bool { days.isEmpty && unscheduled.isEmpty && paused.isEmpty }
    }
    public enum When: Sendable { case today, tomorrow, later }

    public static func build(_ schedules: [Schedule], zone: TimeZone = .current) -> Plan {
        let on = schedules.filter { $0.enabled && !$0.paused && $0.archivedAt == nil }
        let timed = on.compactMap { s in ScheduleTimes.nextRunSeconds(s).map { Entry(schedule: s, at: $0) } }
        let byDay = Dictionary(grouping: timed) { LocalDay(seconds: $0.at!, zone: zone) }
        let days = byDay.keys.sorted().map { d in
            Day(date: d, entries: byDay[d]!.sorted { ($0.at!, $0.schedule.name) < ($1.at!, $1.schedule.name) })
        }
        let timedIds = Set(timed.map(\.schedule.id))
        let unscheduled = on.filter { !timedIds.contains($0.id) }
        // paused by a run first (they want attention), then the ones switched off
        let off = schedules.enumerated().filter { (!$0.element.enabled || $0.element.paused) && $0.element.archivedAt == nil }
            .sorted { $0.element.paused != $1.element.paused ? $0.element.paused : $0.offset < $1.offset }.map(\.element)
        return Plan(days: days, unscheduled: unscheduled, paused: off)
    }

    public static func whenOf(_ date: LocalDay, today: LocalDay) -> When {
        if date == today { return .today }
        if date == today.adding(days: 1) { return .tomorrow }
        return .later
    }
}

/// Watching: those that need a look (an error, a recent hit) first, then the ones on, then the ones off.
public enum Watching {
    public static func order(_ watchers: [Watcher]) -> [Watcher] {
        watchers.sorted { a, b in
            if rank(a) != rank(b) { return rank(a) < rank(b) }
            if (a.lastFiredAt ?? 0) != (b.lastFiredAt ?? 0) { return (a.lastFiredAt ?? 0) > (b.lastFiredAt ?? 0) }
            return a.name < b.name
        }
    }

    public static func rank(_ w: Watcher) -> Int {
        switch w.state {
        case "error": return 0
        case "watching": return 1
        case "done": return 2
        default: return 3
        }
    }
}

/// The red dot on the header's agent (and the app icon's badge): only while something waits for the owner to act. That is
/// approvals waiting, open follow-ups (a question to answer, a login asked for, a job that needs input, an approval that ran out
/// and can be tried again) and secure entries the agent asked for. Finished work and notifications nobody has looked at get no
/// badge (the work page marks its own unseen rows). Handled items leave the server's counts, so it goes on the next status poll.
/// (Android WorkBadge)
public enum WorkBadge {
    public struct Shown: Equatable, Sendable {
        public let count: Int
        public init(count: Int) { self.count = count }
    }

    public static func waiting(_ s: AgentStatus?) -> Int {
        guard let s else { return 0 }
        return s.pendingApprovals + s.openFollowups + s.secretRequests
    }

    public static func of(_ s: AgentStatus?) -> Shown? {
        let n = waiting(s)
        return n > 0 ? Shown(count: n) : nil
    }

    /// One kind of thing that waits and how many, for the words VoiceOver says ("입력을 기다리는 키 1개").
    public struct Part: Equatable, Sendable {
        public let kind: String
        public let count: Int
        public init(kind: String, count: Int) { self.kind = kind; self.count = count }
    }

    public static let approvals = "approvals", followups = "followups", keys = "key", logins = "login", cards = "card",
                      pins = "payment_pin", entries = "entries"

    /// What waits, by kind, in this order: approvals, follow-ups, then secure entries (keys, logins, cards, payment PINs).
    public static func parts(_ s: AgentStatus?) -> [Part] {
        guard let s else { return [] }
        var out: [Part] = []
        if s.pendingApprovals > 0 { out.append(Part(kind: approvals, count: s.pendingApprovals)) }
        if s.openFollowups > 0 { out.append(Part(kind: followups, count: s.openFollowups)) }
        var sorted = 0
        for k in [keys, logins, cards, pins] {
            let n = Int(s.secretKinds[k] ?? 0)
            if n > 0 { out.append(Part(kind: k, count: n)); sorted += n }
        }
        if s.secretRequests > sorted { out.append(Part(kind: entries, count: s.secretRequests - sorted)) }
        return out
    }

    /// The status after a secure entry of `kind` was saved or cancelled here: the badge counts it out at once (the next poll agrees).
    public static func entryClosed(_ s: AgentStatus?, kind: String) -> AgentStatus? {
        guard var s, s.secretRequests > 0 else { return s }
        s.secretRequests -= 1
        let left = (s.secretKinds[kind] ?? 0) - 1
        if left > 0 { s.secretKinds[kind] = left } else { s.secretKinds.removeValue(forKey: kind) }
        return s
    }
}

/// Kotlin's sortedBy: stable.
func stableSorted<T, K: Comparable>(_ items: [T], by key: (T) -> K) -> [T] {
    items.enumerated().sorted { key($0.element) != key($1.element) ? key($0.element) < key($1.element) : $0.offset < $1.offset }.map(\.element)
}
