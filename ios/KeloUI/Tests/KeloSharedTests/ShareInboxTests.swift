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
