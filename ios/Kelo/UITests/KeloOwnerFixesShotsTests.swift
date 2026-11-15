import XCTest

/// The owner's fixes of 2026-11-09, against desktop/tools/mock_server.py (KELO_FIXES=badge|nobadge; other variables as
/// KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS). Without KELO_FIXES the test is skipped.
/// - badge (the mock as it is: an approval and follow-ups wait): the header with its red dot, `<tag>-header-badge.png`.
/// - nobadge (the mock with KELO_MOCK_NO_ASKS=1: nothing waits, only unseen tasks and notifications): the header without a dot,
///   `<tag>-header-nobadge.png`; then 마이 → 알림 in light mode: 알림 시간's switch (`notify-hours`), 새 소식 알림 (`notify-news`),
///   읽는 계정 (`notify-sources`) and the briefing's weather line with "위치 허용" (`briefing-weather`); then in dark mode the chat scrolled
///   up to the middle of a long history (KELO_MOCK_LONG_HISTORY=1, `chat-middle-dark`), the My page's top (`my-top-dark`) and the
///   chat back at its end, the last answer just above the composer (`chat-bottom-dark`).
final class KeloOwnerFixesShotsTests: KeloShotCase {

    override func tearDown() { XCUIDevice.shared.appearance = .light }

    private func containing(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
    }

    /// Scrolls until `e` is on screen in the upper part of the page (so what follows it shows too).
    private func scrollTo(_ e: XCUIElement, _ what: String) {
        var n = 0
        while !(e.exists && e.isHittable) && n < 16 { app.swipeUp(velocity: .slow); n += 1 }
        XCTAssertTrue(e.exists, "\(what) not found\n\(app.debugDescription)")
        var m = 0
        while e.exists && e.frame.minY > app.frame.height * 0.45 && m < 4 {
            app.swipeUp(velocity: .slow); m += 1
        }
        pause(1)
    }

    func testFixes() throws {
        guard let mode = env["KELO_FIXES"], env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no fixes walk asked") }
        XCUIDevice.shared.appearance = .light
        launchApp()
        signIn(key)
        pause(5)   // a few status polls: the dot follows GET /api/agent/status
        let header = element("kelo.header")
        XCTAssertTrue(header.waitForExistence(timeout: 10))
        let words = header.label
        if mode == "badge" {
            XCTAssertTrue(words.contains(w("waiting for you", "답을 기다리는 일")), "the header should say something waits: \(words)")
            shot("header-badge")
            return
        }
        XCTAssertFalse(words.contains(w("waiting for you", "답을 기다리는 일")), "nothing waits, the header must not say so: \(words)")
        shot("header-nobadge")

        // 마이 → 알림
        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(element(w("Notifications", "알림")), "notifications row")
        pause(2.5)
        scrollTo(containing(w("Move alert hours to when you use Kelo", "쓰는 시간에 맞춰 알림 시간 바꾸기")), "hours switch")
        shot("notify-hours")
        scrollTo(element(w("News alerts", "새 소식 알림")), "news alerts")
        XCTAssertTrue(containing(w("Every 2 hours", "2시간마다")).exists, "the about text says how often\n\(app.debugDescription)")
        shot("notify-news")
        scrollTo(element(w("What it reads", "읽는 계정")), "accounts it reads")
        shot("notify-sources")
        // back up to the briefing's weather line (it sits above 새 소식 알림)
        var n = 0
        let allow = element("kelo.briefing.location")
        while !(allow.exists && allow.isHittable) && n < 16 { app.swipeDown(velocity: .slow); n += 1 }
        XCTAssertTrue(allow.exists, "the weather's location line\n\(app.debugDescription)")
        XCTAssertFalse(containing(w("Home area", "사는 곳")).exists, "no place choice any more")
        if allow.frame.minY > app.frame.height * 0.6 { app.swipeUp(velocity: .slow) }
        pause(1)
        shot("briefing-weather")

        // dark: the chat in the middle of a long history (the messages scroll on under the agent) and the My page's top
        XCUIDevice.shared.appearance = .dark
        pause(1.5)
        tap(element(w("Back", "뒤로")), "back")
        pause(1.5)
        shot("my-top-dark")
        tap(element("kelo.tab.chat"), "chat tab")
        pause(2)
        for _ in 0..<3 { app.swipeDown(velocity: .slow) }
        pause(1.5)
        shot("chat-middle-dark")
        // back at the end: the last answer sits just above the composer (ChatOverlayRules.listGap)
        for _ in 0..<6 { app.swipeUp(velocity: .fast) }
        pause(2)
        shot("chat-bottom-dark")
    }
}
