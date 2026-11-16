import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

/// My → Nest → Made here: the list, Share (202 / 409), Delete, and the live reload.
@MainActor @Suite struct NestMadeModelTests {
    private let drafts = """
    {"items":[
      {"id":"old-one","kind":"skill","name":"Old one","version":"0.0.2","updated_at":100.0,"shared":null},
      {"id":"reading","kind":"extension","name":"Reading tab","version":"0.0.5","updated_at":300.0,"shared":{"repo":"me/kelo-reading","commit":"\(String(repeating: "a", count: 40))","at":250}},
      {"id":"writer","kind":"skill","name":"","version":"0.0.1","updated_at":200.0,"shared":null}]}
    """
    private let skills = """
    {"items":[
      {"id":"mine","name":"My review skill","description":"d","source":"custom","state":"ready","created_at":5.0},
      {"id":"writer","name":"Writer","description":"d","source":"custom","state":"ready","created_at":4.0},
      {"id":"builtin","name":"Built in","description":"d","source":"builtin","state":"ready"}]}
    """

    private func transport() -> RoutedTransport {
        let t = RoutedTransport()
        t.on("GET /api/nest/drafts", drafts)
        t.on("GET /api/skills", skills)
        return t
    }

    @Test func theListHasDraftsNewestFirstThenCustomSkillsThatAreNotDraftsYet() async {
        let m = NestMadeModel(api: transport().api())
        await m.load()
        #expect(m.items.map(\.id) == ["reading", "writer", "old-one", "mine"])
        let reading = m.items[0]
        #expect(reading.kind == .extensionKind && reading.version == "0.0.5" && reading.draft && reading.shared)
        #expect(reading.repoUrl == "https://github.com/me/kelo-reading")
        #expect(m.items[1].title == "writer")   // no name: the id
        #expect(!m.items[2].shared && m.items[2].repoUrl == nil)
        #expect(m.items[3].kind == .customSkill && !m.items[3].draft)
        #expect(!m.failed && m.loaded)
    }

