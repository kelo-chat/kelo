import Foundation
import KeloCore
import KeloShared

// Loading the history: the first page, older pages, news from another phone, the briefing cards, and finding a message's row.

extension ChatModel {
    // ---- history -----------------------------------------------------------------------------------------------------------

    public func reload() {
        Task { [weak self] in await self?.refreshing { try await self?.reloadNow() } }
    }

    private func fetch(_ path: String) async throws -> [ChatMessage] {
        guard let api = api() else { throw ApiError(status: 401, detail: "signed out") }
        return ChatMessage.parseList(try await api.request("GET", path))
    }

    /// `afterReply`: a reply just ended and its rows are on screen; each message is swapped for its stored version in place
    /// (`ChatReducer.mergeStored`), so the tidier stored text, the answer buttons and cards come in and nothing else moves.
    func reloadNow(afterReply: Bool = false) async throws {
        let ms = try await fetch("/api/messages?limit=\(Self.page)")
        app.cache.put("messages", ChatHistory.trim(ms))
        if busy { return }   // a reply started meanwhile: its own items are on screen
        lastId = ms.last?.id ?? 0
        items = afterReply ? ChatReducer.mergeStored(items, ms.map(ChatReducer.item)) : ms.map(ChatReducer.item)
        secrets = SecretRules.settled(secrets)
        refreshAsks(force: true)   // a location card still waiting (it is not a stored message) comes back at the end
        loaded = true
        if !afterReply { hasOlder = ms.count >= Self.page }
        offline = false
        await loadFollowupsNow()
        await refreshSecretsNow()
    }

    /// A server that cannot be reached leaves what is shown and only marks it as possibly old.
    private func refreshing(_ block: () async throws -> Void) async {
        do {
            try await block()
            if offline { offline = false }
        } catch is CancellationError {
        } catch let e as ApiError {
            if !e.isUnauthorized { offline = true }
        } catch {
            offline = true
        }
    }

    /// Older messages when the list is scrolled to its top (before_id).
    public func loadOlder() {
        guard hasOlder, !loadingOlder, let first = items.lazy.compactMap({ $0.asMsg?.id }).first else { return }
        loadingOlder = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.loadingOlder = false }
            guard let ms = try? await self.fetch("/api/messages?limit=\(Self.page)&before_id=\(first)") else { return }
            let known = Set(self.items.map(\.key))
            self.items = ms.map(ChatReducer.item).filter { !known.contains($0.key) } + self.items
            self.hasOlder = ms.count >= Self.page
        }
    }

    /// The chat history was cleared on the server (마이 → 대화 기록 삭제): the rows on screen go, then the (empty) history is asked again.
    public func forgetHistory() {
        lastId = 0
        items = []
        hasOlder = false
        followups = [:]
        asks = []
        secrets = []
        reload()
    }

    /// Another phone may have talked to the same assistant: while idle and in view, reload when the newest message changed.
    public func checkForNews() async {
        if busy { return }
        await refreshing {
            let ms = try await fetch("/api/messages?limit=1")
            if (ms.last?.id ?? 0) != lastId && !busy {
                app.activity.touch()   // a new message came in: the character wakes up
                try await reloadNow()
            }
        }
    }

    /// After a reply: note the newest id, and keep the newest page for the next open.
    func remember() {
        Task { [weak self] in
            guard let self, let ms = try? await self.fetch("/api/messages?limit=\(Self.page)") else { return }
            self.lastId = ms.last?.id ?? 0
            self.app.cache.put("messages", ChatHistory.trim(ms))
        }
    }

    /// The briefing cards on screen, asked for again when GET /api/agent/status "briefing_rev" changes (its hours line answered on
    /// another device, or here): the card follows without a reload (Android ChatViewModel.refreshBriefings).
    public func refreshBriefings() {
        guard items.contains(where: { $0.asMsg?.briefing != nil }) else { return }
        Task { [weak self] in
            guard let self, let ms = try? await self.fetch("/api/messages?limit=\(Self.page)") else { return }
            var fresh: [Int64: BriefingCard] = [:]
            for m in ms { if let b = m.briefing { fresh[m.id] = b } }
            self.items = ChatReducer.withBriefings(self.items, fresh)
        }
    }

    // ---- scrolling to a message --------------------------------------------------------------------------------------------

    /// The row key of the stored message `id`, if it is in the list.
    public func rowKey(forMessage id: Int64) -> String? { items.first { $0.asMsg?.id == id }?.key }
}
