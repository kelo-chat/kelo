import Foundation
import Testing
import KeloCore
import KeloTestSupport
@testable import KeloShared

/// The screen the server sends, read into components: every type of docs/nest-ui.md Part 1, the limits and the leniency rules.
@MainActor @Suite struct NestScreenParseTests {
    private func parse(_ json: String) -> NestScreen { NestScreenParse.screen(try! JSON.parse(json)) }

    @Test func theHabitTrackerScreenIsReadComponentByComponent() {
        let s = parse(NestFixtures.habits)
        #expect(s.title == "Habits")
        #expect(s.errors == ["Connect Example Parcels first."])
        #expect(s.components.count == 4)
        #expect(s.components[0] == .header(text: "Keep the streak going", subtitle: "2 check-ins so far"))
        guard case .form(let fields, let label, let submit) = s.components[1] else { Issue.record("not a form"); return }
        #expect(fields.map(\.id) == ["habit", "note"])
        #expect(fields.map(\.type) == ["select", "text"])
        #expect(fields[0].required)
        #expect(fields[0].options.count == 3)
        #expect(fields[0].options[1] == NestOption(value: "Read", label: "Read 20 pages"))
        #expect(fields[1].maxLength == 200)
        #expect(label == "Check in")
        #expect(submit == NestAction(id: "check_in", type: "store_add", confirm: false, label: "Check in"))
        guard case .list(let rows, let emptyText, _) = s.components[2] else { Issue.record("not a list"); return }
        #expect(rows.map(\.title) == ["Walk", "Read"])
        #expect(rows[0].subtitle == "park" && rows[0].trailing == "2026-11-09")
        let remove = rows[0].action!
        #expect(remove.removes && remove.item == "a1" && remove.data == "checkins")
        #expect(emptyText == nil)
        guard case .button(_, let style, let action) = s.components[3] else { Issue.record("not a button"); return }
        #expect(style == "secondary" && action.type == "agent")
    }

    @Test func theStatCardIsReadWithItsIconAndCaption() {
        guard case .stat(let label, let value, _, let caption, let icon) = parse(NestFixtures.card).components.first else { Issue.record("not a stat"); return }
        #expect(label == "Check-ins" && value == "2" && icon == "trophy" && caption == "Last: Walk on 2026-11-09")
    }

    @Test func everyOtherComponentIsRead() {
        let c = parse(NestFixtures.all).components
        let styles = c.prefix(5).map { comp -> String in if case .text(_, let s) = comp { return s }; return "?" }
        #expect(styles == ["title", "body", "caption", "label", "body"])
        #expect(c[5] == .stat(label: "Distance", value: "12.5", unit: "km", caption: "this week", icon: "fitness"))
        #expect(c[6] == .list(rows: [], emptyText: "Nothing here yet", emptyIcon: "sparkle"))
        #expect(c[7] == .chart(kind: "bar", label: "Steps", points: [NestPoint(x: "Mon", y: 3), NestPoint(x: "Tue", y: 5), NestPoint(x: "Wed", y: 2)]))
        #expect(c[8] == .chart(kind: "line", label: "Weight", points: []))
        #expect(c[9] == .image(src: "/api/nest/extensions/demo/image?src=x", alt: "A chart picture", height: 120))
        guard case .card(let title, _, let kids, let action) = c[10] else { Issue.record("not a card"); return }
        #expect(title == "Parcel" && action?.type == "open_url" && action?.url == "https://example.com/track" && kids.count == 2)
        guard case .button(_, let st, let a) = kids[1] else { Issue.record("not a button"); return }
        #expect(st == "danger" && a.confirm)
        guard case .empty(_, let icon, let ea) = c[11] else { Issue.record("not empty"); return }
        #expect(icon == "link" && ea?.id == "connect")
    }

    @Test func aComponentOfAnUnknownTypeBecomesOneUpdateRowAndTheRestStaysWhole() {
        let c = parse(NestFixtures.all).components
        #expect(c[12] == .unsupported(text: ""))                                    // the app's own words
        #expect(c[13] == .unsupported(text: "Update the app to see this part"))     // the server's `unsupported`
        #expect(c.count == 14)
    }

