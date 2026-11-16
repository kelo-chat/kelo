import XCTest

/// The App Store screenshots' raw captures (run by tools/store/ios_shots.sh, framed by tools/store/compose.py): the app against
/// `tools/store/store_mock.py`, one ordinary morning of one user. Saved as `<tag>-NN-name.png` in KELO_SHOTS:
///   01-handoff   a task handed off in the chat and the report it came back with
///   02-approval  a train ticket's one final order card, waiting for Allow / Deny
///   03-live      the live view of the agent's browser (a hotel comparison running in the background)
///   04-briefing  this morning's briefing card
///   05-goal      a goal's page: what you said, the plan (yours and Kelo's steps), the numbers
///   06-connectors, 07-privacy (My → Where your data lives), 08-tone (My → Agent)
/// Variables (TEST_RUNNER_ prefixed for xcodebuild): KELO_STORE_SERVER (the store mock; tools/build/ios.sh starts its own plain
/// mock for KELO_SERVER, which this test does not use), KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS, KELO_STORE_TZ (the time zone the
/// app runs in, the same one the mock was started with). Without KELO_STORE_SERVER the test is skipped.
final class KeloStoreShotsTests: KeloShotCase {
    private var server: String { env["KELO_STORE_SERVER"] ?? "" }

    private func starting(_ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", label, label + ",")).firstMatch
    }

    /// POST to the store mock (no key needed for /mock/...).
    private func mock(_ path: String, _ body: String) {
        var r = URLRequest(url: URL(string: server + path)!)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = Data(body.utf8)
        let done = expectation(description: "POST \(path)")
        URLSession.shared.dataTask(with: r) { _, resp, _ in
            XCTAssertEqual((resp as? HTTPURLResponse)?.statusCode, 200)
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 10)
    }

    private func launch() {
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if let tz = env["KELO_STORE_TZ"] { app.launchEnvironment["TZ"] = tz }
        app.launch()
    }

    /// A fresh app run with the chat the mock was just given (the app reads it on start).
    private func relaunch(chat: String, key: String) {
        mock("/mock/chat", #"{"chat":"\#(chat)"}"#)
        XCUIDevice.shared.press(.home)   // to the background first, so the app keeps what it saves on the way out
        pause(2)
        app.terminate()
        launch()
        signIn(key, server: server)   // (only when it opens on the start screen)
        pause(4)
    }


    /// Drag from the messages down into the keyboard (the chat dismisses it interactively); the list springs back to its end.
    private func hideKeyboard() {
        for _ in 0..<2 {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35))
                .press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.97)),
                       withVelocity: .slow, thenHoldForDuration: 0.1)
            pause(1.5)
            if !app.keyboards.firstMatch.exists { break }
        }
    }

    private func back() {
        let b = element(w("Back", "뒤로"))
        if b.exists && b.isHittable { b.tap() } else { app.swipeRight() }
        pause(1.2)
    }

    func testStoreShots() throws {
        guard !server.isEmpty, let key = env["KELO_KEY"] else { throw XCTSkip("no store mock given") }
        XCUIDevice.shared.appearance = .light
        mock("/mock/chat", #"{"chat":"briefing"}"#)
        launch()
        signIn(key, server: server)
        pause(4)

        // 04: this morning's briefing (the chat as it opens)
        shot("04-briefing")

        // 01: a task handed off earlier and its report
        relaunch(chat: "handoff", key: key)
        shot("01-handoff")

        // 02: the order card of a ticket booked from the chat
        relaunch(chat: "empty", key: key)
        let composer = element("kelo.composer")
        composer.tap()
        pause(1)
        let intro = app.buttons["Continue"]   // a fresh simulator's swipe-typing introduction covers the keyboard
        if intro.exists { intro.tap(); pause(1) }
        composer.typeText(w("Book the 6:05 AM train to Boston on the 17th", "17일 아침 7시 부산 가는 KTX 예매해 줘"))
        tap(element("kelo.send"), "send")
        let later = app.buttons.matching(NSPredicate(format: "label == %@", w("Later", "나중에"))).firstMatch
        if later.waitForExistence(timeout: 4) { later.tap() }
        let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("Allow", "허용"))).firstMatch
        XCTAssertTrue(allow.waitForExistence(timeout: 20), "no order card\n\(app.debugDescription)")
        hideKeyboard()
        pause(2)
        shot("02-approval")
        allow.tap()   // answered, so the character no longer waits on the next pages
        pause(3)

        // 03: the live view of the background job's browser (Work page → Live view)
        tap(element("kelo.header"), "header")
        pause(2.5)
        tap(element(w("Live view", "실시간 보기")), "live view")
        pause(4)
        shot("03-live")
        back()
        back()

        // 05: a goal's page
        tap(element("kelo.tab.goals"), "goals tab")
        pause(2)
        tap(element(w("Read 2 books a month", "한 달에 책 2권 읽기")), "goal row")
        pause(3)
        shot("05-goal")
        back()

        // 06: connectors
        tap(element("kelo.tab.connectors"), "connectors tab")
        pause(4)
        shot("06-connectors")

        // 07: My → Where your data lives
        tap(element("kelo.tab.my"), "my tab")
        pause(2)
        tap(starting(w("Where your data lives", "내 정보가 머무는 곳")), "privacy row")
        pause(2.5)
        shot("07-privacy")
        back()

        // 08: My → Agent (the tone)
        app.swipeDown()
        app.swipeDown()
        pause(1)
        tap(app.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", w("Agent", "에이전트"), w("Agent", "에이전트") + ",")).firstMatch,
            "agent row")
        XCTAssertTrue(element("kelo.tone.polite").waitForExistence(timeout: 10), "no tone rows\n\(app.debugDescription)")
        pause(2)
        shot("08-tone")
    }
}
