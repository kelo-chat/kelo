import XCTest

/// Screens of the storage warning and the cleanup screen (KELO_STORAGE=1) against the real server with a filled storage
/// (tools/storage_demo_server.py: the cap at 83% / 96%, or the server's own disk almost full): 마이's row with the warning, the
/// resources page's warning box with 정리하기, the cleanup screen, the confirm dialog and the result. KELO_CLEANUP=0 stops after the
/// resources page (the host-disk walk). Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS
/// (screens are named `<tag>-<name>.png`). Without KELO_STORAGE the test is skipped.
final class KeloStorageShotsTests: KeloShotCase {

    func testStorage() throws {
        guard env["KELO_STORAGE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no storage walk asked") }
        launchApp()
        signIn(key)

        tap(element("kelo.tab.my"), "My tab")
        let row = element("kelo.resources.row")
        XCTAssertTrue(row.waitForExistence(timeout: 15), "no resources row on My\n\(app.debugDescription)")
        pause(2)
        shot("my")

        tap(row, "resources row")
        let warning = element("kelo.resources.warning")
        XCTAssertTrue(warning.waitForExistence(timeout: 15), "no storage warning\n\(app.debugDescription)")
        pause(1.5)
        shot("resources")
        if env["KELO_CLEANUP"] == "0" { return }

        tap(element("kelo.resources.cleanup"), "clean up")
        let uploads = element("kelo.cleanup.group.uploads")
        XCTAssertTrue(uploads.waitForExistence(timeout: 15), "no cleanup list\n\(app.debugDescription)")
        pause(1.5)
        shot("cleanup")

        tap(uploads, "old uploads group")
        tap(element("kelo.cleanup.group.backups"), "older backups group")
        pause(0.6)
        shot("cleanup-selected")
        tap(element("kelo.cleanup.delete"), "delete")
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 5), "no confirm dialog\n\(app.debugDescription)")
        pause(0.8)
        shot("confirm")
        alert.buttons[w("Delete", "지우기")].tap()
        let result = element("kelo.cleanup.result")
        XCTAssertTrue(result.waitForExistence(timeout: 20), "no result\n\(app.debugDescription)")
        app.swipeDown()
        pause(1.5)
        shot("result")
    }
}
