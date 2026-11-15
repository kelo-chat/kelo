import XCTest

/// Screens of what the iPhone app gained to catch up with Android (ios/CATCHUP.md), one scene per run: KELO_CATCHUP=<scene>.
/// Against desktop/tools/mock_server.py with the scene's mock variables (mock_parts/ios_catchup.py). Other variables as
/// KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS (screens are named `<tag>-<scene>-<name>.png`).
/// KELO_HOLD=<seconds>: stay on the scene's last screen that long (a stack sample from the Mac meanwhile). Skipped without KELO_CATCHUP.
final class KeloCatchupShotsTests: KeloShotCase {
    private var scene: String { env["KELO_CATCHUP"] ?? "" }

    override func shot(_ name: String) {
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let a = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        a.name = "\(tag)-\(scene)-\(name)"
        a.lifetime = .keepAlways
        add(a)
        if let dir = env["KELO_SHOTS"] {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(tag)-\(scene)-\(name).png"))
        }
    }

    private func labelled(_ prefix: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", prefix)).firstMatch
    }

    private func hold() {
        if let s = Double(env["KELO_HOLD"] ?? "") { pause(s) }
    }

    func testCatchup() throws {
        guard !scene.isEmpty, env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no catch-up scene asked") }
        launchApp()
        signIn(key)
        pause(2)
        switch scene {
        case "long": try long()
        case "cards": cards()
        case "grant": grant()
        case "pay": pay()
        case "keypad": keypad()
        case "briefing": briefing()
        case "notify": notifyPage()
        case "keychain": keychain()
        case "grants": grants()
        case "caldav": caldav()
        default: XCTFail("unknown scene \(scene)")
        }
    }

