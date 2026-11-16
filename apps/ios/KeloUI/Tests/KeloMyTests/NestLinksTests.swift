import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

/// My → Nest → Install from a link: the rules, Look, Install (with the permissions the owner saw), the installed list, Update, Remove.
@MainActor @Suite struct NestLinksTests {
    private let card = #"""
    {"token":"tok-1","id":"reader","name":"Reader","description":"Reads things","kind":"skill","version":"1.0.0","author":"me",
     "repo":"me/reader","installed":null,
     "permissions":{"hash":"h-1","connect":"","words":[{"key":"net","text":"Reads the web","level":"caution"}]}}
    """#

    private let installedRow = #"""
    {"id":"reader","version":"1.0.0","name":"Reader","description":"d","author":"me","repo":"me/reader","enabled":true,"state":"on",
     "pinned":false,"permission_words":[{"key":"net","text":"Reads the web","level":"caution"}],"update":null}
    """#

    private let rowsWithoutNewPerms = #"""
    [{"id":"reader","version":"1.0.0","name":"Reader","repo":"me/reader","enabled":true,"state":"on","pinned":false,
      "permission_words":[],"update":{"version":"1.1.0","commit":"def","widened":[],"narrowed":[],"permissions_hash":"h-2","words":[],"pinned":false}}]
    """#

    private let rowsWithMorePerms = #"""
    [{"id":"reader","version":"1.0.0","name":"Reader","repo":"me/reader","enabled":true,"state":"on","pinned":false,
      "permission_words":[],"update":{"version":"1.1.0","commit":"def","widened":["fs"],"narrowed":[],"permissions_hash":"h-2",
      "words":[{"key":"fs","text":"Writes files","level":"caution"}],"pinned":false}}]
    """#

    private func transport() -> RoutedTransport {
        let t = RoutedTransport()
        t.on("GET /api/nest/installed", "[\(installedRow)]")
        return t
    }

    /// A string field of the body of the last call on `route` ("METHOD /path"), nil when there is none.
    private func field(_ t: RoutedTransport, _ route: String, _ key: String) -> String? {
        guard let call = t.calls.last(where: { "\($0.method) \($0.path)" == route }) else { return nil }
        return (try? JSON.parse(call.body))?[key]?.stringValue
    }

    @Test func aPastedLinkIsCleanedAndOnlyAGitHubRepositoryLooksRight() {
        #expect(NestLinkRules.clean("  github.com/me/reader.git ") == "https://github.com/me/reader")
        #expect(NestLinkRules.clean("https://github.com/me/reader") == "https://github.com/me/reader")
        #expect(NestLinkRules.looksRight("https://github.com/me/reader/tree/main"))
        #expect(!NestLinkRules.looksRight("https://gitlab.com/me/reader"))
        #expect(!NestLinkRules.looksRight("hello there"))
    }

    @Test func aBadLinkIsRefusedBeforeAnyCall() async {
        let t = RoutedTransport()
        let m = NestLinksModel(api: t.api())
        m.link = "https://gitlab.com/me/reader"
        await m.check()
        #expect(m.problem == .notLink && m.card == nil && t.calls.isEmpty)
    }

    @Test func lookSendsTheCleanedLinkAndShowsTheCard() async {
        let t = RoutedTransport(); t.on("POST /api/nest/install-from-link", card)
        let m = NestLinksModel(api: t.api())
        m.link = "github.com/me/reader.git"
        await m.check()
        #expect(field(t, "POST /api/nest/install-from-link", "url") == "https://github.com/me/reader")
        #expect(m.card?.token == "tok-1" && m.card?.permissions.hash == "h-1" && m.problem == nil && !m.checking)
        #expect(m.card?.permissions.words.first?.level == "caution")
    }

    @Test func aRepositoryWithoutAPackageSaysSo() async {
        let t = RoutedTransport(); t.on("POST /api/nest/install-from-link", 422, #"{"detail":"not a package"}"#)
        let m = NestLinksModel(api: t.api())
        m.link = "https://github.com/me/notes"
        await m.check()
        #expect(m.problem == .notPackage && m.card == nil)
    }

    @Test func installPostsTheTokenAndTheHashTheOwnerSawThenReadsTheList() async {
        let t = transport(); t.on("POST /api/nest/install-from-link", card); t.on("POST /api/nest/install-from-link/confirm", installedRow)
        let m = NestLinksModel(api: t.api())
        m.link = "github.com/me/reader"
        await m.check()
        await m.install()
        #expect(field(t, "POST /api/nest/install-from-link/confirm", "token") == "tok-1")
        #expect(field(t, "POST /api/nest/install-from-link/confirm", "accept") == "h-1")
        #expect(m.card == nil && m.link == "" && m.problem == nil && !m.installing)
        #expect(m.installed.map(\.id) == ["reader"])
        #expect(t.count("GET /api/nest/installed") == 1)
    }

    @Test func aLinkThatTookTooLongIsExpiredAndNothingIsInstalled() async {
        let t = transport(); t.on("POST /api/nest/install-from-link", card)
        t.on("POST /api/nest/install-from-link/confirm", 410, #"{"detail":"expired","code":"link_expired"}"#)
        let m = NestLinksModel(api: t.api())
        m.link = "github.com/me/reader"
        await m.check()
        await m.install()
        #expect(m.problem == .expired && m.card == nil)
        #expect(t.count("GET /api/nest/installed") == 0)
    }

    @Test func aRepositoryThatChangedWhileLookingAsksToLookAgain() async {
        let t = transport(); t.on("POST /api/nest/install-from-link", card)
        t.on("POST /api/nest/install-from-link/confirm", 409, #"{"detail":"changed","code":"changed"}"#)
        let m = NestLinksModel(api: t.api())
        m.link = "github.com/me/reader"
        await m.check()
        await m.install()
        #expect(m.problem == .changed && m.card == nil)
    }

    @Test func anUpdateThatAsksForNothingNewGoesStraightThrough() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/installed", rowsWithoutNewPerms)
        t.on("POST /api/nest/packages/reader/update", #"{"id":"reader"}"#)
        let m = NestLinksModel(api: t.api())
        await m.loadInstalled()
        await m.update(m.installed[0])
        #expect(t.count("POST /api/nest/packages/reader/update") == 1)
        #expect(field(t, "POST /api/nest/packages/reader/update", "accept") == nil)
        #expect(m.look == nil && m.busy.isEmpty)
    }

    @Test func anUpdateThatAsksForMoreOpensTheLookAndPostsOnlyWhenAccepted() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/installed", rowsWithMorePerms)
        t.on("POST /api/nest/packages/reader/update", #"{"id":"reader"}"#)
        let m = NestLinksModel(api: t.api())
        await m.loadInstalled()
        let row = m.installed[0]
        await m.update(row)
        #expect(m.look?.id == "reader" && t.count("POST /api/nest/packages/reader/update") == 0)
        await m.update(row, accepted: true)
        #expect(t.count("POST /api/nest/packages/reader/update") == 1)
        #expect(field(t, "POST /api/nest/packages/reader/update", "accept") == "h-2")
        #expect(m.look == nil)
    }

    @Test func aLookAgainAnswerOpensTheLookInsteadOfFailing() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/installed", rowsWithoutNewPerms)
        t.on("POST /api/nest/packages/reader/update", 409, #"{"detail":"look again","code":"look_again"}"#)
        let m = NestLinksModel(api: t.api())
        await m.loadInstalled()
        await m.update(m.installed[0])
        #expect(m.look?.id == "reader" && m.problem == nil)
    }

    @Test func removeDeletesThePackageThenReadsTheList() async {
        let t = transport()
        t.on("DELETE /api/nest/packages/reader", #"{"ok":true}"#)
        let m = NestLinksModel(api: t.api())
        await m.loadInstalled()
        await m.remove(m.installed[0])
        #expect(t.count("DELETE /api/nest/packages/reader") == 1)
        #expect(t.count("GET /api/nest/installed") == 2)
        #expect(m.busy.isEmpty && m.problem == nil)
    }

    @Test func theCardCarriesWhatItInstallsAndAnOlderCardHasNoContentsRow() async {
        let withContents = card.replacingOccurrences(of: #""permissions":"#, with: #""contents":{"files":[{"path":"SKILL.md","bytes":1500}],"total_bytes":1500,"texts":{"SKILL.md":"Reader text"},"cut":[],"endpoints":["api.example.com"],"warnings":[]},"permissions":"#)
        let t = RoutedTransport(); t.on("POST /api/nest/install-from-link", withContents)
        let m = NestLinksModel(api: t.api())
        m.link = "github.com/me/reader"
        await m.check()
        #expect(m.card?.contents.files == [NestFile(path: "SKILL.md", bytes: 1500, sha256: "")])
        #expect(m.card?.contents.texts["SKILL.md"] == "Reader text" && m.card?.contents.endpoints == ["api.example.com"])
        #expect(m.card.map { NestContentsRules.hasContents($0.contents) } == true)

        let old = RoutedTransport(); old.on("POST /api/nest/install-from-link", card)
        let o = NestLinksModel(api: old.api())
        o.link = "github.com/me/reader"
        await o.check()
        #expect(o.card?.contents == NestContents() && o.card.map { NestContentsRules.hasContents($0.contents) } == false)
    }

    @Test func aRefusalShowsTheServersReasonUntilTheOwnerTypesAgain() async {
        let t = RoutedTransport(); t.on("POST /api/nest/install-from-link", 422, #"{"detail":"a key-like text was found"}"#)
        let m = NestLinksModel(api: t.api())
        m.link = "https://github.com/me/notes"
        await m.check()
        #expect(m.problem == .notPackage && m.reason == "a key-like text was found")
        m.link = "https://github.com/me/notes2"
        #expect(m.problem == nil && m.reason == "")
    }

    @Test func aFailureThatIsNotARefusalGivesNoReason() async {
        let t = RoutedTransport(); t.on("POST /api/nest/install-from-link", 500, #"{"detail":"boom"}"#)
        let m = NestLinksModel(api: t.api())
        m.link = "https://github.com/me/notes"
        await m.check()
        #expect(m.problem == .failed && m.reason == "")
    }

    @Test func anInstallRefusalAlsoShowsTheReason() async {
        let t = transport(); t.on("POST /api/nest/install-from-link", card)
        t.on("POST /api/nest/install-from-link/confirm", 422, #"{"detail":"the files changed in a way we refuse"}"#)
        let m = NestLinksModel(api: t.api())
        m.link = "github.com/me/reader"
        await m.check()
        await m.install()
        #expect(m.problem == .failed && m.reason == "the files changed in a way we refuse")
    }

    @Test func anUpdateRowCountsItsChangedFilesAndAnOlderRowHasNone() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/installed", #"""
        [{"id":"reader","version":"1.0.0","name":"Reader","update":{"version":"1.1.0","widened":[],"permissions_hash":"h-2",
          "changed":{"added":["new.md"],"removed":[],"modified":["SKILL.md","run.py"]}}},
         {"id":"old","version":"1.0.0","name":"Old","update":{"version":"1.1.0","widened":[]}}]
        """#)
        let m = NestLinksModel(api: t.api())
        await m.loadInstalled()
        let reader = m.installed.first { $0.id == "reader" }?.update?.changed
        #expect(reader.map { NestContentsRules.changedCount($0) } == 3)
        #expect(reader?.modified == ["SKILL.md", "run.py"])
        let old = m.installed.first { $0.id == "old" }?.update?.changed
        #expect(old == NestChanged() && old.map { NestContentsRules.changedCount($0) } == 0)
    }
}