    @Test func anOlderServerWithoutDraftsStillShowsItsCustomSkills() async {
        let t = transport(); t.on("GET /api/nest/drafts", 404, #"{"detail":"none"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        #expect(m.items.map(\.id) == ["mine", "writer"] && !m.failed)
    }

    @Test func nothingReadableMarksTheListFailed() async {
        let t = RoutedTransport(); t.on("GET /api/nest/drafts", 500, #"{"detail":"boom"}"#); t.on("GET /api/skills", 500, #"{"detail":"boom"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        #expect(m.failed && m.items.isEmpty && m.loaded)
    }

    @Test func aRepositoryNameThatIsNotOwnerSlashNameGivesNoLink() {
        #expect(MadeHereRules.repoUrl("javascript:alert(1)") == nil)
        #expect(MadeHereRules.repoUrl("https://evil.example/x/y") == nil)
        #expect(MadeHereRules.repoUrl("a b/c") == nil)
        #expect(MadeHereRules.repoUrl("o/n.x") == "https://github.com/o/n.x")
    }

    @Test func share202OpensTheChatOnThatTurn() async {
        let t = transport(); t.on("POST /api/nest/drafts/reading/share", 202, #"{"turn_id":"turn-7"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.share(m.items.first { $0.id == "reading" }!)
        #expect(m.nextEffect() == .openChat("turn-7"))
        #expect(m.connectFor == nil && m.busy.isEmpty)
    }

    @Test func share409WithoutGithubWriteAccessOffersToConnectAndThenSharesAgain() async {
        let t = transport()
        t.on("POST /api/nest/drafts/reading/share") { _, n in
            n == 0 ? (409, #"{"code":"github_write_needed","detail":"github write access needed"}"#) : (202, #"{"turn_id":"turn-8"}"#)
        }
        let m = NestMadeModel(api: t.api())
        await m.load()
        let item = m.items.first { $0.id == "reading" }!
        await m.share(item)
        #expect(m.connectFor == item && m.nextEffect() == nil && m.message == nil)
        // GitHub gets connected (the existing connector flow), the sheet's button shares again
        await m.retryShare()
        #expect(t.count("POST /api/nest/drafts/reading/share") == 2)
        #expect(m.nextEffect() == .openChat("turn-8") && m.connectFor == nil)
    }

    @Test func aSheetCanBeDismissedWithoutSharing() async {
        let t = transport(); t.on("POST /api/nest/drafts/reading/share", 409, #"{"code":"github_write_needed","detail":"x"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.share(m.items[0])
        #expect(m.connectFor != nil)
        m.dismissConnect()
        await m.retryShare()
        #expect(m.connectFor == nil && t.count("POST /api/nest/drafts/reading/share") == 1)
    }

    @Test func anOtherFailureOfShareSaysWhy() async {
        let t = transport(); t.on("POST /api/nest/drafts/reading/share", 404, #"{"detail":"draft 'reading' not found"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.share(m.items[0])
        #expect(m.message == "draft 'reading' not found" && m.connectFor == nil && m.nextEffect() == nil)
    }

    @Test func sharingACustomSkillFirstMakesItADraftThenSharesIt() async {
        let t = transport()
        t.on("POST /api/nest/drafts/from-skill/mine", #"{"ok":true,"id":"mine","kind":"skill","version":"0.0.1"}"#)
        t.on("POST /api/nest/drafts/mine/share", 202, #"{"turn_id":"turn-3"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.share(m.items.first { $0.id == "mine" }!)
        let posts = t.calls.filter { $0.method == "POST" }.map(\.path)
        #expect(posts == ["/api/nest/drafts/from-skill/mine", "/api/nest/drafts/mine/share"])
        #expect(m.nextEffect() == .openChat("turn-3"))
    }

    @Test func aSkillAlreadyCopiedToADraftIsStillShared() async {
        let t = transport()
        t.on("POST /api/nest/drafts/from-skill/mine", 409, #"{"detail":"Draft 'mine' already exists"}"#)
        t.on("POST /api/nest/drafts/mine/share", 202, #"{"turn_id":"turn-4"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.share(m.items.first { $0.id == "mine" }!)
        #expect(m.nextEffect() == .openChat("turn-4"))
    }

    @Test func deletingADraftCallsItsEndpointAndTheListLosesIt() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/drafts") { _, n in (200, n == 0 ? self.drafts : #"{"items":[{"id":"reading","kind":"extension","name":"Reading tab","version":"0.0.5","updated_at":300.0},{"id":"writer","kind":"skill","name":"","version":"0.0.1","updated_at":200.0}]}"#) }
        t.on("GET /api/skills", skills)
        t.on("DELETE /api/nest/drafts/old-one", #"{"ok":true}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.delete(m.items.first { $0.id == "old-one" }!)
        #expect(t.count("DELETE /api/nest/drafts/old-one") == 1)
        #expect(m.items.map(\.id) == ["reading", "writer", "mine"] && m.busy.isEmpty)
    }

    @Test func deletingACustomSkillCallsTheSkillsEndpoint() async {
        let t = transport(); t.on("DELETE /api/skills/custom/mine", #"{"ok":true}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.delete(m.items.first { $0.id == "mine" }!)
        #expect(t.count("DELETE /api/skills/custom/mine") == 1)
    }

    @Test func aRefusedDeleteSaysWhyAndKeepsTheItem() async {
        let t = transport(); t.on("DELETE /api/nest/drafts/old-one", 403, #"{"detail":"Only the owner"}"#)
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.delete(m.items.first { $0.id == "old-one" }!)
        #expect(m.message == "Only the owner" && m.items.contains { $0.id == "old-one" } && m.busy.isEmpty)
    }

    @Test func nestChangedReloadsTheList() async {
        let t = transport()
        let m = NestMadeModel(api: t.api())
        await m.load()
        await m.changed()
        #expect(t.count("GET /api/nest/drafts") == 2)
    }
}
