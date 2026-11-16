import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 자원 → 정리하기: what can be removed, what is ticked, and the removal itself.
@MainActor @Observable final class CleanupModel {
    private(set) var list: CleanupList?
    private(set) var failed: StringKey?
    private(set) var selected: Set<String> = []
    private(set) var busy = false
    private(set) var result: CleanupResult?
    private(set) var error: StringKey?
    /// Goes up on "try again": the page's `.task(id:)` loads again.
    private(set) var attempt = 0
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi) { self.api = api }

    func retry() { attempt += 1 }

    func load() async {
        failed = nil
        do {
            let l = try await api.cleanupList()
            list = l
            selected = StorageRules.keep(selected, l)
        } catch is CancellationError {
            return
        } catch {
            failed = (error as? ApiError)?.status == 403 ? R.string.storage_owner_only : R.string.storage_load_failed
        }
    }

    func toggle(_ id: String) { selected = StorageRules.toggle(selected, id) }
    func toggle(group g: CleanupGroup) { selected = StorageRules.toggleGroup(selected, g) }

    func delete() {
        let ids = Array(selected)
        busy = true
        error = nil
        Task { @MainActor [self] in
            defer { busy = false }
            do {
                let r = try await api.cleanup(ids)
                result = r
                let fresh = CleanupList(groups: r.groups, total: r.total, olderThanDays: list?.olderThanDays ?? 14)
                list = fresh
                selected = StorageRules.keep(selected.subtracting(r.removed), fresh)
            } catch {
                self.error = R.string.storage_delete_failed
            }
        }
    }
}
