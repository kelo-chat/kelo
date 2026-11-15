import XCTest

/// Screens of the storage warning and the cleanup screen (KELO_STORAGE=1) against the real server with a filled storage
/// (tools/storage_demo_server.py: the cap at 83% / 96%, or the server's own disk almost full): 마이's row with the warning, the
/// resources page's warning box with 정리하기, the cleanup screen, the confirm dialog and the result. KELO_CLEANUP=0 stops after the
/// resources page (the host-disk walk). Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS
/// (screens are named `<tag>-<name>.png`). Without KELO_STORAGE the test is skipped.
final class KeloStorageShotsTests: XCTestCase {
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

    func testStorage() throws {
        guard env["KELO_STORAGE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no storage walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
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
