import Foundation
import Observation
import KeloCore

/// The search screen's state (Android chat search): the query as typed, the hits shown, and paging by `before`. A pause after each
/// change before asking; an answer for a query already replaced is dropped.
@MainActor @Observable
public final class ChatSearchModel {
    public private(set) var query = ""
    public private(set) var hits: [MessageHit] = []
    public private(set) var loading = false
    public private(set) var failed = false
    /// The last page was a full one: more hits may follow (asked when the list reaches its end).
    public private(set) var more = false

    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let pauseNanos: UInt64
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var generation = 0

    public init(api: @escaping () -> KeloApi?, pauseNanos: UInt64 = 250_000_000) {
        self.api = api
        self.pauseNanos = pauseNanos
    }

    /// The query has words in it (a blank one shows the hint, with no request).
    public var hasQuery: Bool { MessageSearchRules.query(query) != nil }

    /// A new query as typed: the search runs once typing pauses (`submit` runs it at once). A search for the old text is dropped.
    public func setQuery(_ text: String) {
        query = text
        start(pause: pauseNanos)
    }

    /// The Search key: the search runs now, without the pause.
    public func submit() { start(pause: 0) }

    private func start(pause: UInt64) {
        generation += 1
        task?.cancel()
        hits = []
        more = false
        failed = false
        guard let q = MessageSearchRules.query(query) else { loading = false; return }
        loading = true
        let g = generation
        task = Task { [weak self] in
            if pause > 0 { try? await Task.sleep(nanoseconds: pause) }
            guard !Task.isCancelled, let self else { return }
            await self.fetch(q, before: nil, generation: g)
        }
    }

    /// The next page, when the list reaches its end.
    public func loadMore() {
        guard more, !loading, let q = MessageSearchRules.query(query), let before = hits.map(\.id).min() else { return }
        loading = true
        let g = generation
        task = Task { [weak self] in await self?.fetch(q, before: before, generation: g) }
    }

    private func fetch(_ q: String, before: Int64?, generation g: Int) async {
        guard let api = api() else {
            if g == generation { loading = false; failed = true }
            return
        }
        do {
            let page = MessageHit.parseList(try await api.request("GET", MessageSearchRules.searchPath(query: q, before: before)))
            guard g == generation else { return }
            hits = before == nil ? page : MessageSearchRules.appendPage(hits, page)
            more = MessageSearchRules.nextBefore(page) != nil
            failed = false
        } catch {
            guard g == generation, !(error is CancellationError) else { return }
            failed = true
        }
        if g == generation { loading = false }
    }
}
