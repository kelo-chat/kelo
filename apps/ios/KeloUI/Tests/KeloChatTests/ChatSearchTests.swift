import Testing
import Foundation
import SwiftUI
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

// Search across the chat history: the window a hit adds, opening a hit (loaded: scroll and mark; not loaded: its window replaces the
// rows), following the window down, and the search screen's own state (query, paging, stale answers).

private func msgs(_ ids: ClosedRange<Int64>, role: String = "user") -> String {
    let items = ids.map { #"{"id": \#($0), "role": "\#(role)", "content": "m\#($0)", "created_at": 1700000000}"# }
    return "[" + items.joined(separator: ",") + "]"
}

private func hitsJSON(_ ids: [Int64]) -> String {
    let items = ids.map { #"{"id": \#($0), "role": "user", "snippet": "x «q\#($0)» y", "created_at": 1700000000}"# }
    return "[" + items.joined(separator: ",") + "]"
}

/// The app and a scripted server (memory cache, no network); every request is kept by the transport.
@MainActor private func searchApp(_ route: @escaping @Sendable (String, String, Data?) -> (Int, String)) -> (AppModel, RouteTransport) {
    let t = RouteTransport(route)
    let defaults = UserDefaults(suiteName: "kelo-search-test-\(UUID().uuidString)")!
    let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")), prefs: Prefs(defaults: defaults),
                       transport: t, cache: ScreenCache(directory: nil, key: { nil }))
    return (app, t)
}

@MainActor private func settle(_ cond: @MainActor () -> Bool) async {
    for _ in 0..<300 {
        if cond() { return }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

@Suite(.serialized) struct ChatSearchTests {
    // ---- the window a hit adds ----------------------------------------------------------------------------------------------

    @Test func aWindowKnowsWhichSidesHaveMoreBeyondThem() {
        let full = ChatMessage.parseList(try! JSON.parse(msgs(1...21)))          // hit 11: 10 before (1-10), 10 after (12-21)
        let w = ChatSearchRules.window(full, hit: 11)
        #expect(w.rows.map(\.id) == Array(1...21))
        #expect(w.hasOlder && w.hasNewer)
        let edge = ChatMessage.parseList(try! JSON.parse(msgs(1...14)))         // hit 5: 4 before, 9 after
        let e = ChatSearchRules.window(edge, hit: 5)
        #expect(!e.hasOlder && !e.hasNewer)
    }

    @Test func theNewerPartIsWhatComesAfterTheNewestShown() {
        let fetched = ChatMessage.parseList(try! JSON.parse(msgs(8...60)))
        let n = ChatSearchRules.newer(fetched, after: 10, page: 50)
        #expect(n.rows.first?.id == 11)
        #expect(n.rows.count == 50)
        #expect(n.more)
        let short = ChatSearchRules.newer(ChatMessage.parseList(try! JSON.parse(msgs(8...12))), after: 10, page: 50)
        #expect(short.rows.map(\.id) == [11, 12] && !short.more)
    }

    // ---- opening a hit ----------------------------------------------------------------------------------------------------

    @MainActor @Test func aLoadedHitIsScrolledToAndMarkedWithoutAnyRequest() async {
        let (app, t) = searchApp { _, path, _ in
            if path.hasPrefix("/api/messages?") { return (200, msgs(1...3)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        let before = m.focusRequest
        await m.openSearchHit(2)
        #expect(m.focusTarget == 2)
        #expect(m.focusRequest == before + 1)
        #expect(m.markedMessage == 2)
        #expect(!t.requests.contains { $0.url?.path == "/api/messages/around" })
        #expect(m.items.count == 3 && !m.hasNewer)
    }

    @MainActor @Test func aHitNotInTheListReplacesTheRowsWithItsWindow() async {
        let (app, t) = searchApp { _, path, _ in
            if path == "/api/messages/around?id=40&n=10" { return (200, msgs(30...50)) }
            if path.hasPrefix("/api/messages?") { return (200, msgs(91...100)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        await m.openSearchHit(40)
        #expect(t.requests.contains { $0.url?.path == "/api/messages/around" && $0.url?.query == "id=40&n=10" })
        #expect(m.items.map(\.key) == (30...50).map { "m\($0)" })
        #expect(m.hasOlder && m.hasNewer)
        #expect(m.focusTarget == 40 && m.markedMessage == 40)
    }

    @MainActor @Test func aHitTheServerDoesNotHaveLeavesTheChatAsItWas() async {
        let (app, _) = searchApp { _, path, _ in
            if path.hasPrefix("/api/messages/around") { return (404, #"{"detail":"not found"}"#) }
            if path.hasPrefix("/api/messages?") { return (200, msgs(1...3)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        await m.openSearchHit(999)
        #expect(m.items.map(\.key) == ["m1", "m2", "m3"])
        #expect(m.markedMessage == nil && m.focusRequest == 0)
    }

    @MainActor @Test func theMarkFadesAfterItsHold() async {
        let (app, _) = searchApp { _, path, _ in
            if path.hasPrefix("/api/messages?") { return (200, msgs(1...2)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        await m.openSearchHit(1)
        #expect(m.markedMessage == 1)
        try? await Task.sleep(nanoseconds: ChatSearchRules.markNanos + 300_000_000)
        #expect(m.markedMessage == nil)
    }

    // ---- the window's end and the newest page ------------------------------------------------------------------------------

    @MainActor @Test func theEndOfAWindowLoadsTheMessagesAfterIt() async {
        let (app, _) = searchApp { _, path, _ in
            if path == "/api/messages/around?id=40&n=10" { return (200, msgs(30...50)) }
            if path == "/api/messages/around?id=50&n=50" { return (200, msgs(41...50)) }
            if path.hasPrefix("/api/messages?") { return (200, msgs(91...100)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        await m.openSearchHit(40)
        #expect(m.hasNewer)
        m.loadNewer()
        await settle { !m.loadingNewer && m.items.count > 21 }
        #expect(m.items.last?.asMsg?.id == 50)
        #expect(!m.hasNewer)   // a short answer: the newest message is in
    }

    @MainActor @Test func leavingAWindowBringsTheNewestPageBack() async {
        let (app, _) = searchApp { _, path, _ in
            if path == "/api/messages/around?id=40&n=10" { return (200, msgs(30...50)) }
            if path.hasPrefix("/api/messages?") { return (200, msgs(91...100)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        await m.openSearchHit(40)
        var ran = false
        m.leaveWindow { ran = true }
        await settle { ran }
        #expect(ran)
        #expect(m.items.map(\.key) == (91...100).map { "m\($0)" })
        #expect(!m.hasNewer)
    }

    @MainActor @Test func theNewestPageNoLongerReplacesAWindowWhenNewsComes() async {
        let (app, t) = searchApp { _, path, _ in
            if path == "/api/messages/around?id=40&n=10" { return (200, msgs(30...50)) }
            if path.hasPrefix("/api/messages?") { return (200, msgs(91...100)) }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await settle { m.loaded }
        await m.openSearchHit(40)
        let calls = t.requests.count
        await m.checkForNews()           // another phone may have written: the window stays while it is open
        #expect(t.requests.count == calls)
        #expect(m.items.map(\.key) == (30...50).map { "m\($0)" })
    }

    // ---- the search screen's state ------------------------------------------------------------------------------------------

    @MainActor @Test func aBlankQueryAsksNothing() async {
        let (app, t) = searchApp { _, _, _ in (200, "[]") }
        let s = ChatSearchModel(api: { app.api }, pauseNanos: 1)
        s.setQuery("   ")
        try? await Task.sleep(nanoseconds: 30_000_000)
        #expect(!s.hasQuery && s.hits.isEmpty && !s.loading)
        #expect(!t.requests.contains { $0.url?.path == "/api/messages/search" })
    }

    @MainActor @Test func theQueryIsSentTrimmedAndPagedByBefore() async {
        let (app, t) = searchApp { _, path, _ in
            if path.hasPrefix("/api/messages/search?q=%EB%82%A0%EC%94%A8&limit=20") && path.contains("before=") == false {
                return (200, hitsJSON(Array((21...40).map { Int64($0) }.reversed())))
            }
            if path.contains("before=21") { return (200, hitsJSON([20, 19])) }
            return (200, "[]")
        }
        let s = ChatSearchModel(api: { app.api }, pauseNanos: 1)
        s.setQuery("  날씨 ")
        await settle { s.hits.count == 20 }
        #expect(s.hits.count == 20 && s.more)
        #expect(t.requests.last?.url?.query?.hasPrefix("q=%EB%82%A0%EC%94%A8") == true)
        s.loadMore()
        await settle { s.hits.count == 22 }
        #expect(s.hits.map(\.id).last == 19)
        #expect(!s.more)            // a short page is the end
    }

    @MainActor @Test func aSearchForAnOldQueryIsDropped() async {
        let (app, _) = searchApp { _, path, _ in
            if path.contains("q=old") { return (200, hitsJSON([7])) }
            return (200, hitsJSON([9]))
        }
        let s = ChatSearchModel(api: { app.api }, pauseNanos: 1)
        s.setQuery("old")
        s.setQuery("new")
        await settle { !s.loading && !s.hits.isEmpty }
        #expect(s.hits.map(\.id) == [9])
    }

    @MainActor @Test func aFailedSearchSaysSo() async {
        let (app, _) = searchApp { _, _, _ in (500, #"{"detail":"no"}"#) }
        let s = ChatSearchModel(api: { app.api }, pauseNanos: 1)
        s.setQuery("abc")
        await settle { s.failed }
        #expect(s.failed && s.hits.isEmpty && !s.loading)
    }

    @MainActor @Test func theSnippetShowsTheMatchBoldWithoutTheMarks() {
        let s = ChatSearchRow.snippet("어제 «날씨» 좋았어", accent: .red, ink: .black)
        #expect(String(s.characters) == "어제 날씨 좋았어")
        let bold = s.runs.filter { $0.inlinePresentationIntent == .stronglyEmphasized }.map { String(s[$0.range].characters) }
        #expect(bold == ["날씨"])
    }
}
