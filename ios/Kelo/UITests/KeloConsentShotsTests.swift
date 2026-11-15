import XCTest

/// Screens of the one question before chats first go to an AI service (KELO_CONSENT=1), against desktop/tools/mock_server.py with
/// `--ai-consent` (or KELO_MOCK_AI_CONSENT=1 in the environment of the run that starts it): a message is typed and sent, the mock
/// holds it back with 428 `ai_consent_required` (as src/assistant/ai_consent.py does), and the sheet asks (Agree / Not now). Not now
/// leaves the text in the composer and sends nothing; Agree sends it. Screens: `<tag>-consent.png`, `<tag>-consent-notnow.png`,
/// `<tag>-consent-sent.png`. Other variables as KeloScreensTests. Without KELO_CONSENT the test is skipped.
final class KeloConsentShotsTests: KeloShotCase {

    @discardableResult
    override func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) -> Bool {
        guard e.waitForExistence(timeout: timeout) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return false }
        e.tap()
        return true
    }

    func testConsent() throws {
        guard env["KELO_CONSENT"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no consent walk asked") }
        launchApp()
        signIn(key)
        pause(2)

        let message = w("Plan a 3-day trip to Seoul", "서울 3일 여행 계획 짜 줘")
        let composer = element("kelo.composer")
        tap(composer, "composer")
        composer.typeText(message)
        pause(0.5)
        let tip = app.buttons["Continue"]
        if tip.exists { tip.tap(); pause(0.5) }
        tap(element("kelo.send"), "send")

        // the server holds the first message back: the question opens
        let agree = element("kelo.consent.agree")
        if !agree.waitForExistence(timeout: 8) {
            if tip.exists { tip.tap(); pause(0.5) }
            if element("kelo.send").isHittable { element("kelo.send").tap() }
        }
        XCTAssertTrue(agree.waitForExistence(timeout: 20), "the consent sheet did not open\n\(app.debugDescription)")
        XCTAssertTrue(element("kelo.consent.notNow").exists, "no Not now")
        XCTAssertTrue(element("kelo.consent.privacy").exists, "no privacy link")
        pause(1.5)
        shot("consent")

        // Not now: nothing sent, the text is still in the composer
        tap(element("kelo.consent.notNow"), "not now")
        pause(1.5)
        XCTAssertFalse(agree.exists, "the sheet stayed open")
        XCTAssertTrue(((composer.value as? String) ?? "").contains(message.prefix(6)), "the text left the composer: \(composer.value ?? "")")
        shot("consent-notnow")

        // send again and Agree: the message goes
        tap(element("kelo.send"), "send again")
        XCTAssertTrue(agree.waitForExistence(timeout: 20), "the consent sheet did not open again")
        pause(0.8)
        agree.tap()
        let sent = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", String(message.prefix(6)))).firstMatch
        XCTAssertTrue(sent.waitForExistence(timeout: 20), "the message was not sent after Agree")
        pause(4)
        shot("consent-sent")
    }
}
