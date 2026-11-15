import XCTest

/// Screens of two settings fed by a real server run from the working tree (not the mock): 마이 → 알림 → 읽는 계정 with an Outlook
/// mailbox next to another one, and the Outlook Mail connector's detail with only the permission it was given.
/// TEST_RUNNER_KELO_SCOPE_SERVER / _KEY: that server and its access key; KELO_LANG, KELO_TAG, KELO_SHOTS as KeloScreensTests
/// (screens are named `<tag>-<name>.png`). Skipped without KELO_SCOPE_SERVER.
final class KeloScopeShotsTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "ko" }
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
        var n = 0
        while !e.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        e.tap()
        return true
    }

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    private func signIn(_ server: String, _ key: String) {
        if element(w("Connect with access key", "접속 키로 연결하기")).waitForExistence(timeout: 10) {
            tap(element(w("Connect with access key", "접속 키로 연결하기")), "connect with key")
            let field = app.textFields.firstMatch
            field.tap()
            let cur = (field.value as? String) ?? ""
            if !cur.isEmpty && cur != field.placeholderValue {
                field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: cur.count))
            }
            field.typeText(server)
            let secure = app.secureTextFields.firstMatch
            secure.tap()
            secure.typeText(key)
            let submit = app.buttons.matching(NSPredicate(format: "label == %@", w("Connect", "연결"))).firstMatch
            if !submit.isHittable { app.swipeUp() }
            submit.tap()
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 25), "chat did not open\n\(app.debugDescription)")
    }

    func testScope() throws {
        guard let server = env["KELO_SCOPE_SERVER"], let key = env["KELO_SCOPE_KEY"] else { throw XCTSkip("no scope server given") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(server, key)
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
