import Foundation

/// What the "Send to Kelo" share extension left for the app (Android core/Share.kt ShareInbox): text for the chat's draft and/or one
/// file to upload like the "+" button does. The extension writes it into the App Group container; the chat takes it when it shows.
/// Without the App Group (an unsigned simulator build) there is no container and nothing is ever waiting.
public enum ShareInbox {
    public static let appGroup = "group.chat.kelo.app"
    public static let folderName = "ShareInbox"
    public static let maxText = 20_000

    public struct Item: Equatable, Sendable {
        public var text: String
        public var file: URL?
        public init(text: String, file: URL?) { self.text = text; self.file = file }
    }

    static func folder(_ base: URL? = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)) -> URL? {
        base?.appendingPathComponent(folderName, isDirectory: true)
    }

    /// Text first, the subject in front when the body does not start with it (Android ShareParse), at most `maxText` characters.
    public static func text(body: String?, subject: String?) -> String {
        let b = (body ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let h = (subject ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let t = !h.isEmpty && !b.hasPrefix(h) ? (b.isEmpty ? h : h + "\n" + b) : b
        return String(t.prefix(maxText))
    }

    /// Takes what is waiting (and removes it): the file is moved to a temporary place the upload deletes afterwards.
    public static func take(base: URL? = nil) -> Item? {
        guard let dir = base.map({ $0.appendingPathComponent(folderName, isDirectory: true) }) ?? folder() else { return nil }
        let index = dir.appendingPathComponent("inbox.json")
        guard let data = try? Data(contentsOf: index),
              let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        try? FileManager.default.removeItem(at: index)
        let text = String(((o["text"] as? String) ?? "").prefix(maxText))
        var moved: URL?
        if let name = o["file"] as? String, !name.isEmpty, !name.contains("/") {
            let src = dir.appendingPathComponent(name)
            let dst = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "-" + name)
            if (try? FileManager.default.moveItem(at: src, to: dst)) != nil { moved = dst }
        }
        if text.isEmpty && moved == nil { return nil }
        return Item(text: text, file: moved)
    }

    /// What the extension does (also used by tests): one waiting share replaces an older one.
    public static func put(text: String, file: URL?, base: URL? = nil) throws {
        guard let dir = base.map({ $0.appendingPathComponent(folderName, isDirectory: true) }) ?? folder() else { return }
        try? FileManager.default.removeItem(at: dir)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var o: [String: Any] = ["text": String(text.prefix(maxText)), "at": Date().timeIntervalSince1970]
        if let file {
            let name = file.lastPathComponent
            try FileManager.default.copyItem(at: file, to: dir.appendingPathComponent(name))
            o["file"] = name
        }
        try JSONSerialization.data(withJSONObject: o).write(to: dir.appendingPathComponent("inbox.json"), options: .atomic)
    }
}
