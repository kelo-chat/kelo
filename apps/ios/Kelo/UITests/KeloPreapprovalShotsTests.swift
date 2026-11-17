import XCTest

/// The screens of the forgotten-topics / payments-approved-in-advance batch (KELO_PREAPPROVAL=1) against a mock with the
/// order_preapprove and forget_confirm cards waiting (KELO_MOCK_BATCH_IOS): the two cards at the chat's end (`<tag>-chat-cards`),
/// 마이 → Permissions with the payments section (`-payments`), 마이 → Forgotten topics (`-forgotten`). Variables as KeloScreensTests.
/// Without KELO_PREAPPROVAL the test is skipped.
final class KeloPreapprovalShotsTests: KeloShotCase {

    func testPreapproval() throws {
        guard env["KELO_PREAPPROVAL"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no preapproval walk asked") }
        launchApp()
        signIn(key)

        XCTAssertTrue(element(w("Most you pay", "최대 금액")).waitForExistence(timeout: 25), "no preapproval card\n\(app.debugDescription)")
        pause(2)
        shot("chat-cards")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("chat-cards-2")

        tap(element("kelo.tab.my"), "My tab")
        tap(element(w("Permissions", "권한")), "Permissions row")
        let order = element("kelo.paycover.order")
        var n = 0
        while !order.waitForExistence(timeout: 1) && n < 10 { app.swipeUp(velocity: .slow); n += 1 }
        XCTAssertTrue(order.exists, "no payments section\n\(app.debugDescription)")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("payments")
        tap(element(w("Back", "뒤로")), "Back")

        tap(element(w("Forgotten topics", "잊은 주제")), "Forgotten topics row")
        XCTAssertTrue(element("kelo.forget.row").waitForExistence(timeout: 15), "no forgotten topics\n\(app.debugDescription)")
        pause(1.5)
        shot("forgotten")
        tap(app.buttons.matching(NSPredicate(format: "label == %@", w("Allow again", "다시 허용"))).firstMatch, "Allow again")
        pause(1)
        shot("forgotten-confirm")
    }
}
