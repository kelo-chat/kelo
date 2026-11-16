import XCTest

/// Screens of the secure entries the agent asks for (KELO_SECRET_ENTRIES=1), against apps/desktop/tools/mock_server.py
/// `--secret-entries` (three open requests: an OpenWeather key under the newest agent message, a Coupang sign-in and a card under the
/// first one). Shots: the chat at its end with the open key entry (`<tag>-entry-chat.png`), the same entry with a sample key typed
/// (`<tag>-entry-chat-typed.png`, hidden), the Keychain's "waiting for input" section at its top (`<tag>-entry-keychain.png`) and
/// one of its entries opened with Fill in (`<tag>-entry-keychain-fill.png`). The Keychain opens behind the phone's own check: with
/// Face ID enrolled the Mac sends a match (`xcrun simctl spawn <device> notifyutil -p com.apple.BiometricKit_Sim.pearl.match`).
/// Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS, KELO_BIGTEXT. Without
/// KELO_SECRET_ENTRIES the test is skipped.
final class KeloSecretEntryShotsTests: KeloShotCase {

    @discardableResult
    override func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) -> Bool {
        guard e.waitForExistence(timeout: timeout) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return false }
        e.tap()
        return true
    }

    func testSecretEntries() throws {
        guard env["KELO_SECRET_ENTRIES"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("no secure entry walk asked")
        }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        signIn(key)

        // ---- the chat: the key entry right under the agent message that asked, at the end of the list
        let field = element("kelo.secretEntry.key")
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no key entry in the chat\n\(app.debugDescription)")
        XCTAssertTrue(element("kelo.secretEntry.save").exists, "no Save")
        pause(2)
        shot("entry-chat")
        // a sample key typed (not a real one): Save turns on, the field shows dots
        let secure = app.secureTextFields.matching(identifier: "kelo.secretEntry.key").firstMatch
        if secure.exists { secure.tap() } else { field.tap() }
        app.typeText("owm-sample-0000-1111-2222")
        pause(0.8)
        app.swipeDown(velocity: .slow)   // the keyboard goes (the list dismisses it interactively)
        pause(1.5)
        shot("entry-chat-typed")

        // ---- the Keychain: "waiting for input" at its top
        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(element(w("Keychain", "키체인")), "keychain row")
        let fill = element("kelo.vault.waiting.fill")
        XCTAssertTrue(fill.waitForExistence(timeout: 40), "no waiting section (Face ID match sent?)\n\(app.debugDescription)")
        pause(2)
        shot("entry-keychain")
        fill.tap()
        pause(1.5)
        shot("entry-keychain-fill")
    }
}
