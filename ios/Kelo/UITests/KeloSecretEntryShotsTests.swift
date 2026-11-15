import XCTest

/// Screens of the secure entries the agent asks for (KELO_SECRET_ENTRIES=1), against desktop/tools/mock_server.py
/// `--secret-entries` (three open requests: an OpenWeather key under the newest agent message, a Coupang sign-in and a card under the
/// first one). Shots: the chat at its end with the open key entry (`<tag>-entry-chat.png`), the same entry with a sample key typed
/// (`<tag>-entry-chat-typed.png`, hidden), the Keychain's "waiting for input" section at its top (`<tag>-entry-keychain.png`) and
/// one of its entries opened with Fill in (`<tag>-entry-keychain-fill.png`). The Keychain opens behind the phone's own check: with
/// Face ID enrolled the Mac sends a match (`xcrun simctl spawn <device> notifyutil -p com.apple.BiometricKit_Sim.pearl.match`).
/// Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS, KELO_BIGTEXT. Without
/// KELO_SECRET_ENTRIES the test is skipped.
final class KeloSecretEntryShotsTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var tag: String { env["KELO_TAG"] ?? lang }
    private var app: XCUIApplication!

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }

    override func setUp() { continueAfterFailure = true }

    private func shot(_ name: String) {
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let a = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        a.name = "\(tag)-\(name)"
        a.lifetime = .keepAlways
        add(a)
        if let dir = env["KELO_SHOTS"] {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(tag)-\(name).png"))
        }
    }

    private func element(_ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR identifier == %@", label, label)).firstMatch
    }

    @discardableResult
    private func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) -> Bool {
        guard e.waitForExistence(timeout: timeout) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return false }
        e.tap()
        return true
    }

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    private func signIn(_ key: String) {
        if element(w("Connect with access key", "접속 키로 연결하기")).waitForExistence(timeout: 10) {
            tap(element(w("Connect with access key", "접속 키로 연결하기")), "connect with key")
            let server = app.textFields.firstMatch
            server.tap()
            let cur = (server.value as? String) ?? ""
            if !cur.isEmpty && cur != server.placeholderValue {
                server.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: cur.count))
            }
            server.typeText(env["KELO_SERVER"]!)
            let secure = app.secureTextFields.firstMatch
            secure.tap()
            secure.typeText(key)
            let submit = app.buttons.matching(NSPredicate(format: "label == %@", w("Connect", "연결"))).firstMatch
            if !submit.isHittable { app.swipeUp() }
            submit.tap()
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 25), "chat did not open\n\(app.debugDescription)")
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
