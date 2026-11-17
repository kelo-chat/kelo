import SwiftUI
import KeloCore
import KeloShared

extension ActionItem: @retroactive Identifiable {
    /// When it happened: `at` (/api/did) or `ts` (/api/actions).
    public var timestamp: Double { at > 0 ? at : ts }
    public var displayTitle: String { title.isEmpty ? line : title }
    /// done | failed | unknown | stopped | interrupted (/api/did `outcome`, or the older `state`).
    public var displayOutcome: String { !outcome.isEmpty ? outcome : (state.isEmpty ? "done" : state) }
    /// send / change / delete (the older list) are all "action".
    public var normalizedKind: String {
        switch kind {
        case "send", "change", "delete": "action"
        default: kind
        }
    }
}

/// The filter chips of "Kelo가 한 일": all / send & change / browser / tasks (Android DidFilter).
public enum DidFilter: CaseIterable, Sendable {
    case all, actions, browser, tasks

    public var labelKey: StringKey {
        switch self {
        case .all: R.string.did_filter_all
        case .actions: R.string.did_filter_actions
        case .browser: R.string.did_filter_browser
        case .tasks: R.string.did_filter_tasks
        }
    }

    public func matches(_ item: ActionItem) -> Bool {
        switch self {
        case .all: true
        case .actions: item.normalizedKind == "action"
        case .browser: item.normalizedKind == "browser"
        case .tasks: item.normalizedKind == "task"
        }
    }
}

/// The filter period for Kelo's outside actions.
public enum ActionPeriod: Int, CaseIterable, Sendable {
    case today = 1
    case sevenDays = 7
    case thirtyDays = 30

    public var days: Int { rawValue }

    public var labelKey: StringKey {
        switch self {
        case .today: R.string.actions_period_today
        case .sevenDays: R.string.actions_period_7d
        case .thirtyDays: R.string.actions_period_30d
        }
    }
}

/// 마이 → Kelo가 한 일 (Android my/ActionsViewModel.kt): what Kelo did, when, where and from which run, as one timeline (GET /api/did; an older server: GET /api/actions).
@MainActor @Observable public final class ActionsModel {
    public private(set) var items: [ActionItem] = []
    public private(set) var groups: [DayGroups.Group<ActionItem>] = []
    public private(set) var loading = false
    public private(set) var failed = false
    public private(set) var period: ActionPeriod = .sevenDays
    public private(set) var filter: DidFilter = .all

    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let todayProvider: () -> LocalDay
    @ObservationIgnored private let zone: TimeZone

    public init(
        api: KeloApi,
        today: @escaping () -> LocalDay = { LocalDay(seconds: Date().timeIntervalSince1970) },
        zone: TimeZone = .current
    ) {
        self.api = api
        self.todayProvider = today
        self.zone = zone
    }

    public func load() async {
        loading = true
        defer { loading = false }
        do {
            let res: ActionList
            do {
                res = try await api.get("/api/did?days=\(period.days)", as: ActionList.self)
            } catch let e as ApiError where e.status == 404 {
                res = try await api.get("/api/actions?days=\(period.days)", as: ActionList.self)   // an older server
            }
            // /api/did items carry no id: give each a stable one by position so rows never collide
            items = res.items.enumerated().map { i, item in
                var x = item
                if x.id == 0 { x.id = -Int64(i + 1) }
                return x
            }
            regroup()
            failed = false
        } catch is CancellationError {
            return
        } catch {
            failed = true
        }
    }

    private func regroup() {
        let z = zone
        let f = filter
        groups = DayGroups.group(items.filter { f.matches($0) }, today: todayProvider()) { item in
            item.timestamp > 0 ? LocalDay(seconds: item.timestamp, zone: z) : nil
        }
    }

    public func setFilter(_ f: DidFilter) {
        guard filter != f else { return }
        filter = f
        regroup()
    }

    public func setPeriod(_ p: ActionPeriod) async {
        guard period != p else { return }
        period = p
        await load()
    }

    public func refresh() async {
        await load()
    }
}
