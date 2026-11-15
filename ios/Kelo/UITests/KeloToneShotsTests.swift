import XCTest

/// 마이 → 에이전트 → 말투 (KELO_TONE=1, CATCHUP.md §6) against `desktop/tools/mock_server.py`: the page with 공손 chosen (an older or a
/// fresh server), 친근 tapped and saved, then the tone changed behind the page's back (as "반말로 해 줘" / "존댓말 써" in the chat does
/// through set_tone) and shown by the status poll without a reload. KELO_DARK=1 shoots in dark mode (the simulator's appearance is
/// set back to light at the end). Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS
/// (screens are named `<tag>-<name>.png`). Without KELO_TONE the test is skipped.
final class KeloToneShotsTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var tag: String { env["KELO_TAG"] ?? lang }
    private var app: XCUIApplication!

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }

    override func setUp() { continueAfterFailure = true }

    override func tearDown() {
        if env["KELO_DARK"] == "1" { XCUIDevice.shared.appearance = .light }
    }

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
        var n = 0
        while !e.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
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

    /// PUT /api/profile {"tone"} straight to the mock, as the agent's set_tone does on the real server.
    private func setToneOnServer(_ tone: String, key: String) {
        var r = URLRequest(url: URL(string: env["KELO_SERVER"]! + "/api/profile")!)
        r.httpMethod = "PUT"
        r.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = Data(#"{"tone":"\#(tone)"}"#.utf8)
        let done = expectation(description: "PUT tone")
        URLSession.shared.dataTask(with: r) { _, resp, _ in
            XCTAssertEqual((resp as? HTTPURLResponse)?.statusCode, 200)
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 10)
    }

    private func waitSelected(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 15) {
        let ok = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isSelected == true"), object: e)
        XCTAssertEqual(XCTWaiter().wait(for: [ok], timeout: timeout), .completed, "\(what) not chosen\n\(app.debugDescription)")
    }

    func testTone() throws {
        guard env["KELO_TONE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no tone walk asked") }
        XCUIDevice.shared.appearance = env["KELO_DARK"] == "1" ? .dark : .light
        setToneOnServer("polite", key: key)   // a fresh start whatever an earlier run left
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)

        tap(element("kelo.tab.my"), "My tab")
        pause(1)
        let title = w("Agent", "에이전트")
        tap(app.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", title, title + ",")).firstMatch, "agent row")
        let friendly = element("kelo.tone.friendly")
        let polite = element("kelo.tone.polite")
        XCTAssertTrue(polite.waitForExistence(timeout: 15), "no tone rows\n\(app.debugDescription)")
        waitSelected(polite, "polite")
        XCTAssertFalse(friendly.isSelected)
        pause(1.5)
        shot("agent-tone")

        // 친근 tapped: PUT {"tone":"friendly"}, the saved profile shows it
        tap(friendly, "friendly row")
        waitSelected(friendly, "friendly after tap")
        XCTAssertFalse(polite.isSelected)
        pause(1)
        shot("agent-tone-friendly")

        // changed elsewhere ("존댓말 써" in the chat): the status poll brings it while the page stays open
        setToneOnServer("polite", key: key)
        waitSelected(polite, "polite from the status poll", timeout: 20)
        XCTAssertFalse(friendly.isSelected)
        pause(1)
        shot("agent-tone-polled")

        // the page reloads the profile each time it opens
        setToneOnServer("friendly", key: key)
        tap(element(w("Back", "뒤로")), "back")
        pause(1)
        tap(app.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", title, title + ",")).firstMatch, "agent row again")
        waitSelected(element("kelo.tone.friendly"), "friendly after reopening", timeout: 10)
        setToneOnServer("polite", key: key)   // leave the mock as it was
    }
}
