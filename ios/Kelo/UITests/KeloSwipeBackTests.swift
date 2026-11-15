import XCTest

/// The edge swipe goes back from a sub page (the pages draw their own back bar over a hidden system bar, which used to switch the
/// swipe off). My -> Notifications, a drag from the left edge to the middle, and My is shown again. Run through ios.sh with
///   --only KeloUITests/KeloSwipeBackTests
/// Without a mock server the test skips.
final class KeloSwipeBackTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var app: XCUIApplication!

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }

    private func element(_ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR identifier == %@", label, label)).firstMatch
    }

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    func testEdgeSwipeGoesBack() throws {
        guard env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no mock server given") }
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        let connect = element(w("Connect with access key", "접속 키로 연결하기"))
        if connect.waitForExistence(timeout: 10) {
            connect.tap()
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
        pause(2)
        element("kelo.tab.my").tap()
        pause(2)

        let row = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", w("Notifications", "알림"))).firstMatch
        var n = 0
        while (!row.exists || !row.isHittable) && n < 8 { app.swipeUp(); n += 1 }
        XCTAssertTrue(row.exists, "no Notifications row\n\(app.debugDescription)")
        row.tap()
        let back = app.buttons[w("Back", "뒤로")]
        XCTAssertTrue(back.waitForExistence(timeout: 8), "the sub page did not open\n\(app.debugDescription)")
        XCTAssertFalse(element("kelo.tab.my").isHittable, "the tab bar is still there on the sub page")

        let from = app.coordinate(withNormalizedOffset: CGVector(dx: 0.0, dy: 0.5)).withOffset(CGVector(dx: 2, dy: 0))
        let to = app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5))
        from.press(forDuration: 0.1, thenDragTo: to, withVelocity: .fast, thenHoldForDuration: 0)

        let gone = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: back)
        XCTAssertEqual(XCTWaiter().wait(for: [gone], timeout: 6), .completed, "the edge swipe did not go back")
        XCTAssertTrue(element("kelo.tab.my").waitForExistence(timeout: 5) && element("kelo.tab.my").isHittable, "My is not shown again")
    }
}
