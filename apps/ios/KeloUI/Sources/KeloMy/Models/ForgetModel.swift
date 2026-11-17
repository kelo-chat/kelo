import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Forgotten topics (Android my/ForgetViewModel.kt): what Kelo was asked to forget, and "Allow again" (the topic may be
/// written about again). `items` is nil until the first load; `failed`: the list could not be read (a loaded list stays shown).
@MainActor @Observable final class ForgetModel {
    private(set) var items: [ForgottenTopic]?
    private(set) var failed = false
    private(set) var busy: Set<String> = []
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearNotice() { notice = nil }

    func load() async {
        do { items = try await api.forgottenTopics(); failed = false }
        catch { failed = true }
    }

    /// "Allow again": the topic leaves the list at once; one the server no longer has (404) counts as done; any other failure puts the
    /// list back.
    func allowAgain(_ t: ForgottenTopic) {
        guard !busy.contains(t.id) else { return }
        items = items.map { ForgottenRules.without($0, t.id) }
        busy.insert(t.id)
        jobs.run { [self] in
            defer { busy.remove(t.id) }
            do { try await api.allowForgottenAgain(t.id); notice = L(R.string.forget_allowed) }
            catch { notice = L(R.string.connect_failed); await load() }
        }
    }

    /// What was removed as words and counts ("Memory: 3 · Notes: 2"), or that nothing was found.
    static func removedLine(_ t: ForgottenTopic) -> String {
        let parts = ForgottenRules.parts(t.removed)
        if parts.isEmpty { return L(R.string.forget_nothing_removed) }
        return L(R.string.forget_removed, parts.map { L(R.string.forget_count, forgetCategoryWord($0.category), MyFormat.number(Int64($0.count))) }.joined(separator: " · "))
    }
}
