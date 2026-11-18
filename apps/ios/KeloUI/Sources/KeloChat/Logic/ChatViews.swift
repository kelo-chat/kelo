import Foundation
import KeloCore

// The views the server shows in the chat (GET /api/chat/views, chat_views.py): a browser tab or the phone is a card in the chat until
// the main agent or the owner closes it, or it is left untouched for ten minutes. Android `ChatViews.kt` / `applyViews` is the same.

/// The key the phone's view has (its live source and its card's tab).
let phoneViewKey = "android"

/// Field reads of a JSON object (the accessors of `Lenient` in KeloCore are not reachable from here).
private extension Dictionary where Key == String, Value == JSON {
    func str(_ k: String, _ def: String = "") -> String { self[k]?.text ?? def }
    func int(_ k: String, _ def: Int = 0) -> Int { self[k]?.intValue ?? def }
    func bool(_ k: String, _ def: Bool = false) -> Bool { self[k]?.boolValue ?? def }
    func double(_ k: String, _ def: Double = 0) -> Double { self[k]?.doubleValue ?? def }
}

/// One browser tab or the phone the server shows in the chat. `key`: the tab ("owner", a device id, "task:…") or "android" for the phone;
/// `kind` "browser" or "phone"; `anchorId`: the stored message the newest card goes after.
public struct ChatViewItem: Equatable, Sendable {
    public var key: String, kind: String, workerId: String
    public var ownerAsked: Bool
    public var anchorId: Int64
    public var title: String, url: String, host: String, status: String
    public var image: String?
    public var width: Int, height: Int

    static func parse(_ o: JSONObject) -> ChatViewItem {
        let image = o.str("image")
        return ChatViewItem(key: o.str("key"), kind: o.str("kind", "browser"), workerId: o.str("worker_id"), ownerAsked: o.bool("owner_asked"),
                            anchorId: Int64(o.int("anchor_id")), title: o.str("title"), url: o.str("url"), host: o.str("host"),
                            status: o.str("status"), image: image.hasPrefix("data:image/") ? image : nil,
                            width: o.int("width"), height: o.int("height"))
    }
}

/// The answer of GET /api/chat/views: `unchanged` when the `since` version is still the newest (no pictures sent); `at` the server's clock.
public struct ChatViewsAnswer: Equatable, Sendable {
    public var version: Int, unchanged: Bool, items: [ChatViewItem], at: Double

    static func parse(_ j: JSON) -> ChatViewsAnswer {
        let o = j.objectValue ?? [:]
        let items = (o["items"]?.arrayValue ?? []).compactMap { $0.objectValue }.map(ChatViewItem.parse)
        return ChatViewsAnswer(version: o.int("version"), unchanged: o.bool("unchanged"), items: items, at: o.double("at"))
    }
}

extension ChatReducer {
    /// The rows after the server's views are compared with the shown rows (Android `applyViews`): (a) a card whose view is listed takes the
    /// view's words and picture; (b) a card whose view is gone goes when it is older than the answer (`checkedAt` minus 5 s); (c) when
    /// `insert`, a view with no card gets one after its anchor message (the top or the end when that message is not on the page).
    public static func applyViews(_ items: [ChatItem], _ views: [ChatViewItem], checkedAt: Double, insert: Bool = true) -> [ChatItem] {
        var byKey: [String: ChatViewItem] = [:]
        for v in views where byKey[v.key] == nil { byKey[v.key] = v }
        var present = Set<String>()
        var out: [ChatItem] = []
        for item in items {
            guard case .browser(var b) = item else { out.append(item); continue }
            let key = b.device ? phoneViewKey : b.tab
            if let v = byKey[key] {
                present.insert(key)
                b.title = v.title.isEmpty ? b.title : v.title
                b.host = v.host.isEmpty ? b.host : v.host
                b.status = v.status.isEmpty ? b.status : v.status
                if let image = v.image { b.image = image; b.width = v.width; b.height = v.height }
                out.append(.browser(b))
            } else if b.at > 0 && b.at < checkedAt - 5 {
                continue   // closed on the server: its card goes
            } else {
                out.append(item)
            }
        }
        guard insert else { return out }
        for v in views where !present.contains(v.key) {
            let card = ChatItem.Browser(key: "view:\(v.key)", id: "", title: v.title, host: v.host, status: v.status, image: v.image,
                                        width: v.width, height: v.height, tab: v.kind == "phone" ? phoneViewKey : v.key,
                                        device: v.kind == "phone", at: checkedAt)
            out.insert(.browser(card), at: viewCardPosition(out, v.anchorId))
        }
        return out
    }

    /// Where a view's card goes: after the stored message `anchorId`; the end when the id is unknown and newer than the page.
    private static func viewCardPosition(_ out: [ChatItem], _ anchorId: Int64) -> Int {
        let msgs: [(index: Int, id: Int64)] = out.enumerated().compactMap { i, item in item.asMsg?.id.map { (index: i, id: $0) } }
        guard anchorId > 0, let last = msgs.last else { return out.count }
        if let m = msgs.first(where: { $0.id == anchorId }) { return m.index + 1 }
        if anchorId > last.id { return out.count }
        return (msgs.last(where: { $0.id < anchorId })?.index ?? -1) + 1
    }
}
