import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private func answer(_ items: String) -> String { #"{"notices":[\#(items)]}"# }
private let importantNotice = #"{"id":"n-2","date":"2026-11-15","level":"important","title":"보안 업데이트","body":"서버를 올려 주세요.","link":"https://kelo.chat/download"}"#
private let infoNotice = #"{"id":"n-1","date":"2026-11-10","level":"info","title":"점검 안내","body":"새벽에 잠깐 멈춰요."}"#

/// 마이 → 공지: the parsing, the link and language rules, and the model's load, open and read calls.
@MainActor @Suite struct NoticesModelTests {
    private func model(_ t: RoutedTransport, lang: String = "ko") -> NoticesModel {
        NoticesModel(api: t.api(), language: { lang })
    }

    @Test func parsesTheNoticesNewestFirstAndAsksForTheLanguage() async {
        let t = RoutedTransport()
        t.on("GET /api/notices", answer(importantNotice + "," + infoNotice))
        let m = model(t)
        await m.load()
        #expect(m.notices.map(\.id) == ["n-2", "n-1"])
        #expect(NoticesRules.isImportant(m.notices[0]))
        #expect(!NoticesRules.isImportant(m.notices[1]))
        #expect(t.calls.last?.query == "lang=ko")
    }

    @Test func aMissingLevelIsInfoAndUnknownFieldsAreIgnored() async {
        let t = RoutedTransport()
        t.on("GET /api/notices", answer(#"{"id":"a1","title":"t","body":"b","future":{"x":1}}"#))
        let m = model(t)
        await m.load()
        #expect(m.notices.map(\.id) == ["a1"])
        #expect(m.notices[0].level == NoticesRules.info)
        #expect(m.notices[0].link == nil)
    }

    @Test func anOlderServerOrABrokenAnswerShowsNothing() async {
        let older = RoutedTransport()                      // no such route: 404
        let m1 = model(older)
        await m1.load()
        #expect(m1.notices.isEmpty)
        let broken = RoutedTransport()
        broken.on("GET /api/notices", "not json")
        let m2 = model(broken)
        await m2.load()
        #expect(m2.notices.isEmpty)
    }

    @Test func aNoticeWithoutAUsableIdIsLeftOut() async {
        let t = RoutedTransport()
        t.on("GET /api/notices", answer(#"{"id":"","title":"x"},{"id":"../etc","title":"y"},{"id":"ok-1","title":"z"}"#))
        let m = model(t)
        await m.load()
        #expect(m.notices.map(\.id) == ["ok-1"])
    }

    @Test func onlyKeloSiteHttpsLinksAreOffered() {
        #expect(NoticesRules.safeLink("https://kelo.chat/download")?.absoluteString == "https://kelo.chat/download")
        #expect(NoticesRules.safeLink("  https://kelo.chat/x  ") != nil)
        #expect(NoticesRules.safeLink("http://kelo.chat/download") == nil)
        #expect(NoticesRules.safeLink("https://kelo.chat.evil.example/") == nil)
        #expect(NoticesRules.safeLink("https://evil.example/https://kelo.chat/") == nil)
        #expect(NoticesRules.safeLink("https://kelo.chat/") == nil)
        #expect(NoticesRules.safeLink("https://kelo.chat/a b") == nil)
        #expect(NoticesRules.safeLink("") == nil)
        #expect(NoticesRules.safeLink(nil) == nil)
    }

    @Test func theLanguageIsThePickOrThePhonesWhenFollowingIt() {
        #expect(NoticesRules.language(pick: "ja", systemShown: "ko") == "ja")
        #expect(NoticesRules.language(pick: nil, systemShown: "en") == "en")
        #expect(NoticesRules.language(pick: "system", systemShown: "ko") == "ko")
    }

    @Test func openingRemovesTheCardAndTellsTheServer() async {
        let t = RoutedTransport()
        t.on("GET /api/notices", answer(importantNotice + "," + infoNotice))
        t.on("POST /api/notices/n-2/read", 204, "")
        let m = model(t)
        await m.load()
        let first = m.notices[0]
        m.open(first)
        #expect(m.opened?.id == "n-2")
        #expect(m.notices.map(\.id) == ["n-1"])
        for _ in 0..<100 where t.count("POST /api/notices/n-2/read") == 0 {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        #expect(t.count("POST /api/notices/n-2/read") == 1)
        m.close()
        #expect(m.opened == nil)
    }

    @Test func aNoticeReadHereDoesNotComeBackOnTheNextLoad() async {
        let t = RoutedTransport()
        t.on("GET /api/notices", answer(importantNotice + "," + infoNotice))   // the server still lists it (the read failed)
        t.on("POST /api/notices/n-2/read", 204, "")
        let m = model(t)
        await m.load()
        m.open(m.notices[0])
        m.close()
        await m.load()
        #expect(m.notices.map(\.id) == ["n-1"])
    }

    @Test func markReadPostsToTheNoticesRoute() async throws {
        let t = RoutedTransport()
        t.on("POST /api/notices/n-1/read", 204, "")
        try await model(t).markRead("n-1")
        #expect(t.count("POST /api/notices/n-1/read") == 1)
    }
}
