import XCTest

/// The compact card and the bottom sheet for a login and for a service key (KELO_LOGIN_SHEET=1), against a mock server started with
/// `--secret-entries` whose sign-in request carries `username_hint`. Shots: the key card at the chat's end (`<tag>-login-key-card`),
/// the key sheet (`-login-key-sheet`), the sign-in card (`-login-card`), its sheet with the prefilled user name (`-login-sheet`),
/// with Save still off. Variables as KeloScreensTests. Without
/// KELO_LOGIN_SHEET the test is skipped.
final class KeloLoginSheetShotsTests: KeloShotCase {

    /// The card's open button: the nearest one under its title.
    private func openBelow(_ title: XCUIElement) -> XCUIElement {
        let all = app.buttons.matching(identifier: "kelo.secretEntry.open").allElementsBoundByIndex
        let below = all.filter { $0.frame.minY > title.frame.maxY }.sorted { $0.frame.minY < $1.frame.minY }
        return below.first ?? all[0]
    }

    func testLoginSheet() throws {
        guard env["KELO_LOGIN_SHEET"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("no login sheet walk asked")
        }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        signIn(key)

        // the service key: the compact card at the end of the chat, its sheet, Cancel only closes it
        let open = app.buttons.matching(identifier: "kelo.secretEntry.open")
        XCTAssertTrue(open.firstMatch.waitForExistence(timeout: 25), "no entry card\n\(app.debugDescription)")
        pause(2)
        shot("login-key-card")
        let keyTitle = element(w("Add key", "키 추가"))
        XCTAssertTrue(keyTitle.waitForExistence(timeout: 5), "no key card title")
        open.allElementsBoundByIndex.last!.tap()
        XCTAssertTrue(element("kelo.secretEntry.key").waitForExistence(timeout: 10), "no key sheet")
        pause(1)
        shot("login-key-sheet")
        tap(element("kelo.secretEntry.sheetCancel"), "sheet Cancel")
        pause(1)
        XCTAssertTrue(keyTitle.exists, "Cancel must leave the card")

        // the sign-in: scroll up to its card
        let loginTitle = element(w("Add password", "비밀번호 추가"))
        var n = 0
        while !loginTitle.waitForExistence(timeout: 1) && n < 15 { app.swipeDown(velocity: .slow); n += 1 }
        XCTAssertTrue(loginTitle.waitForExistence(timeout: 5), "no sign-in card")
        pause(1)
        shot("login-card")
        openBelow(loginTitle).tap()
        let user = element("kelo.secretEntry.user")
        XCTAssertTrue(user.waitForExistence(timeout: 10), "no sign-in sheet")
        pause(1)
        XCTAssertTrue(app.textFields.matching(NSPredicate(format: "value == %@", "kim@example.com")).firstMatch.exists, "the user name is not prefilled")
        XCTAssertFalse(element("kelo.secretEntry.save").isEnabled, "Save must be off until both fields are filled")
        shot("login-sheet")
        // typing into the secure field needs a software keyboard (the simulator's hardware keyboard drops it): the Save flow is unit-tested
    }
}
