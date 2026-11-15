import XCTest

/// Screens of two settings fed by a real server run from the working tree (not the mock): 마이 → 알림 → 읽는 계정 with an Outlook
/// mailbox next to another one, and the Outlook Mail connector's detail with only the permission it was given.
/// TEST_RUNNER_KELO_SCOPE_SERVER / _KEY: that server and its access key; KELO_LANG, KELO_TAG, KELO_SHOTS as KeloScreensTests
/// (screens are named `<tag>-<name>.png`). Skipped without KELO_SCOPE_SERVER.
final class KeloScopeShotsTests: KeloShotCase {
    override var lang: String { env["KELO_LANG"] ?? "ko" }


    func testScope() throws {
        guard let server = env["KELO_SCOPE_SERVER"], let key = env["KELO_SCOPE_KEY"] else { throw XCTSkip("no scope server given") }
        launchApp()
        signIn(key, server: server)
        pause(2)

        // 마이 → 알림, down to the accounts it reads
        tap(element("kelo.tab.my"), "My tab")
        pause(1)
        tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", w("Notifications", "알림"))).firstMatch, "notifications row")
        pause(2)
        let sources = element("kelo.briefing.sources")
        var n = 0
        while !(sources.exists && sources.isHittable) && n < 10 { app.swipeUp(velocity: .slow); n += 1 }
        let outlook = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "outlook.com")).firstMatch
        n = 0
        while !(outlook.exists && outlook.isHittable) && n < 6 { app.swipeUp(velocity: .slow); n += 1 }
        pause(1)
        XCTAssertTrue(outlook.exists, "no Outlook mailbox in the accounts\n\(app.debugDescription)")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("sources")

        // 커넥터 → Outlook Mail: only the permission it was given (back to My first: the tab bar is hidden on a sub page)
        tap(app.buttons.matching(NSPredicate(format: "label == %@", w("Back", "뒤로"))).firstMatch, "back")
        pause(1.5)
        tap(element("kelo.tab.connectors"), "Connectors tab")
        pause(2)
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Outlook Mail")).firstMatch
        n = 0
        while !(row.exists && row.isHittable) && n < 6 { app.swipeUp(velocity: .slow); n += 1 }
        tap(row, "Outlook Mail row")
        pause(3)
        shot("outlook")
        let granted = element(w("Permissions granted", "허락하는 권한"))
        n = 0
        while !(granted.exists && granted.isHittable) && n < 4 { app.swipeUp(velocity: .slow); n += 1 }
        pause(1)
        XCTAssertTrue(granted.exists, "no granted permission\n\(app.debugDescription)")
        shot("outlook-permission")
    }
}
