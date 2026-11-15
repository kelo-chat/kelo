import XCTest

/// Keyguard screens (KELO_KEYGUARD_SHOTS=1) against `desktop/tools/mock_server.py` with its aa_keyguard part:
/// KELO_MOCK_KEYGUARD=off: 마이 → 보안 → Keyguard, turned on (the software key on the simulator), the recovery code shown once with
/// "I wrote it down", then the page with this phone in the list; KELO_MOCK_KEYGUARD=locked: the "Kelo is locked" banner over the
/// chat and what a tap on Unlock says. Other variables as KeloScreensTests (KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS).
final class KeloKeyguardShotsTests: XCTestCase {
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

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    private func tap(_ e: XCUIElement, _ what: String) {
        guard e.waitForExistence(timeout: 10) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return }
        var n = 0
        while !e.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        e.tap()
    }

    private func signIn(_ key: String) {
        let withKey = element(w("Connect with access key", "접속 키로 연결하기"))
        if withKey.waitForExistence(timeout: 10) {
            withKey.tap()
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

    func testKeyguard() throws {
        guard env["KELO_KEYGUARD_SHOTS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("KELO_KEYGUARD_SHOTS, KELO_SERVER and KELO_KEY are needed")
        }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)

        if element("kelo.keyguard.locked").waitForExistence(timeout: 12) {
            pause(1)
            shot("locked-banner")
            tap(element("kelo.keyguard.unlock"), "unlock")
            pause(3)
            shot("locked-after-unlock")
            return
        }

        // 마이 → 보안 → Keyguard, turned on
        tap(element(w("My", "마이")), "My tab")
        tap(element("Keyguard"), "Keyguard row")
        pause(1.5)
        shot("page-off")
        tap(element("kelo.keyguard.turnOn"), "turn on")
        let yes = app.alerts.buttons.matching(NSPredicate(format: "label == %@", w("Turn on", "켜기"))).firstMatch
        if yes.waitForExistence(timeout: 5) { shot("turn-on-confirm"); yes.tap() }
        XCTAssertTrue(element("kelo.keyguard.recoveryCode").waitForExistence(timeout: 15), "no recovery code\n\(app.debugDescription)")
        pause(1)
        shot("recovery-code")
        tap(element("kelo.keyguard.wroteDown"), "I wrote it down")
        pause(0.5)
        shot("recovery-code-ticked")
        tap(element(w("Done", "완료")), "done")
        pause(2)
        shot("page-on")
    }
}
