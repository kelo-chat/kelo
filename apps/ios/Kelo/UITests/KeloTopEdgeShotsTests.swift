import XCTest

/// The top of the pages (TopEdge.swift): chat and goals at rest and scrolled, and a pushed sub page (a goal's detail), against `apps/desktop/tools/mock_server.py`. At rest the
/// top area is the page colour; once the list scrolls under the clock and the header, the system bar material shows behind them.
/// KELO_DARK=1 shoots in dark mode (the simulator's appearance is set back to light at the end). Other variables as KeloScreensTests:
/// KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS. Without KELO_TOPEDGE the test is skipped.
final class KeloTopEdgeShotsTests: KeloShotCase {

    override func tearDown() {
        if env["KELO_DARK"] == "1" { XCUIDevice.shared.appearance = .light }
    }

    func testTopEdge() throws {
        guard env["KELO_TOPEDGE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no top edge walk asked") }
        XCUIDevice.shared.appearance = env["KELO_DARK"] == "1" ? .dark : .light
        launchApp()
        signIn(key)
        pause(2)

        // the chat at rest, then scrolled up through its history
        shot("chat-rest")
        let list = app.scrollViews.firstMatch
        if list.waitForExistence(timeout: 10) {
            list.swipeDown(velocity: .fast)
            list.swipeDown(velocity: .fast)
            pause(1.5)
            shot("chat-scrolled")
        } else {
            XCTFail("no chat list\n\(app.debugDescription)")
        }

        // goals: back at rest, then scrolled
        tap(element("kelo.tab.goals"), "goals tab")
        pause(2)
        shot("goals-rest")
        let goals = app.collectionViews.firstMatch.exists ? app.collectionViews.firstMatch : app.tables.firstMatch
        if goals.waitForExistence(timeout: 10) {
            goals.swipeUp(velocity: .fast)
            goals.swipeUp(velocity: .fast)
            pause(1.5)
            shot("goals-scrolled")
        }
    }

    /// A pushed sub page (a goal's detail, SubPage): at rest its back bar sits on the page colour; scrolled, the content runs under
    /// the back bar and the status bar, and the system bar material shows behind both.
    func testSubPageTopEdge() throws {
        guard env["KELO_TOPEDGE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no top edge walk asked") }
        XCUIDevice.shared.appearance = env["KELO_DARK"] == "1" ? .dark : .light
        launchApp()
        signIn(key)
        pause(2)

        tap(element("kelo.tab.goals"), "goals tab")
        pause(2)
        let goals = app.collectionViews.firstMatch.exists ? app.collectionViews.firstMatch : app.tables.firstMatch
        guard goals.waitForExistence(timeout: 10) else { return XCTFail("no goals list\n\(app.debugDescription)") }
        // the first goal's title (the mock's first goal in each walk language) opens its detail, a SubPage
        tap(element(w("Walk 30 minutes a day", "매일 30분 걷기")), "first goal")
        pause(2)
        shot("subpage-rest")

        // the detail's own scroll view: the one on top of the goals list underneath it
        let page = app.scrollViews.allElementsBoundByIndex.last ?? app.scrollViews.firstMatch
        if page.waitForExistence(timeout: 10) {
            page.swipeUp(velocity: .fast)
            page.swipeUp(velocity: .fast)
            pause(1.5)
            shot("subpage-scrolled")
        } else {
            XCTFail("no sub page scroll view\n\(app.debugDescription)")
        }
    }
}