    @Test func aFieldTheAppDoesNotKnowIsIgnoredAndMissingOnesAreEmpty() {
        let s = parse(#"{"extra":1,"components":[{"type":"header","text":"Hi","future_field":[1,2]},{"type":"text"}]}"#)
        #expect(s.components[0] == .header(text: "Hi", subtitle: ""))
        #expect(s.components[1] == .text("", style: "body"))
        #expect(NestScreenParse.screen(.string("nope")) == NestScreen())
        #expect(NestScreenParse.screen(.object([:])) == NestScreen())
    }

    // ---- limits

    private func texts(_ n: Int) -> String { "{\"components\":[" + (1...n).map { "{\"type\":\"text\",\"text\":\"t\($0)\"}" }.joined(separator: ",") + "]}" }

    @Test func atMostSixtyComponentsInAScreenNestedOnesCounted() {
        #expect(parse(texts(80)).components.count == 60)
        let card = #"{"type":"card","title":"c","children":["# + (1...30).map { _ in #"{"type":"text","text":"x"}"# }.joined(separator: ",") + "]}"
        let s = parse("{\"components\":[\(card),\(card),\(card)]}")
        let drawn = s.components.reduce(0) { n, c in if case .card(_, _, let kids, _) = c { return n + 1 + kids.count }; return n + 1 }
        #expect(drawn <= 60)
    }

    @Test func cardsNestAtMostThreeDeep() {
        func card(_ d: Int) -> String { d == 0 ? #"{"type":"text","text":"leaf"}"# : "{\"type\":\"card\",\"title\":\"c\(d)\",\"children\":[\(card(d - 1))]}" }
        var c: NestComponent = parse("{\"components\":[\(card(5))]}").components[0]
        var depth = 0
        while case .card(_, _, let kids, _) = c { depth += 1; c = kids[0] }
        #expect(depth == 3)
        #expect(c == .unsupported(text: ""))   // the fourth level is not drawn
    }

    @Test func aCardHoldsAtMostTwentyChildren() {
        let kids = (1...30).map { _ in #"{"type":"text","text":"x"}"# }.joined(separator: ",")
        guard case .card(_, _, let children, _) = parse("{\"components\":[{\"type\":\"card\",\"children\":[\(kids)]}]}").components[0] else { Issue.record("not a card"); return }
        #expect(children.count == 20)
    }

    @Test func listRowsChartPointsFormFieldsAndOptionsAreCapped() {
        let rows = (1...150).map { "{\"key\":\"k\($0)\",\"title\":\"r\($0)\"}" }.joined(separator: ",")
        guard case .list(let r, _, _) = parse("{\"components\":[{\"type\":\"list\",\"rows\":[\(rows)]}]}").components[0] else { Issue.record("x"); return }
        #expect(r.count == 100)
        let points = (1...90).map { "{\"x\":\"p\($0)\",\"y\":\($0)}" }.joined(separator: ",")
        guard case .chart(_, _, let p) = parse("{\"components\":[{\"type\":\"chart\",\"kind\":\"line\",\"points\":[\(points)]}]}").components[0] else { Issue.record("x"); return }
        #expect(p.count == 60)
        let act = #""submit":{"label":"Go","action":{"id":"a","type":"store_add"}}"#
        let fields = (1...20).map { "{\"id\":\"f\($0)\",\"type\":\"text\",\"label\":\"F\"}" }.joined(separator: ",")
        guard case .form(let f, _, _) = parse("{\"components\":[{\"type\":\"form\",\"fields\":[\(fields)],\(act)}]}").components[0] else { Issue.record("x"); return }
        #expect(f.count == 12)
        let opts = (1...50).map { "{\"value\":\"v\($0)\",\"label\":\"L\($0)\"}" }.joined(separator: ",")
        guard case .form(let g, _, _) = parse("{\"components\":[{\"type\":\"form\",\"fields\":[{\"id\":\"s\",\"type\":\"select\",\"label\":\"S\",\"options\":[\(opts)]}],\(act)}]}").components[0] else { Issue.record("x"); return }
        #expect(g[0].options.count == 30)
    }

    @Test func aTextIsCutAt600CharactersAndAnImageHeightStaysBetween40And400() {
        guard case .text(let t, _) = parse("{\"components\":[{\"type\":\"text\",\"text\":\"\(String(repeating: "a", count: 900))\"}]}").components[0] else { Issue.record("x"); return }
        #expect(t.count == 600)
        func h(_ v: String) -> Int { if case .image(_, _, let x) = parse("{\"components\":[{\"type\":\"image\",\"src\":\"/api/nest/x\",\"alt\":\"a\"\(v)}]}").components[0] { return x }; return -1 }
        #expect(h(#","height":5000"#) == 400)
        #expect(h(#","height":1"#) == 40)
        #expect(h("") == 160)
    }

    @Test func aButtonWithoutAnActionAndAFormWithoutFieldsAreUpdateRows() {
        let c = parse(#"{"components":[{"type":"button","label":"x","style":"primary"},{"type":"form","fields":[],"submit":{"label":"Go","action":{"id":"a","type":"x"}}}]}"#).components
        #expect(c == [.unsupported(text: ""), .unsupported(text: "")])
    }

    @Test func errorsAreShownOnceEach() {
        let s = parse(#"{"components":[],"errors":[{"data":"a","error":"Same"},{"data":"b","error":"Same"},{"data":"c","error":""}]}"#)
        #expect(s.errors == ["Same"])
    }

    // ---- action answers

    @Test func everyActionAnswerIsRead() throws {
        func r(_ j: String) -> NestActionResult { NestActionResult.parse(try! JSON.parse(j)) }
        #expect(r(#"{"status":"done","message":"Logged.","refresh":true}"#) == .done(message: "Logged.", refresh: true))
        #expect(r(#"{"status":"done"}"#) == .done(message: "", refresh: false))
        #expect(r(#"{"status":"open","url":"https://example.com/x"}"#) == .open(url: "https://example.com/x"))
        #expect(r(#"{"status":"started","message":"Kelo is on it","turn_id":"t1"}"#) == .started(message: "Kelo is on it", turnId: "t1"))
        guard case .confirm(let token, let title, let detail, let sends, let exp) = r(#"{"status":"confirm","token":"tok","title":"Cancel the parcel?","detail":"Goes to parcels.example","sends":{"id":"p1","force":true,"n":3,"tags":["a","b"]},"expires_in":300}"#)
        else { Issue.record("not a confirm"); return }
        #expect(token == "tok" && title == "Cancel the parcel?" && detail == "Goes to parcels.example" && exp == 300)
        let lines = Dictionary(uniqueKeysWithValues: sends.map { ($0.key, $0.value) })
        #expect(lines == ["id": "p1", "force": "true", "n": "3", "tags": #"["a","b"]"#])
        #expect(r(#"{"status":"something_new"}"#) == .unknown)
    }

    // ---- forms

    @Test func aFormIsCheckedBeforeItIsSentAndSendsTypedValues() {
        let fields = [
            NestField(id: "habit", type: "select", label: "Habit", required: true, options: [NestOption(value: "Walk", label: "Walk")]),
            NestField(id: "n", type: "number", label: "Count", min: 1, max: 10),
            NestField(id: "note", type: "text", label: "Note", maxLength: 5),
            NestField(id: "on", type: "toggle", label: "On"),
            NestField(id: "when", type: "date", label: "When"),
        ]
        #expect(NestFormRules.missing(fields, ["habit": ""]) == ["habit"])
        #expect(NestFormRules.missing(fields, ["habit": "Walk", "n": "11"]) == ["n"])
        #expect(NestFormRules.missing(fields, ["habit": "Walk", "n": "abc"]) == ["n"])
        #expect(NestFormRules.missing(fields, ["habit": "Walk", "n": "3"]).isEmpty)
        let p = NestFormRules.payload(fields, ["habit": "Walk", "n": "3", "note": "abcdefgh", "on": "true", "when": "2026-11-09"])
        #expect(p["habit"] == .string("Walk"))
        #expect(p["n"] == .number(3))
        #expect(p["note"] == .string("abcde"))   // max_length
        #expect(p["on"] == .bool(true))
        #expect(p["when"] == .string("2026-11-09"))
        #expect(NestFormRules.payload(fields, ["n": "1,5"])["n"] == .number(1.5))
        #expect(NestFormRules.payload(fields, [:])["note"] == nil)   // an empty optional text is left out
        #expect(NestFormRules.payload(fields, [:])["on"] == .bool(false))
    }

    @Test func aFormStartsWithItsDefaultsAndTheFirstOptionOfARequiredSelect() {
        let initial = NestFormRules.initial([
            NestField(id: "a", type: "text", label: "A", default: "hi"), NestField(id: "b", type: "toggle", label: "B", default: "true"),
            NestField(id: "c", type: "toggle", label: "C"),
            NestField(id: "d", type: "select", label: "D", required: true, options: [NestOption(value: "x", label: "X"), NestOption(value: "y", label: "Y")]),
            NestField(id: "e", type: "select", label: "E", options: [NestOption(value: "x", label: "X")]),
        ])
        #expect(initial == ["a": "hi", "b": "true", "c": "false", "d": "x", "e": ""])
    }

    @Test func onlyWebAddressesLeaveTheAppAndOnlyThroughTheSystemBrowser() {
        #expect(nestOpenable("https://example.com/a?b=1"))
        #expect(nestOpenable("http://example.com"))
        #expect(!nestOpenable("javascript:alert(1)"))
        #expect(!nestOpenable("file:///etc/passwd"))
        #expect(!nestOpenable("intent://x#Intent;end"))
        #expect(!nestOpenable("https://exa mple.com"))
        #expect(!nestOpenable(""))
    }

    @Test func everyNameOfTheSchemaHasAnIconAndAnUnknownOneIsAStar() {
        #expect(NestIcons.names.count == 70)
        for n in NestIcons.names { #expect(NestIcons.known(n), "\(n)") }
        #expect(NestIcons.symbol("not-an-icon") == NestIcons.symbol("star"))
        #expect(!NestIcons.known("not-an-icon"))
    }

    @Test func theExtensionsAnswerKeepsTheDraftFlag() throws {
        let x = try JSONDecoder().decode(NestExtensions.self, from: Data(NestFixtures.extensions.utf8))
        #expect(x.maxTabs == 2)
        let tabs = x.places[NestPlace.tab] ?? []
        #expect(tabs.map(\.draft) == [false, true])
        #expect(tabs[0].author == "Example Author" && tabs[1].icon == "book")
    }
}

/// Loading a surface, running its actions, and what each answer of docs/nest-ui.md Part 1 does.
@MainActor @Suite struct NestScreenModelTests {
    private let entry = NestEntry(key: "habit-tracker/habits", packageId: "habit-tracker", surface: "habits", place: NestPlace.tab, title: "Habits")
    private let data = "GET /api/nest/extensions/habit-tracker/data"
    private func post(_ id: String) -> String { "POST /api/nest/extensions/habit-tracker/actions/\(id)" }
    private func last(_ t: RoutedTransport) -> [String: JSON] { (try? JSON.parse(t.calls.last { $0.method == "POST" }?.body ?? "{}"))?.objectValue ?? [:] }

    @Test func loadingReadsTheScreenAndKeepsTheNoticeOfDataThatFailed() async {
        let t = RoutedTransport(); t.on(data, NestFixtures.habits)
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.load()
        #expect(m.loaded && !m.refreshing && !m.gone && m.error == nil)
        #expect(m.screen.title == "Habits" && m.screen.components.count == 4)
        #expect(m.screen.errors == ["Connect Example Parcels first."])
        #expect(t.calls.first?.query == "surface=habits")
    }

    @Test func aSurfaceTheServerForbidsOrDoesNotKnowIsGone() async {
        for code in [403, 404] {
            let t = RoutedTransport(); t.on(data, code, #"{"detail":"No longer shown"}"#)
            let m = NestScreenModel(api: t.api(), entry: entry)
            await m.load()
            #expect(m.gone && m.loaded, "\(code)")
            #expect(m.error == "No longer shown")
        }
    }

    @Test func tooManyRequestsShowTheServersWordsAndKeepWhatWasOnScreen() async {
        let t = RoutedTransport()
        t.on(data) { _, n in n == 0 ? (200, NestFixtures.habits) : (429, #"{"detail":"Slow down, try again later"}"#) }
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.load(); await m.load()
        #expect(m.error == "Slow down, try again later" && !m.gone)
        #expect(m.screen.components.count == 4)   // the screen stays
    }

    @Test func aFormSubmitSendsTheFormAndReloadsWhenTheAnswerSaysRefresh() async {
        let t = RoutedTransport(); t.on(data, NestFixtures.habits)
        t.on(post("check_in"), #"{"status":"done","message":"Logged. Keep it up!","refresh":true}"#)
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.load()
        await m.run(NestAction(id: "check_in", type: "store_add", label: "Check in"), form: ["habit": "Walk", "note": "park"])
        #expect(t.count(data) == 2)
        #expect(last(t)["form"]?["habit"] == .string("Walk"))
        #expect(last(t)["item"] == nil)
        #expect(m.message == "Logged. Keep it up!")
        #expect(m.busy.isEmpty)
    }

    @Test func aRowActionSendsOnlyTheRowsKeyAndDataId() async {
        let t = RoutedTransport(); t.on(post("remove"), #"{"status":"done","refresh":false}"#)
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "remove", type: "store_delete", label: "Remove", item: "a1", data: "checkins"))
        let b = last(t)
        #expect(b["item"] == .string("a1") && b["data"] == .string("checkins") && b["form"] == nil)
        #expect(t.count(data) == 0)   // refresh was false
    }

    @Test func anActionThatNeedsConfirmingAsksFirstAndThenSendsTheTokenOnly() async {
        let t = RoutedTransport()
        t.on(post("cancel")) { call, _ in
            call.body.contains("tok-1") ? (200, #"{"status":"done","message":"Cancelled","refresh":false}"#)
                : (200, #"{"status":"confirm","token":"tok-1","title":"Cancel the parcel?","detail":"Goes to parcels.example","sends":{"id":"p1"},"expires_in":300}"#)
        }
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "cancel", type: "connector", confirm: true, label: "Cancel", item: "p1", data: "parcels"))
        #expect(m.confirm?.title == "Cancel the parcel?")
        #expect(m.confirm?.sends == [NestSend(key: "id", value: "p1")])
        #expect(t.count(post("cancel")) == 1)
        await m.confirmPending()
        #expect(t.count(post("cancel")) == 2)
        #expect(m.message == "Cancelled" && m.confirm == nil)
        #expect(Set(last(t).keys) == ["confirm"])   // the token only, never the form or the row again
        #expect(last(t)["confirm"] == .string("tok-1"))
    }

    @Test func cancellingTheConfirmSendsNothingMore() async {
        let t = RoutedTransport(); t.on(post("cancel"), #"{"status":"confirm","token":"t","title":"Sure?","detail":"","sends":{},"expires_in":60}"#)
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "cancel", type: "connector", confirm: true, label: "Cancel"))
        m.dismissConfirm()
        #expect(m.confirm == nil && t.count(post("cancel")) == 1)
    }

    @Test func anExpiredConfirmationIsDroppedWithTheServersWords() async {
        let t = RoutedTransport()
        t.on(post("cancel")) { call, _ in
            call.body.contains("tok") ? (410, #"{"detail":"That took too long, try again"}"#)
                : (200, #"{"status":"confirm","token":"tok","title":"Sure?","detail":"","sends":{},"expires_in":1}"#)
        }
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "cancel", type: "connector", confirm: true, label: "Cancel"))
        await m.confirmPending()
        #expect(m.message == "That took too long, try again" && m.confirm == nil)
    }

    @Test func anOpenAnswerGoesToTheSystemBrowserAndOnlyWebAddressesDo() async {
        let t = RoutedTransport()
        t.on(post("open")) { _, n in (200, n == 0 ? #"{"status":"open","url":"https://example.com/track"}"# : #"{"status":"open","url":"javascript:alert(1)"}"#) }
        let m = NestScreenModel(api: t.api(), entry: entry)
        let a = NestAction(id: "open", type: "connector", label: "Open")
        await m.run(a)
        #expect(m.nextEffect() == .openUrl("https://example.com/track"))
        await m.run(a)
        #expect(m.nextEffect() == nil)
    }

    @Test func anOpenUrlActionOpensItsOwnAddressWithoutAskingTheServer() async {
        let t = RoutedTransport()
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "track", type: "open_url", label: "Track", url: "https://example.com/track"))
        #expect(m.nextEffect() == .openUrl("https://example.com/track"))
        await m.run(NestAction(id: "track", type: "open_url", label: "Track", url: "file:///etc/passwd"))
        #expect(m.nextEffect() == nil && t.count(post("track")) == 0)
    }

    @Test func aStartedAnswerShowsItsMessageAndOffersTheChat() async {
        let t = RoutedTransport(); t.on(post("weekly"), #"{"status":"started","message":"Kelo is reviewing your week","turn_id":"turn-9"}"#)
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "weekly", type: "agent", label: "Weekly review"))
        #expect(m.message == "Kelo is reviewing your week")
        #expect(m.nextEffect() == .started(message: "Kelo is reviewing your week", turnId: "turn-9"))
    }

    @Test func aRefusedActionShowsTheServersWordsAndA403Or404ReadsTheScreenAgain() async {
        for (code, reads) in [(422, 0), (409, 0), (429, 0), (502, 0), (403, 1), (404, 1)] {
            let t = RoutedTransport(); t.on(data, NestFixtures.habits); t.on(post("check_in"), code, "{\"detail\":\"Words for \(code)\"}")
            let m = NestScreenModel(api: t.api(), entry: entry)
            await m.run(NestAction(id: "check_in", type: "store_add", label: "Check in"), form: ["habit": ""])
            #expect(m.message == "Words for \(code)", "\(code)")
            #expect(t.count(data) == reads, "\(code)")
            #expect(m.confirm == nil && m.busy.isEmpty)
        }
    }

    @Test func aButtonIsNotSentTwiceWhileItsRequestRuns() async {
        let t = RoutedTransport(); let gate = Gate()
        t.on(post("slow")) { _, _ in await gate.wait(); return (200, #"{"status":"done"}"#) }
        let m = NestScreenModel(api: t.api(), entry: entry)
        let a = NestAction(id: "slow", type: "store_add", label: "Slow")
        let first = Task { @MainActor in await m.run(a) }
        _ = await eventually { m.busy.contains(a.key) }
        await m.run(a); await m.run(a)
        await gate.release()
        await first.value
        #expect(t.count(post("slow")) == 1 && m.busy.isEmpty)
        #expect(!m.busy.contains(a.key) && a.key != NestAction(id: "slow", item: "r2", data: "d").key)   // another row is another button
    }

    @Test func nestChangedForThisPackageReloadsTheOpenScreenAndOthersDoNot() async {
        let t = RoutedTransport(); t.on(data, NestFixtures.habits)
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.load()
        await m.changed(id: "another-package")
        #expect(t.count(data) == 1)
        await m.changed(id: "habit-tracker")
        #expect(t.count(data) == 2)
    }

    @Test func theLiveEventIsDeliveredToWhoeverListens() async {
        var got: [String] = []
        let task = Task { @MainActor in for await id in NestLive.ids() { got.append(id); if got.count == 2 { break } } }
        try? await Task.sleep(nanoseconds: 50_000_000)
        NestLive.post("a"); NestLive.post("b")
        _ = await eventually { got.count == 2 }
        task.cancel()
        #expect(got == ["a", "b"])
    }

    @Test func anActionWithoutAnIdIsIgnored() async {
        let t = RoutedTransport()
        let m = NestScreenModel(api: t.api(), entry: entry)
        await m.run(NestAction(id: "", type: "store_add"))
        #expect(m.busy.isEmpty && t.calls.isEmpty)
    }

    @Test func theOwnerPicksTheTabsAndTheAnswerIsTheNewList() async {
        let t = RoutedTransport()
        t.on("GET /api/nest/extensions", NestFixtures.extensions)
        t.on("PUT /api/nest/extensions/tabs", #"{"schema":1,"max_tabs":2,"places":{"tab":[{"key":"reading/books","shown":true},{"key":"habit-tracker/habits","shown":false}]}}"#)
        let n = NestModel(api: t.api())
        await n.loadOnce()
        let err = await n.setTabs(["reading/books"])
        #expect(err == nil)
        #expect(t.calls.last?.body == #"{"show":["reading\/books"]}"# || t.calls.last?.body == #"{"show":["reading/books"]}"#)
        #expect(n.state.entries(NestPlace.tab).map(\.key) == ["reading/books"])
        t.on("PUT /api/nest/extensions/tabs", 403, #"{"detail":"Only the owner"}"#)
        #expect(await n.setTabs([]) == "Only the owner")
    }
}
