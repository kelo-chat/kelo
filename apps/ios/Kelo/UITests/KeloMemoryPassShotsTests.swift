import XCTest

/// Screens of the memory pass on 마이 → 기록 (KELO_MEMORY=1), against apps/desktop/tools/mock_server.py: the look-back card's
/// "기억 정리: 3개 추가, 2개 정리" line with its "바뀐 내용" link, and the sheet that link opens (lines grouped by area, the undo
/// button). Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS (screens are named
/// `<tag>-memory-line.png` / `<tag>-memory-sheet.png`). Without KELO_MEMORY the test is skipped.
final class KeloMemoryPassShotsTests: KeloShotCase {

    private func containing(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
    }

    func testMemoryPass() throws {
        guard env["KELO_MEMORY"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no memory walk asked") }
        launchApp()
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
