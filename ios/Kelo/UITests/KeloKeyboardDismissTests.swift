import XCTest

/// The keyboard of the chat goes away again: a tap on the messages, and a downward drag, with a short chat (the mock's few messages,
/// which do not fill the screen) and with an empty one. Run through ios.sh with
///   --only KeloUITests/KeloKeyboardDismissTests
/// against desktop/tools/mock_server.py (the walk's variables: KELO_SERVER, KELO_KEY, KELO_LANG). Without a server the tests skip.
/// The empty-chat tests delete the mock's history first, so they run after the short-chat ones (names sort that way).
final class KeloKeyboardDismissTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var app: XCUIApplication!

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }

    override func setUp() { continueAfterFailure = false }

    private func element(_ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR identifier == %@", label, label)).firstMatch
    }

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    private func launch() throws {
        guard env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no mock server given") }
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
    }

    /// My -> delete the chat history -> back to the chat (as the screen walk does)
    private func emptyTheChat() {
        element("kelo.tab.my").tap()
        pause(2)
        let clear = element(w("Delete chat history", "대화 기록 삭제"))
        var n = 0
        while (!clear.exists || !clear.isHittable) && n < 8 { app.swipeUp(); n += 1 }
        XCTAssertTrue(clear.exists, "no clear row\n\(app.debugDescription)")
        clear.tap()
        let del = app.buttons.matching(NSPredicate(format: "label == %@", w("Delete", "삭제"))).firstMatch
        XCTAssertTrue(del.waitForExistence(timeout: 5), "no confirm button")
        del.tap()
        pause(1.5)
        element("kelo.tab.chat").tap()
        pause(2)
    }

    private func openKeyboard() {
        let composer = element("kelo.composer")
        composer.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 8), "the keyboard did not come up after tapping the composer")
        pause(0.8)
    }

    private func assertKeyboardGone(_ what: String) {
        let gone = NSPredicate(format: "exists == false")
        let wait = XCTNSPredicateExpectation(predicate: gone, object: app.keyboards.firstMatch)
        XCTAssertEqual(XCTWaiter().wait(for: [wait], timeout: 6), .completed, "the keyboard stayed after \(what)")
    }

    /// a tap on the empty part of the message area (well above the composer, clear of the name tag)
    private func tapMessageArea() {
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
    }

    private func dragDown() {
        let from = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25))
        let to = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.55))
        from.press(forDuration: 0.05, thenDragTo: to)
    }

    func testShortChatTapDismisses() throws {
        try launch()
        openKeyboard()
        tapMessageArea()
        assertKeyboardGone("a tap on the messages")
    }

    func testShortChatDragDismisses() throws {
        try launch()
        openKeyboard()
        dragDown()
        assertKeyboardGone("a downward drag over the messages")
    }

    func testZEmptyChatTapDismisses() throws {
        try launch()
        emptyTheChat()
        openKeyboard()
        tapMessageArea()
        assertKeyboardGone("a tap on the empty chat")
    }

    func testZEmptyChatDragDismisses() throws {
        try launch()
        emptyTheChat()
        openKeyboard()
        dragDown()
        assertKeyboardGone("a downward drag over the empty chat")
    }
}
