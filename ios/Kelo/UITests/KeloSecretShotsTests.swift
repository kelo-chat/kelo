import XCTest

/// Screens of the "this looks like a key or password" sheet (KELO_SECRETS=1), against desktop/tools/mock_server.py `--secrets`
/// (the real server's secret check): a message with an Anthropic key is typed and sent, the server holds it back with 409
/// `secret_in_message`, and the sheet asks (Save in Keychain / Send as is / Edit); then Save opens its second step with the key
/// already filled in and hidden. Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS
/// (screens are named `<tag>-secret-ask.png` / `<tag>-secret-save.png`), KELO_HOST_SHOT=1 (see `hostShot`). Without KELO_SECRETS
/// the test is skipped.
final class KeloSecretShotsTests: XCTestCase {
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

    func testSecretSheet() throws {
        guard env["KELO_SECRETS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no secret walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        signIn(key)
        pause(2)

        // a sample key in the shape of an Anthropic API key (not a real one)
        let message = w("My Anthropic key is sk-ant-api03-Zq7xW2mK9pL4vN8rT1yB6cD3fG5hJ0sA — please connect it",
                        "Anthropic 키는 sk-ant-api03-Zq7xW2mK9pL4vN8rT1yB6cD3fG5hJ0sA 이거야. 이걸로 연결해줘")
        let composer = element("kelo.composer")
        tap(composer, "composer")
        composer.typeText(message)
        pause(0.5)
        // a fresh simulator's keyboard may show its swipe-typing tip over the screen first
        let tip = app.buttons["Continue"]
        if tip.exists { tip.tap(); pause(0.5) }
        tap(element("kelo.send"), "send")

        // the server holds the message back: the sheet asks
        let save = element("kelo.secret.save")
        if !save.waitForExistence(timeout: 8) {
            if tip.exists { tip.tap(); pause(0.5) }
            if element("kelo.send").isHittable { element("kelo.send").tap() }
        }
        XCTAssertTrue(save.waitForExistence(timeout: 20), "the secret sheet did not open\n\(app.debugDescription)")
        XCTAssertTrue(element("kelo.secret.sendAsIs").exists, "no Send as is")
        XCTAssertTrue(element("kelo.secret.edit").exists, "no Edit")
        pause(1.5)
        shot("secret-ask")
        hostShot("secret-ask")

        // Save: the second step with the key already in its field, hidden
        save.tap()
        let saveNow = element("kelo.secret.saveNow")
        XCTAssertTrue(saveNow.waitForExistence(timeout: 10), "the save step did not open\n\(app.debugDescription)")
        let value = element("kelo.secret.value")
        XCTAssertTrue(value.waitForExistence(timeout: 5), "no value field")
        let shownValue = (value.value as? String) ?? ""
        XCTAssertFalse(shownValue.contains("Zq7xW2mK9pL4"), "the key shows in clear text")
        XCTAssertTrue(shownValue.contains("•"), "the key is not filled in (field value: \(shownValue.count) characters)")
        pause(1.5)
        shot("secret-save")
        hostShot("secret-save")
    }

    /// iOS leaves a secure field's dots out of the test runner's own screenshot (the field draws in a capture-protected layer), so
    /// with KELO_HOST_SHOT=1 the test also asks the Mac for one: it writes `<tag>-<name>.want` into KELO_SHOTS and holds the screen
    /// until `<tag>-<name>.host.png` shows up (the Mac takes it with `xcrun simctl io <device> screenshot`), at most 60 s.
    private func hostShot(_ name: String) {
        guard env["KELO_HOST_SHOT"] == "1", let dir = env["KELO_SHOTS"] else { return }
        let base = URL(fileURLWithPath: dir).appendingPathComponent("\(tag)-\(name)")
        try? Data().write(to: base.appendingPathExtension("want"))
        let done = URL(fileURLWithPath: base.path + ".host.png")
        let end = Date().addingTimeInterval(60)   // `simctl io` can take its time to return while other simulators run
        while !FileManager.default.fileExists(atPath: done.path) && Date() < end { pause(0.25) }
        XCTAssertTrue(FileManager.default.fileExists(atPath: done.path), "the Mac took no screenshot of \(name)")
    }
}
