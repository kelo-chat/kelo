import XCTest

/// Learned message hours (KELO_HOURS=suggest|accept|notify), against desktop/tools/mock_server.py (a night owl: 10:00 – 01:00 next
/// day, set 08:00 – 22:00): the review row on 마이 → 기록 ("알림 받는 시간 제안" with "지금대로 두기" / "이 시간으로 바꾸기"); with
/// `accept` the row after "이 시간으로 바꾸기" and then 마이 → 알림 saying the hours were learned, with "쓰는 시간에 맞춰 알림 시간 바꾸기"; `notify` only
/// that page. Other variables as KeloScreensTests (KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS); screens are named
/// `<tag>-hours-suggest.png`, `<tag>-hours-accepted.png`, `<tag>-hours-learned.png`. Without KELO_HOURS the test is skipped.
final class KeloHoursShotsTests: KeloShotCase {

    private func containing(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
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
        launchApp()
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
