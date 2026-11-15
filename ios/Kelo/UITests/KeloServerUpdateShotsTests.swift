import XCTest

/// Screens of the server update (KELO_SERVERUPDATE=1) against a pretend server that also answers /api/server-update as the owner:
/// the highlighted card at the top of 마이, the page with the newer version and its notes, the confirm dialog, the progress while the
/// server restarts (the pretend server answers 502 for a few seconds then, like a real restart behind a proxy) and the result.
/// Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS (screens are named
/// `<tag>-<name>.png`). Without KELO_SERVERUPDATE the test is skipped.
final class KeloServerUpdateShotsTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var tag: String { env["KELO_TAG"] ?? lang }
    private var app: XCUIApplication!

    /// The few labels the walk taps, in the two languages checked for narrow widths besides en / ko.
    private let other: [String: [String: String]] = [
        "de": ["Connect with access key": "Mit Zugangsschlüssel verbinden", "Connect": "Verbinden", "Back": "Zurück"],
        "th": ["Connect with access key": "เชื่อมต่อด้วยคีย์การเข้าถึง", "Connect": "เชื่อมต่อ", "Back": "กลับ"],
    ]

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : (other[lang]?[en] ?? en) }

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

    func testServerUpdate() throws {
        guard env["KELO_SERVERUPDATE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no server update walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
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
