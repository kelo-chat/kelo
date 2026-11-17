import Foundation
import KeloCore

// The rules a search hit adds to the chat's rows: the window GET /api/messages/around gives (and whether more lies beyond each end),
// and the messages after a window when it is followed down (Android chat/ChatSearch.kt makes the same decisions).

public enum ChatSearchRules {
    /// How long a hit stays marked before it fades.
    public static let markNanos: UInt64 = 1_500_000_000

    /// A window around a hit, oldest first. `hasOlder` / `hasNewer`: a full side came, so messages may follow beyond it.
    public struct Window: Equatable, Sendable {
        public var rows: [ChatMessage]
        public var hasOlder: Bool
        public var hasNewer: Bool
        public init(rows: [ChatMessage], hasOlder: Bool, hasNewer: Bool) {
            self.rows = rows; self.hasOlder = hasOlder; self.hasNewer = hasNewer
        }
    }

    public static func window(_ ms: [ChatMessage], hit: Int64, n: Int = MessageSearchRules.windowSide) -> Window {
        Window(rows: ms.sorted { $0.id < $1.id },
               hasOlder: ms.filter { $0.id < hit }.count >= n,
               hasNewer: ms.filter { $0.id > hit }.count >= n)
    }

    /// The messages after `last` from a fetch around it (`n` = `newerPage`), and whether a full page came (more may follow).
    public static func newer(_ ms: [ChatMessage], after last: Int64, page: Int = MessageSearchRules.newerPage) -> (rows: [ChatMessage], more: Bool) {
        let rows = ms.filter { $0.id > last }.sorted { $0.id < $1.id }
        return (rows, rows.count >= page)
    }
}
