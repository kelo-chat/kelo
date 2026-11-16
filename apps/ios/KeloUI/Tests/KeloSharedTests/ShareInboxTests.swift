import Testing
import Foundation
@testable import KeloShared

@Suite struct ShareInboxTests {
    @Test func putThenTakeOnce() throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-share-\(UUID().uuidString)")
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("report-\(UUID().uuidString).pdf")
        try Data("pdf".utf8).write(to: file)
        try ShareInbox.put(text: "look at this", file: file, base: base)
        let got = try #require(ShareInbox.take(base: base))
        #expect(got.text == "look at this")
        #expect(got.file.map { (try? Data(contentsOf: $0)) == Data("pdf".utf8) } == true)
        #expect(ShareInbox.take(base: base) == nil)
        #expect(ShareInbox.text(body: "body", subject: "Title") == "Title\nbody")
        #expect(ShareInbox.text(body: "Title and more", subject: "Title") == "Title and more")
    }
}

/// What the share extension writes (the extension has its own copy); the tests use it to leave something waiting.
extension ShareInbox {
    /// Text first, the subject in front when the body does not start with it (Android ShareParse), at most `maxText` characters.
    static func text(body: String?, subject: String?) -> String {
        let b = (body ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let h = (subject ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let t = !h.isEmpty && !b.hasPrefix(h) ? (b.isEmpty ? h : h + "\n" + b) : b
        return String(t.prefix(maxText))
    }

    /// What the extension does: one waiting share replaces an older one.
    static func put(text: String, file: URL?, base: URL? = nil) throws {
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
