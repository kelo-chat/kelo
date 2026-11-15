import Foundation

// Hand-written helpers on the generated models (ApiModels.swift), the same as the Android models' members.

extension AgentTask {
    public static let statusRunning = "running"
    public static let statusWaiting = "waiting_approval"
    public static let statusDone = "done"
    public static let statusFailed = "failed"
    public static let statusStopped = "stopped"
    public var running: Bool { status == AgentTask.statusRunning || status == AgentTask.statusWaiting }
    public var failed: Bool { status == AgentTask.statusFailed }
}

extension TaskDetail {
    public var running: Bool { status == AgentTask.statusRunning || status == AgentTask.statusWaiting }
}

extension Worker {
    public static let statusRunning = "running"
    public static let statusNeedsInput = "needs_input"
    public static let statusBlocked = "blocked"
    public static let statusFailed = "failed"
    public static let statusStopped = "stopped"
    public static let statusInterrupted = "interrupted"
    public static let resumable: Set<String> = [statusNeedsInput, statusBlocked, statusFailed, statusStopped, statusInterrupted]

    public var running: Bool { status == Worker.statusRunning }
    /// Stopped and waiting for the user (a login, the tab handed back, an answer).
    public var needsYou: Bool { status == Worker.statusNeedsInput || status == Worker.statusBlocked }
    public var active: Bool { running || needsYou || status == Worker.statusInterrupted }
    public var canResume: Bool { Worker.resumable.contains(status) }
}

extension WorkerDetail {
    /// The list form of the job.
    public func brief() -> Worker {
        Worker(id: id, kind: kind, title: title, status: status, parentTaskId: parentTaskId, taskId: taskId, turnId: turnId, tab: tab,
               startedAt: startedAt, updatedAt: updatedAt, finishedAt: finishedAt, steps: steps, maxSteps: maxSteps, deadlineAt: deadlineAt,
               waitingFor: waitingFor, login: login, reason: reason, statusText: statusText, lastUrl: lastUrl, thumbnail: thumbnail)
    }
}

extension Tokens {
    public var sum: Int64 { inputTokens + outputTokens }
}

extension OAuthFlow {
    public var pending: Bool { status == "pending" }
}

extension WalletLimit {
    /// A currency the user added (no server default): resetting removes it.
    public var added: Bool { defaultPerPayment == nil }
}

extension WalletPayment {
    public static let kindAuto = "auto"
    public var auto: Bool { kind == WalletPayment.kindAuto }
}

extension VaultEntry {
    public static let kindLogin = "login"
    public static let kindEmail = "email"
    public static let kindApi = "api"
    public var isLogin: Bool { kind == VaultEntry.kindLogin }
    /// The last real use failed (a sign-in the site turned down, a key the service refused).
    public var isBad: Bool { lastResult == "failed" || lastResult == "rejected" }
    /// Made in the Connectors tab by signing in, not by typing a key.
    public var signedIn: Bool { kind == VaultEntry.kindEmail || placement.via == "oauth" || name == "github-api" }
}

extension LearnedRule {
    public static let proposed = "proposed"
    public static let accepted = "accepted"
    public static let rejected = "rejected"
}

/// The learned rules in the three parts the page shows.
public struct RuleGroups: Equatable, Sendable {
    public let proposed: [LearnedRule]
    public let accepted: [LearnedRule]
    public let rejected: [LearnedRule]
}

/// When the last run happened, in words (Android RunWhen).
public enum RunWhen: Sendable { case lastNight, today, day }

/// The last run's status line, apart from its words (Android RunLine): the app turns each case into a sentence in its language.
public enum RunLine: Equatable, Sendable {
    case done(newRules: Int)
    case noChat, switchedOff, inUse, skipped
    case failedModel, failedAnswer, failed
    case noModel

    public var failedLine: Bool { [.failedModel, .failedAnswer, .failed].contains(self) }
}

/// The look-back's small rules (Android agent/AgentModels.kt LookbackRules).
public enum LookbackRules {
    /// The server puts a proposed rule into replies already from this confidence on (reflection.PROMPT_CONFIDENCE).
    public static let inUseConfidence = 0.8

    /// Proposed first (most certain, then newest), then accepted in the server's order, then rejected newest first. Blank rules and
    /// unknown statuses are left out.
    public static func group(_ rules: [LearnedRule]) -> RuleGroups {
        let shown = rules.filter { !$0.rule.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        let proposed = shown.filter { $0.status == LearnedRule.proposed }.sorted {
            if $0.confidence != $1.confidence { return $0.confidence > $1.confidence }
            if $0.date != $1.date { return $0.date > $1.date }
            return $0.id > $1.id
        }
        let rejected = shown.filter { $0.status == LearnedRule.rejected }.sorted {
            if $0.date != $1.date { return $0.date > $1.date }
            return $0.id > $1.id
        }
        return RuleGroups(proposed: proposed, accepted: shown.filter { $0.status == LearnedRule.accepted }, rejected: rejected)
    }

    /// Whether replies already follow it: accepted, or proposed with enough confidence.
    public static func inUse(_ r: LearnedRule) -> Bool {
        r.status == LearnedRule.accepted || (r.status == LearnedRule.proposed && r.confidence >= inUseConfidence)
    }

    /// The groups with the rules the user just decided on still among the proposed ones (shown with "kept" / "discarded" and an
    /// undo for a few seconds), in their old place.
    public static func group(_ rules: [LearnedRule], justDecided: Set<Int64>) -> RuleGroups {
        group(rules.map { r in var c = r; if justDecided.contains(r.id) { c.status = LearnedRule.proposed }; return c })
    }

    /// How long the "kept" / "discarded" note with its undo stays on a decided rule (seconds).
    public static let undoSeconds = 5.0

    /// The status line of a run from its structured fields only (never the server's free text); nil for an unknown status.
    public static func runLine(_ run: LookbackRun) -> RunLine? {
        switch run.status {
        case "done": return .done(newRules: max(0, run.added))
        case "skipped":
            switch run.code {
            case "no_messages": return .noChat
            case "switched_off": return .switchedOff
            case "in_use": return .inUse
            default: return .skipped
            }
        case "failed":
            switch run.code {
            case "model_error": return .failedModel
            case "bad_output": return .failedAnswer
            default: return .failed
            }
        case "pending": return .noModel
        default: return nil
        }
    }

    /// When `at` (unix seconds) was, seen from `now`: before 6 in the morning today or from 6 in the evening yesterday is last night.
    public static func runWhen(_ at: Double, now: Date, calendar: Calendar = .current) -> RunWhen {
        let when = Date(timeIntervalSince1970: at)
        let hour = calendar.component(.hour, from: when)
        if calendar.isDate(when, inSameDayAs: now) { return hour < 6 ? .lastNight : .today }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(when, inSameDayAs: yesterday), hour >= 18 {
            return .lastNight
        }
        return .day
    }
}

/// Chat history kept on the phone for the next open (Android ChatHistoryCache): the newest 200 messages, at most 300k characters.
public enum ChatHistoryCache {
    public static let maxMessages = 200
    public static let maxChars = 300_000
}
