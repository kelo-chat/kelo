import Foundation
import SwiftUI
import Testing
import KeloCore
import KeloTestSupport
@testable import KeloShared

@MainActor private struct FakeSurface: NestSurfaceView {
    func view(for entry: NestEntry) -> AnyView { AnyView(Text(entry.key)) }
}

private func entry(_ key: String, place: String = NestPlace.tab, shown: Bool = true, title: String = "", pkg: String = "pkg") -> NestEntry {
    NestEntry(key: key, packageId: "p", place: place, title: title, packageName: pkg, shown: shown)
}

@MainActor private func builtIn() -> [TabSpec] { Tabs.builtIn { _ in AnyView(EmptyView()) } }

@MainActor @Suite struct TabsForTests {
    private func ids(_ t: [TabSpec]) -> [String] { t.map(\.id) }

    @Test func todaysOrderWithoutNest() {
        #expect(ids(Tabs.for(builtIn: builtIn(), nest: [])) == ["chat", "goals", "connectors", "my"])
    }

    @Test func nestTabsComeAfterConnectorsAndBeforeMy() {
        let t = Tabs.for(builtIn: builtIn(), nest: [entry("a"), entry("b")], maxTabs: 5)
        #expect(ids(t) == ["chat", "goals", "connectors", "nest/a", "nest/b", "my"])
        #expect(t[3].fromNest)
    }

    @Test func maxTabsCutsTheList() {
        let t = Tabs.for(builtIn: builtIn(), nest: [entry("a"), entry("b"), entry("c")], maxTabs: 2)
        #expect(ids(t) == ["chat", "goals", "connectors", "nest/a", "nest/b", "my"])
        #expect(ids(Tabs.for(builtIn: builtIn(), nest: [entry("a")], maxTabs: 0)) == ["chat", "goals", "connectors", "my"])
    }

    @Test func entriesNotShownAreLeftOut() {
        let t = Tabs.for(builtIn: builtIn(), nest: [entry("a", shown: false), entry("b")], maxTabs: 3)
        #expect(ids(t).contains("nest/b") && !ids(t).contains("nest/a"))
    }

    @Test func chatAndMyCannotBeHidden() {
        let t = Tabs.for(builtIn: builtIn(), nest: [], layout: TabLayout(hidden: ["chat", "my", "goals", "connectors"]))
        #expect(ids(t) == ["chat", "my"])
    }

    @Test func aNestTabIsNamedByItsTitleElseItsPackage() {
        let t = Tabs.for(builtIn: builtIn(), nest: [entry("a", title: "Mine"), entry("b", title: "", pkg: "Pkg")], maxTabs: 2)
        #expect(t[3].title == .text("Mine"))
        #expect(t[4].title == .text("Pkg"))
    }
}

@MainActor @Suite struct NestSlotsTests {
    private let state = NestUiState(loaded: true, maxTabs: 2, byPlace: [
        NestPlace.tab: [entry("t")], NestPlace.mySection: [entry("m", place: NestPlace.mySection), entry("h", place: NestPlace.mySection, shown: false)],
        NestPlace.homeCard: [entry("c", place: NestPlace.homeCard)], NestPlace.chatCard: [entry("x", place: NestPlace.chatCard)],
    ])

    @Test func withoutARendererNothingShowsAnywhere() {
        for p in [NestPlace.tab, NestPlace.mySection, NestPlace.homeCard, NestPlace.chatCard] {
            #expect(state.entries(p, surface: nil).isEmpty)
        }
    }

    @Test func withARendererEachPlaceGetsItsShownEntries() {
        let s = FakeSurface()
        #expect(state.entries(NestPlace.tab, surface: s).map(\.key) == ["t"])
        #expect(state.entries(NestPlace.mySection, surface: s).map(\.key) == ["m"])
        #expect(state.entries(NestPlace.homeCard, surface: s).map(\.key) == ["c"])
        #expect(state.entries(NestPlace.chatCard, surface: s).map(\.key) == ["x"])
    }

    @Test func theAnswerIsReadFromTheServerShape() throws {
        let json = #"{"schema":1,"max_tabs":3,"places":{"tab":[{"key":"k","package":"p","package_name":"Pk","title":"T","shown":true}]}}"#
        let x = try JSONDecoder().decode(NestExtensions.self, from: Data(json.utf8))
        #expect(x.maxTabs == 3)
        #expect(x.places["tab"]?.first?.packageName == "Pk")
    }
}

@MainActor @Suite struct NestModelTests {
    @Test func loadsOnceAndReloadReplaces() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/extensions", #"{"max_tabs":2,"places":{"tab":[{"key":"a"}]}}"#)
        let m = NestModel(api: t.api())
        await m.loadOnce()
        await m.loadOnce()
        #expect(t.count("GET /api/nest/extensions") == 1)
        #expect(m.state.entries(NestPlace.tab).count == 1)
        t.on("GET /api/nest/extensions", #"{"max_tabs":2,"places":{}}"#)
        await m.reload()
        #expect(m.state.entries(NestPlace.tab).isEmpty)
    }

    @Test func a404OrAFailureGivesAnEmptyList() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/extensions", 404, #"{"detail":"none"}"#)
        let m = NestModel(api: t.api())
        await m.loadOnce()
        #expect(m.state.loaded)
        #expect(m.state.byPlace.isEmpty)
    }

    @Test func anOlderReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/nest/extensions") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"places":{"tab":[{"key":"old"}]}}"#) }
            return (200, #"{"places":{"tab":[{"key":"new"}]}}"#)
        }
        let m = NestModel(api: t.api())
        let first = Task { @MainActor in await m.reload() }
        _ = await eventually { t.count("GET /api/nest/extensions") == 1 }
        await m.reload()
        await gate.release()
        await first.value
        #expect(m.state.entries(NestPlace.tab).map(\.key) == ["new"])
    }
}

@MainActor @Suite struct RouterNestTabTests {
    @Test func aNestTabHoldsTheBarUntilABuiltInOneIsTapped() {
        let r = Router()
        r.tapNest("nest/a")
        #expect(r.nestTab == "nest/a")
        r.tap(.chat)
        #expect(r.nestTab == nil)
        #expect(r.tab == .chat)
    }

    @Test func theChromeIgnoresTheBuiltInStackWhileANestTabShows() {
        let r = Router()
        r.paths[.chat] = NavigationPath([1])
        #expect(r.barHidden)
        r.tapNest("nest/a")
        #expect(!r.barHidden)
    }
}
