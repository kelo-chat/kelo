import XCTest

/// Screens of the memory pass on 마이 → 기록 (KELO_MEMORY=1), against desktop/tools/mock_server.py: the look-back card's
/// "기억 정리: 3개 추가, 2개 정리" line with its "바뀐 내용" link, and the sheet that link opens (lines grouped by area, the undo
/// button). Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS (screens are named
/// `<tag>-memory-line.png` / `<tag>-memory-sheet.png`). Without KELO_MEMORY the test is skipped.
final class KeloMemoryPassShotsTests: XCTestCase {
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

    private func containing(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
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

    func testMemoryPass() throws {
        guard env["KELO_MEMORY"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no memory walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)

        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(containing(w("Weekly journal", "주간 일지")), "records row")
        pause(2.5)

        // the memory line sits in the look-back card under the "last run" line
        let line = containing(w("Memory tidy-up", "기억 정리:"))
        var n = 0
        while !(line.exists && line.isHittable) && n < 12 { app.swipeUp(velocity: .slow); n += 1 }
        XCTAssertTrue(line.exists, "memory line not found\n\(app.debugDescription)")
        // bring the line a bit further up so the card below it shows too
        if line.exists && line.frame.maxY > app.frame.height * 0.75 { app.swipeUp(velocity: .slow) }
        pause(1.2)
        shot("memory-line")

        let link = app.buttons.matching(NSPredicate(format: "label == %@", w("What changed", "바뀐 내용"))).firstMatch
        tap(link, "what changed link")
        let title = element(w("What the memory tidy-up changed", "기억 정리에서 바뀐 내용"))
        XCTAssertTrue(title.waitForExistence(timeout: 10), "sheet did not open\n\(app.debugDescription)")
        pause(2)
        shot("memory-sheet")
    }
}
