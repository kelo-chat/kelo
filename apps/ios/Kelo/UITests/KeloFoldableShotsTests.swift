import XCTest

/// Wide windows (iPad, KELO_FOLDABLE_SHOTS=1) against `apps/desktop/tools/mock_server.py`: portrait (a centred column, one pane) and
/// landscape (two panes): the chat with the work list beside it, then the connectors and 마이 tabs with a page open in the second
/// pane. Other variables as KeloScreensTests (KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS).
final class KeloFoldableShotsTests: KeloShotCase {

    /// The first row of the open tab's list: a button on the left side, below the header, that is not a tab.
    func openFirstRow(maxX: CGFloat) {
        let rows = app.buttons.allElementsBoundByIndex.filter {
            let f = $0.frame
            return $0.isHittable && !$0.identifier.hasPrefix("kelo.") && f.minY > 160 && f.maxX <= maxX && f.height > 40
        }
        guard let row = rows.first else { XCTFail("no row to open\n\(app.debugDescription)"); return }
        row.tap()
    }

    func testFoldable() throws {
        guard env["KELO_FOLDABLE_SHOTS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("KELO_FOLDABLE_SHOTS, KELO_SERVER and KELO_KEY are needed")
        }
        XCUIDevice.shared.orientation = .portrait
        launchApp()
        signIn(key)
        pause(2)
        shot("portrait-chat")
        tap(element("kelo.tab.connectors"), "connectors tab")
        pause(1.5)
        shot("portrait-connectors")
        tap(element("kelo.tab.my"), "my tab")
        pause(1.5)
        shot("portrait-my")

        XCUIDevice.shared.orientation = .landscapeLeft
        pause(2)
        tap(element("kelo.tab.chat"), "chat tab")
        pause(2)
        shot("landscape-chat")
        let width = app.windows.firstMatch.frame.width
        tap(element("kelo.tab.connectors"), "connectors tab")
        pause(1.5)
        shot("landscape-connectors")
        openFirstRow(maxX: width * 0.6)
        pause(2)
        shot("landscape-connectors-detail")
        tap(element("kelo.tab.my"), "my tab")
        pause(1.5)
        shot("landscape-my")
        openFirstRow(maxX: width * 0.6)
        pause(2)
        shot("landscape-my-detail")
        tap(element("kelo.tab.goals"), "work tab")
        pause(1.5)
        shot("landscape-goals")
        XCUIDevice.shared.orientation = .portrait
    }
}
