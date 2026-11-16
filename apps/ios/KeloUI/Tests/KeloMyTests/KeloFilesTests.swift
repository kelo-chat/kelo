import Testing
import Foundation
import KeloCore
@testable import KeloShared
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

    @Test func theSkillsListKeepsOnlyCustomOnesByNameOrNewestFirst() throws {
        let rows = try JSONDecoder().decode(CustomSkillList.self, from: Data(#"""
        {"items":[{"id":"research","name":"Research","source":"builtin","state":"ready"},
                  {"id":"nest-one","name":"Nest one","source":"installed","state":"ready"},
                  {"id":"tidy-inbox","name":"Tidy inbox","description":"Tidy.","source":"custom","state":"ready"},
                  {"id":"a-held","name":"A held one","source":"custom","state":"held"}]}
        """#.utf8)).items
        let list = CustomSkillRow.forList(rows)
        #expect(list.map(\.id) == ["a-held", "tidy-inbox"])
        #expect(list[0].held && !list[1].held)
        let dated = [CustomSkillRow(id: "old", createdAt: 1), CustomSkillRow(id: "new", createdAt: 9)]
        #expect(CustomSkillRow.forList(dated).map(\.id) == ["new", "old"])
    }
}

/// A server with two custom skills (and a built-in one); GET /api/skills can be made to fail.
private final class SkillsTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var custom = ["tidy-inbox", "a-held"]
    private var failSkills = false
    func setFail(_ v: Bool) { lock.withLock { failSkills = v } }

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        let path = request.url!.path, method = request.httpMethod ?? "GET"
        let (ids, fail) = lock.withLock { (custom, failSkills) }
        if method == "GET" && path == "/api/files" { return (200, Data(#"[{"path":"user.md","type":"file","bytes":3,"writable":true}]"#.utf8)) }
        if method == "GET" && path == "/api/skills" {
            if fail { return (500, Data(#"{"detail":"boom"}"#.utf8)) }
            let rows = ids.map { #"{"id":"\#($0)","name":"\#($0)","description":"d","source":"custom","state":"\#($0 == "a-held" ? "held" : "ready")"}"# }
            return (200, Data(#"{"items":[{"id":"research","name":"Research","source":"builtin","state":"ready"},\#(rows.joined(separator: ","))]}"#.utf8))
        }
        if method == "GET" && path == "/api/skills/custom/tidy-inbox" {
            return (200, Data(#"{"id":"tidy-inbox","name":"Tidy inbox","description":"d","text":"Steps\n1. Sort.","created_at":5,"state":"ready"}"#.utf8))
        }
        if method == "DELETE" && path.hasPrefix("/api/skills/custom/") {
            let id = String(path.split(separator: "/").last ?? "")
            lock.withLock { custom.removeAll { $0 == id } }
            return (200, Data(#"{"ok":true}"#.utf8))
        }
        return (404, Data(#"{"detail":"no"}"#.utf8))
    }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try await send(request)
        return (code, AsyncThrowingStream { c in for b in data { c.yield(b) }; c.finish() })
    }
}

/// The skills Kelo made, in Kelo's files (Android KeloFilesViewModelTest): the row needs a custom skill; open one; delete -> reload.
@Suite(.serialized) struct KeloFilesSkillsTests {
    @MainActor @Test func theRowShowsOnlyCustomSkillsAndASkillsFailureLeavesTheFilesWorking() async {
        let t = SkillsTransport()
        let m = KeloFilesModel(api: KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t))
        await m.load()
        #expect(m.entries?.count == 1 && !m.failed)
        #expect(m.skills.map(\.id) == ["a-held", "tidy-inbox"])      // by name; the built-in one never
        t.setFail(true)
        await m.load()
        #expect(m.entries?.count == 1 && !m.failed && m.skills.isEmpty)   // the row hides, the page stays
    }

    @MainActor @Test func openingASkillReadsItsTextAndDeletingReloadsTheList() async {
        let t = SkillsTransport()
        let m = KeloFilesModel(api: KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t))
        await m.load()
        await m.openSkill("tidy-inbox")
        #expect(m.skill?.text == "Steps\n1. Sort.")
        var last: Bool?
        await m.deleteSkill("tidy-inbox") { last = $0 }
        #expect(last == false && m.skills.map(\.id) == ["a-held"] && m.skill == nil)
        await m.deleteSkill("a-held") { last = $0 }
        #expect(last == true && m.skills.isEmpty)
    }
}
