import SwiftUI
import KeloCore
import KeloShared

extension ActionItem: @retroactive Identifiable {}

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

/// 마이 → Kelo가 한 일 (Android my/ActionsViewModel.kt): what Kelo did outside, when, where and from which run (GET /api/actions).
@MainActor @Observable public final class ActionsModel {
    public private(set) var items: [ActionItem] = []
    public private(set) var groups: [DayGroups.Group<ActionItem>] = []
    public private(set) var loading = false
    public private(set) var failed = false
    public private(set) var period: ActionPeriod = .sevenDays

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
            let res = try await api.get("/api/actions?days=\(period.days)", as: ActionList.self)
            items = res.items
            let t = todayProvider()
            let z = zone
            groups = DayGroups.group(res.items, today: t) { item in
                item.ts > 0 ? LocalDay(seconds: item.ts, zone: z) : nil
            }
            failed = false
        } catch is CancellationError {
            return
        } catch {
            failed = true
        }
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
