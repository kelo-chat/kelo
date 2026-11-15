import XCTest

/// Learned message hours (KELO_HOURS=suggest|accept|notify), against desktop/tools/mock_server.py (a night owl: 10:00 – 01:00 next
/// day, set 08:00 – 22:00): the review row on 마이 → 기록 ("알림 받는 시간 제안" with "지금대로 두기" / "이 시간으로 바꾸기"); with
/// `accept` the row after "이 시간으로 바꾸기" and then 마이 → 알림 saying the hours were learned, with "쓰는 시간에 맞춰 알림 시간 바꾸기"; `notify` only
/// that page. Other variables as KeloScreensTests (KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS); screens are named
/// `<tag>-hours-suggest.png`, `<tag>-hours-accepted.png`, `<tag>-hours-learned.png`. Without KELO_HOURS the test is skipped.
final class KeloHoursShotsTests: XCTestCase {
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

    private func scrollTo(_ e: XCUIElement, _ what: String) {
        var n = 0
        while !(e.exists && e.isHittable) && n < 12 { app.swipeUp(velocity: .slow); n += 1 }
        XCTAssertTrue(e.exists, "\(what) not found\n\(app.debugDescription)")
    }

    private func openNotify() {
        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(element(w("Notifications", "알림")), "notifications row")
        pause(2.5)
        let learned = containing(w("Learned from your use", "사용 패턴에서 배운 시간"))
        scrollTo(learned, "learned line")
        let auto = containing(w("Move alert hours to when you use Kelo", "쓰는 시간에 맞춰 알림 시간 바꾸기"))
        scrollTo(auto, "adjust automatically switch")
        pause(1.2)
        shot("hours-learned")
    }

    func testHours() throws {
        guard let mode = env["KELO_HOURS"], env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no hours walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)
        if mode == "notify" { openNotify(); return }

        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(containing(w("Weekly journal", "주간 일지")), "records row")
        pause(2.5)
        let accept = app.buttons.matching(NSPredicate(format: "label == %@", w("Use these hours", "이 시간으로 바꾸기"))).firstMatch
        scrollTo(accept, "use these hours button")
        if accept.exists && accept.frame.maxY > app.frame.height * 0.8 { app.swipeUp(velocity: .slow) }
        XCTAssertTrue(containing(w("Suggested message hours", "알림 받는 시간 제안")).exists, "heading missing\n\(app.debugDescription)")
        pause(1.2)
        shot("hours-suggest")
        guard mode == "accept" else { return }
        accept.tap()
        XCTAssertTrue(containing(w("Message hours changed", "알림 받는 시간을 바꿨어요")).waitForExistence(timeout: 5))
        shot("hours-accepted")
        pause(3.5)
        XCTAssertFalse(containing(w("Suggested message hours", "알림 받는 시간 제안")).exists, "the row should go after the choice")
        tap(element(w("Back", "뒤로")), "back")
        pause(1)
        openNotify()
    }
}
