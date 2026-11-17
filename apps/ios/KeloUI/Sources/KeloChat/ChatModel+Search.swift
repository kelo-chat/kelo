import Foundation
import KeloCore
import KeloShared

// Search across the chat history, from the chat's side: opening a hit (scrolled to and marked when it is loaded; otherwise its window
// replaces the rows), following a window down to the newest message, and leaving a window for the newest page.

extension ChatModel {
    /// A search hit was opened (Android ChatHistory.openAt). A message already in the list is scrolled to; any other comes with the
    /// messages around it, which replace the rows (`hasNewer`: more follows below; `hasOlder`: more is above). Nothing changes when a
    /// reply is being written, when the message is unknown or hidden, or when the server did not answer: the chat stays where it is.
    public func openSearchHit(_ id: Int64) async {
        let loaded = Set(items.compactMap { $0.asMsg?.id })
        if MessageSearchRules.opening(id, loaded: loaded) == .fetchWindow {
            guard !busy, let api = api() else { return }
            do {
                let ms = ChatMessage.parseList(try await api.request("GET", MessageSearchRules.aroundPath(id: id)))
                guard ms.contains(where: { $0.id == id }), !busy else { return }
                let w = ChatSearchRules.window(ms, hit: id)
                items = w.rows.map(ChatReducer.item)
                hasOlder = w.hasOlder
                hasNewer = w.hasNewer
            } catch {
                return
            }
        }
        focus(id)
    }

    /// The hit is scrolled to by the list (`focusRequest`) and marked for a moment.
    func focus(_ id: Int64) {
        focusTarget = id
        focusRequest += 1
        markedMessage = id
        markTask?.cancel()
        markTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: ChatSearchRules.markNanos)
            guard !Task.isCancelled else { return }
            self?.markedMessage = nil
        }
    }

    /// The end of a search window came into view: the messages after its newest one (`newerPage` at a time). A short answer means
    /// the newest message is in: the window is then the normal history again.
    public func loadNewer() {
        guard hasNewer, !loadingNewer, !busy, let last = items.compactMap({ $0.asMsg?.id }).max() else { return }
        loadingNewer = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.loadingNewer = false }
            guard let api = self.api() else { return }
            do {
                let ms = ChatMessage.parseList(try await api.request("GET", MessageSearchRules.aroundPath(id: last, n: MessageSearchRules.newerPage)))
                let w = ChatSearchRules.newer(ms, after: last)
                let known = Set(self.items.map(\.key))
                self.items += w.rows.map(ChatReducer.item).filter { !known.contains($0.key) }
                self.hasNewer = w.more
                self.offline = false
            } catch is CancellationError {
            } catch {
                self.offline = true
            }
        }
    }

    /// Drops a search window for the newest page ("↓", or a message sent from the window). `next` runs after the page is in (or
    /// failed: then the window stays, without the messages after it).
    public func leaveWindow(then next: (@MainActor () -> Void)? = nil) {
        guard hasNewer else { next?(); return }
        // cleared first: `next` (a send) must not find the window again; a failed page then leaves the window as it is
        hasNewer = false
        Task { [weak self] in
            guard let self else { return }
            await self.refreshing { try await self.reloadNow() }
            next?()
        }
    }
}
