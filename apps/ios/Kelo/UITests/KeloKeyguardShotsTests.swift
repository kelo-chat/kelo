import XCTest

/// Keyguard screens (KELO_KEYGUARD_SHOTS=1) against `apps/desktop/tools/mock_server.py` with its aa_keyguard part:
/// KELO_MOCK_KEYGUARD=off: 마이 → 보안 → Keyguard, turned on (the software key on the simulator), the recovery code shown once with
/// "I wrote it down", then the page with this phone in the list; KELO_MOCK_KEYGUARD=locked: the "Kelo is locked" banner over the
/// chat and what a tap on Unlock says. Other variables as KeloScreensTests (KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS).
final class KeloKeyguardShotsTests: KeloShotCase {

    func tap(_ e: XCUIElement, _ what: String) {
        guard e.waitForExistence(timeout: 10) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return }
        var n = 0
        while !e.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        e.tap()
    }


    func testKeyguard() throws {
        guard env["KELO_KEYGUARD_SHOTS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("KELO_KEYGUARD_SHOTS, KELO_SERVER and KELO_KEY are needed")
        }
        launchApp()
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
