import XCTest

/// The thinking level of a custom OpenAI-compatible endpoint (KELO_EFFORT=1), against apps/desktop/tools/mock_server.py started with
/// KELO_MOCK_CUSTOM_EFFORT=levels (mock_parts/custom_effort.py: the main model is K-EXAONE on a custom endpoint, level 높음).
/// Screens: `<tag>-effort-chip.png` (the chat's model chip with the level), `<tag>-effort-sheet.png` (the model sheet, levels open),
/// `<tag>-effort-model.png` (My → Model with the 생각 수준 field). Other variables as KeloScreensTests. Skipped without KELO_EFFORT.
final class KeloEffortShotsTests: KeloShotCase {


    func testCustomEffort() throws {
        guard env["KELO_EFFORT"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no effort walk asked") }
        launchApp()
        signIn(key, dismissNotifications: true)
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