    private func send(_ text: String) {
        let composer = element("kelo.composer")
        composer.tap()
        composer.typeText(text)
        tap(element("kelo.send"), "send")
        // after the first message: "shall Kelo say when a reply is ready?" (asked once per phone)
        let alert = app.alerts.firstMatch
        if alert.waitForExistence(timeout: 4) { alert.buttons[w("Later", "나중에")].tap() }
        // the keyboard away (dragged down across it, as a person would), then back to the newest row, so the cards show whole
        let from = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
        from.press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.98)))
        pause(1)
        let jump = element("kelo.jump")
        if jump.waitForExistence(timeout: 2) && jump.isHittable { jump.tap() }
    }

    private var allow: XCUIElement { app.buttons.matching(NSPredicate(format: "label == %@", w("Allow", "허용"))).firstMatch }

    /// Tool steps folded into one chip, one browser card over two sites, a permission for 37 items with its list, then the step limit.
    private func cards() {
        send(w("Unsubscribe me from all the promo mail", "홍보 메일 다 구독 취소해줘"))
        XCTAssertTrue(allow.waitForExistence(timeout: 20), "no approval card\n\(app.debugDescription)")
        pause(1.5)
        shot("card")
        tap(element("kelo.approval.list"), "see the list")
        pause(1)
        shot("list")
        tap(element("kelo.approval.list"), "hide the list")
        allow.tap()
        pause(3)
        shot("done")
        tap(element("kelo.toolchip"), "folded chip")
        pause(1)
        shot("chip-open")
    }

    /// The task setup card of an urgent "when X happens, buy Y" task.
    private func grant() {
        send(w("Watch for a cancelled KTX seat and book it", "KTX 취소표 나오면 알림 주고 예매해줘"))
        XCTAssertTrue(allow.waitForExistence(timeout: 20), "no setup card\n\(app.debugDescription)")
        pause(1.5)
        shot("card")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("card-bottom")
    }

    /// A payment step "approve in another app", then the PIN to release once it is approved.
    private func pay() {
        send(w("Pay now please", "결제 진행해줘"))
        let approved = element("kelo.paystep.approved")
        XCTAssertTrue(approved.waitForExistence(timeout: 20), "no payment step\n\(app.debugDescription)")
        pause(1.5)
        shot("step")
        approved.tap()
        XCTAssertTrue(element("kelo.pin.release").waitForExistence(timeout: 20), "no PIN card\n\(app.debugDescription)")
        pause(1.5)
        shot("pin")
    }

    /// The page's PIN keypad shown in the card.
    private func keypad() {
        send(w("Use the keypad", "키패드로 진행해줘"))
        XCTAssertTrue(element("kelo.paystep.pad").waitForExistence(timeout: 20), "no keypad\n\(app.debugDescription)")
        pause(3)
        shot("pad")
    }

    /// 마이 → 알림: weekday and weekend hours with a learned window and a suggestion, then the morning briefing's settings.
    private func notifyPage() {
        tap(element("kelo.tab.my"), "My tab")
        pause(1)
        tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", w("Notifications", "알림"))).firstMatch, "notifications row")
        pause(2)
        shot("top")
        for i in 1...4 {
            app.swipeUp(velocity: .slow)
            pause(1)
            shot("scroll\(i)")
        }
        // the accounts it reads: its own section at the very bottom, for the briefing and the heads-ups alike
        let sources = element("kelo.briefing.sources")
        var n = 0
        while !(sources.exists && sources.isHittable) && n < 6 { app.swipeUp(velocity: .slow); n += 1 }
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("sources")
    }

    /// 커넥터 → CalDAV 캘린더: the address, user name and app password form (checked once by the server before saving).
    private func caldav() {
        tap(element("kelo.tab.connectors"), "Connectors tab")
        pause(2)
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "CalDAV")).firstMatch
        var n = 0
        while !(row.exists && row.isHittable) && n < 6 { app.swipeUp(velocity: .slow); n += 1 }
        tap(row, "CalDAV row")
        pause(2)
        shot("form")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("form-bottom")
    }

    /// 마이 → 키체인: the payment PINs section (a PIN saved on another phone) and its add form.
    private func keychain() {
        tap(element("kelo.tab.my"), "My tab")
        pause(1)
        tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", w("Keychain", "키체인"))).firstMatch, "keychain row")
        pause(3)
        let add = element("kelo.pin.add")
        var n = 0
        while !(add.exists && add.isHittable) && n < 6 { app.swipeUp(velocity: .slow); n += 1 }
        pause(1)
        shot("pins")
        if add.exists && add.isHittable {
            add.tap()
            pause(1)
            app.swipeUp(velocity: .slow)
            pause(1)
            shot("pin-form")
        }
    }

    /// A watcher's page with what it may do without asking (allowed at setup) and 허용 취소.
    private func grants() {
        tap(element("kelo.header"), "header")
        pause(2)
        let row = labelled(w("Flight to Lisbon", "제주 항공권 가격"))
        var n = 0
        while !(row.exists && row.isHittable) && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        tap(row, "watcher row")
        pause(2)
        let block = element("kelo.taskGrants")
        n = 0
        while !(block.exists && block.isHittable) && n < 6 { app.swipeUp(velocity: .slow); n += 1 }
        pause(1)
        shot("watcher")
    }

    /// The morning briefing card in the history.
    private func briefing() {
        XCTAssertTrue(element("kelo.briefing").waitForExistence(timeout: 15), "no briefing card\n\(app.debugDescription)")
        pause(1.5)
        // the card is taller than the screen: bring its title to the top, shoot, then the rest
        let title = labelled(w("Morning briefing", "아침 브리핑"))
        var n = 0
        while n < 12 && !(title.exists && title.isHittable && title.frame.minY < 260) { app.swipeDown(velocity: .slow); n += 1 }
        shot("top")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("bottom")
    }

    /// A long history: scrolled up, the list must stay responsive (the ↓ button answers at once and takes it back down).
    private func long() throws {
        shot("bottom")
        for _ in 0..<Int(env["KELO_SWIPES"] ?? "4")! { app.swipeDown(velocity: .fast) }
        pause(1.5)
        shot("scrolled-up")
        hold()
        let jump = element("kelo.jump")
        let t0 = Date()
        tap(jump, "jump to the newest")
        let took = Date().timeIntervalSince(t0)
        pause(1.5)
        shot("back-at-bottom")
        XCTAssertLessThan(took, 5, "the ↓ button took \(took) s to answer")
        XCTAssertFalse(jump.exists && jump.isHittable, "still scrolled up after ↓")
    }
}
