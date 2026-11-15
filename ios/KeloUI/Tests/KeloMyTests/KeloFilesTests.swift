import Testing
import Foundation
import KeloCore
@testable import KeloMy

/// Kelo's files: the rules and the answers' shapes (Android my/KeloFilesTest.kt).
struct KeloFilesTests {
    private func f(_ p: String) -> HomeEntry { HomeEntry(path: p) }
    private func d(_ p: String) -> HomeEntry { HomeEntry(path: p, type: "dir") }

    @Test func theTopShowsFoldersThenCoreFilesInOrderThenTheRest() {
        let all = [f("user.md"), f("zeta.md"), d("memory"), f("assistant.md"), f("memory/a.md"), d("notes"), f("Alpha.md"), f("MEMORY.md")]
        #expect(KeloFileRules.children(all, folder: "").map(\.path) == ["memory", "notes", "assistant.md", "user.md", "MEMORY.md", "Alpha.md", "zeta.md"])
    }

    @Test func aFolderShowsOnlyItsDirectChildren() {
        let all = [d("memory"), f("memory/b.md"), d("memory/people"), f("memory/people/x.md"), f("memory/a.md"), f("other/c.md")]
        #expect(KeloFileRules.children(all, folder: "memory").map(\.path) == ["memory/people", "memory/a.md", "memory/b.md"])
    }

    @Test func deleteAndNewFolderRules() {
        #expect(KeloFileRules.canDelete("memory/a.md"))
        #expect(!KeloFileRules.canDelete("memory"))
        #expect(!KeloFileRules.canDelete("user.md"))
        #expect(!KeloFileRules.canMakeFolder(""))
        #expect(KeloFileRules.canMakeFolder("notes"))
        #expect(KeloFileRules.canMakeFolder("notes/x"))
        #expect(!KeloFileRules.canMakeFolder("uploads"))
    }

    @Test func folderNames() {
        #expect(KeloFileRules.folderName(" a ") == "a")
        #expect(KeloFileRules.folderName("a/b") == nil)
        #expect(KeloFileRules.folderName(".x") == nil)
        #expect(KeloFileRules.folderName("") == nil)
    }

    @Test func eachPartIsSegmentedAndTheSlashesStay() {
        #expect(KeloFileRules.apiPath("memory/people/김 철수.md") == "memory/people/%EA%B9%80%20%EC%B2%A0%EC%88%98.md")
    }

    @Test func aFilesRowDecodesWithItsType() throws {
        let rows = try JSONDecoder().decode([HomeEntry].self, from: Data(#"[{"path":"memory","type":"dir","bytes":null,"writable":true},{"path":"user.md","type":"file","bytes":12,"writable":true}]"#.utf8))
        #expect(rows[0].isDir && rows[0].name == "memory" && rows[0].writable)
        #expect(!rows[1].isDir && rows[1].bytes == 12)
    }

    @Test func aTrashedRowCountsItsDays() throws {
        let t = try JSONDecoder().decode(HomeTrashed.self, from: Data(#"{"id":"a","path":"notes/x.md","bytes":3,"deleted_at":1000,"type":"file","purge_at":173800}"#.utf8))
        #expect(t.daysLeft(now: Date(timeIntervalSince1970: 1000)) == 2)
        #expect(t.daysLeft(now: Date(timeIntervalSince1970: 999_999)) == 0)
    }
}
