import XCTest

/// Screens of the server update (KELO_SERVERUPDATE=1) against a pretend server that also answers /api/server-update as the owner:
/// the highlighted card at the top of 마이, the page with the newer version and its notes, the confirm dialog, the progress while the
/// server restarts (the pretend server answers 502 for a few seconds then, like a real restart behind a proxy) and the result.
/// Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS (screens are named
/// `<tag>-<name>.png`). Without KELO_SERVERUPDATE the test is skipped.
final class KeloServerUpdateShotsTests: KeloShotCase {

    /// The few labels the walk taps, in the two languages checked for narrow widths besides en / ko.
    private let other: [String: [String: String]] = [
        "de": ["Connect with access key": "Mit Zugangsschlüssel verbinden", "Connect": "Verbinden", "Back": "Zurück"],
        "th": ["Connect with access key": "เชื่อมต่อด้วยคีย์การเข้าถึง", "Connect": "เชื่อมต่อ", "Back": "กลับ"],
    ]

    override func w(_ en: String, _ ko: String, _ de: String? = nil) -> String { lang == "ko" ? ko : (other[lang]?[en] ?? en) }

    func testServerUpdate() throws {
        guard env["KELO_SERVERUPDATE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no server update walk asked") }
        launchApp()
        signIn(key)

        tap(element("kelo.tab.my"), "My tab")
        let card = element("kelo.serverUpdate.card")
        XCTAssertTrue(card.waitForExistence(timeout: 15), "no update card on My\n\(app.debugDescription)")
        pause(1.5)
        shot("card")

        tap(card, "update card")
        let update = element("kelo.serverUpdate.update")
        XCTAssertTrue(update.waitForExistence(timeout: 10), "no update button\n\(app.debugDescription)")
        pause(1.2)
        shot("page")

        tap(update, "update button")
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 5), "no confirm dialog\n\(app.debugDescription)")
        pause(0.8)
        shot("confirm")
        alert.buttons.element(boundBy: alert.buttons.count - 1).tap()   // Update (Cancel comes first)

        let progress = element("kelo.serverUpdate.progress")
        XCTAssertTrue(progress.waitForExistence(timeout: 10), "no progress\n\(app.debugDescription)")
        pause(7)   // into the download step
        shot("progress")
        // the pretend server is away for a few seconds during the restart: the page says it reconnects
        let reconnecting = element("kelo.serverUpdate.reconnecting")
        if reconnecting.waitForExistence(timeout: 30) {
            pause(0.6)
            shot("reconnecting")
        }

        let result = element("kelo.serverUpdate.result")
        XCTAssertTrue(result.waitForExistence(timeout: 60), "no result\n\(app.debugDescription)")
        pause(1.5)
        shot("result")

        // back on My: the card is gone after a good update, or tells what went wrong
        tap(element(w("Back", "뒤로")), "back")
        pause(2)
        shot("my-after")
    }
}
