import XCTest

/// The thinking level of a custom OpenAI-compatible endpoint (KELO_EFFORT=1), against desktop/tools/mock_server.py started with
/// KELO_MOCK_CUSTOM_EFFORT=levels (mock_parts/custom_effort.py: the main model is K-EXAONE on a custom endpoint, level 높음).
/// Screens: `<tag>-effort-chip.png` (the chat's model chip with the level), `<tag>-effort-sheet.png` (the model sheet, levels open),
/// `<tag>-effort-model.png` (My → Model with the 생각 수준 field). Other variables as KeloScreensTests. Skipped without KELO_EFFORT.
final class KeloEffortShotsTests: XCTestCase {
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

    private func signIn(_ key: String) {
        if element(w("Connect with access key", "접속 키로 연결하기")).waitForExistence(timeout: 10) {
            element(w("Connect with access key", "접속 키로 연결하기")).tap()
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
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for target in [app!, springboard] {
            for label in ["Not Now", "지금 안 함"] where target.buttons[label].waitForExistence(timeout: 1) { target.buttons[label].tap(); pause(1) }
        }
    }

    func testCustomEffort() throws {
        guard env["KELO_EFFORT"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no effort walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)
        pause(3)

        let chip = element("kelo.modelChip")
        XCTAssertTrue(chip.waitForExistence(timeout: 10))
        let high = w("High", "높음")
        XCTAssertTrue(chip.label.contains(high), "the chip shows no level: \(chip.label)")
        shot("effort-chip")

        if !chip.isHittable { app.swipeDown() }
        chip.tap()
        pause(1.2)
        let levelRow = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", w("Thinking level", "생각 수준"))).firstMatch
        if levelRow.waitForExistence(timeout: 5) { levelRow.tap(); pause(1.2) }
        XCTAssertTrue(element(w("Max", "최대")).waitForExistence(timeout: 5), "no custom levels in the sheet\n\(app.debugDescription)")
        shot("effort-sheet")
        let close = element(w("Close", "닫기"))
        if close.exists { close.tap() } else { app.swipeDown() }
        pause(1)

        element("kelo.tab.my").tap()
        pause(1.5)
        let model = app.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", w("Model", "모델"), w("Model", "모델") + ",")).firstMatch
        var n = 0
        while (!model.exists || !model.isHittable) && n < 8 { app.swipeUp(); n += 1 }
        model.tap()
        pause(2.5)
        let field = element(w("Thinking level", "생각 수준"))
        n = 0
        while (!field.exists || field.frame.maxY > app.frame.height * 0.7) && n < 6 {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.75))
                .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.55)),
                       withVelocity: .slow, thenHoldForDuration: 0.3)
            pause(0.5)
            n += 1
        }
        XCTAssertTrue(field.exists, "no thinking level on My → Model\n\(app.debugDescription)")
        pause(0.8)
        shot("effort-model")
    }
}
