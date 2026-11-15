import XCTest

/// Screens of auto-pay and schedule grants (KELO_GRANTS=1), against desktop/tools/mock_server.py:
///   - the wallet list (the card rows' "pays on its own" / reason line and, for a card saved to ask each time, "turn on auto-pay for
///     this card"; the auto-pay row's count, the notes, the schedules that buy on their own) and the page of a card that asks each time (reason, "turn on auto-pay for this card", the first-site rule);
///   - with KELO_GRANTS_PART=all (the default) also the keychain page of the Coupang login with its schedule grant and the page of
///     schedule 4 ("Weekly water order") with what it may do without asking.
/// The wallet opens behind the owner's check: enroll Face ID in the simulator and send matches from the Mac while this runs
/// (`xcrun simctl spawn <device> notifyutil -p com.apple.BiometricKit_Sim.pearl.match`). Other variables as KeloScreensTests:
/// KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS (screens are named `<tag>-wallet-auto-*` / `<tag>-grants-*`).
final class KeloGrantsShotsTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var tag: String { env["KELO_TAG"] ?? lang }
    private var app: XCUIApplication!

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }

    override func setUp() { continueAfterFailure = true }

    private func shot(_ name: String) {
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let a = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        a.name = "\(tag)-\(name)"
        a.lifetime = .keepAlways
        add(a)
        if let dir = env["KELO_SHOTS"] {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(tag)-\(name).png"))
        }
    }

    private func element(_ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR identifier == %@", label, label)).firstMatch
    }

    private func containing(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
    }

    @discardableResult
    private func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) -> Bool {
        guard e.waitForExistence(timeout: timeout) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return false }
        reveal(e)
        e.tap()
        return true
    }

    /// Scrolls until `e` sits clear of the floating header and the tab bar (they cover the page's top and bottom, so an element
    /// under them still counts as hittable but a tap would land on the bar).
    private func reveal(_ e: XCUIElement) {
        let h = app.windows.firstMatch.frame.height
        var n = 0
        while n < 10 && e.exists && (!e.isHittable || e.frame.maxY > h - 130) {
            app.swipeUp(velocity: .slow); n += 1
        }
        n = 0
        while n < 6 && e.exists && e.frame.minY < 110 {
            app.swipeDown(velocity: .slow); n += 1
        }
    }

    /// Scrolls up until `e` is on screen (or `max` swipes), small steps so the page does not overshoot.
    @discardableResult
    private func scrollTo(_ e: XCUIElement, max: Int = 10) -> Bool {
        var n = 0
        while (!e.exists || !e.isHittable) && n < max { app.swipeUp(velocity: .slow); n += 1 }
        return e.exists
    }

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    private func back() {
        let b = element(w("Back", "뒤로"))
        if b.waitForExistence(timeout: 3) { b.tap() } else { app.swipeRight() }
        pause(1)
    }

    private func signIn(_ key: String) {
        if element(w("Connect with access key", "접속 키로 연결하기")).waitForExistence(timeout: 10) {
            tap(element(w("Connect with access key", "접속 키로 연결하기")), "connect with key")
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
    }

    /// The work card's row "turn on auto-pay": the notice, then (the mock holds no copy of it made on this phone) the "not on this
    /// phone" alert; the everyday card (the default): its page with the Default card section and its payment history, paged.
    private func walletCards() {
        let rowTurnOn = app.descendants(matching: .any).matching(NSPredicate(format: "identifier == %@", "kelo.wallet.turn-on.c2")).firstMatch
        if tap(rowTurnOn, "work card's turn-on link") {
            pause(1.5)
            shot("wallet-autoon-notice")
            tap(element(w("Got it", "확인했어요")), "agree")
            pause(0.5)
            tap(app.buttons.matching(NSPredicate(format: "label == %@", w("Turn on", "켜기"))).firstMatch, "turn on")
            let alert = app.alerts.firstMatch
            XCTAssertTrue(alert.waitForExistence(timeout: 8), "no not-on-this-phone alert\n\(app.debugDescription)")
            pause(0.8)
            shot("wallet-autoon-not-here")
            let cancel = alert.buttons.matching(NSPredicate(format: "label == %@", w("Cancel", "취소"))).firstMatch
            if cancel.exists { cancel.tap() } else { alert.buttons.element(boundBy: alert.buttons.count - 1).tap() }
            pause(1)
        }
        app.swipeDown(velocity: .fast); app.swipeDown(velocity: .fast)
        pause(0.8)
        tap(element("kelo.wallet.card.c1"), "everyday card")
        pause(2)
        shot("wallet-card-default")
        let history = element(w("Payments with this card", "이 카드로 한 결제"))
        _ = scrollTo(history, max: 8)
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("wallet-card-history")
        let more = element("kelo.wallet.historyMore")
        if scrollTo(more, max: 14) {
            reveal(more)
            pause(0.8)
            shot("wallet-card-history-more")
            more.tap()
            pause(2)
            app.swipeUp(velocity: .slow)
            pause(1)
            shot("wallet-card-history-older")
        }
        back()
    }

    func testGrants() throws {
        guard env["KELO_GRANTS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no grants walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        signIn(key)
        let all = (env["KELO_GRANTS_PART"] ?? "all") == "all"

        // ---- the wallet
        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(element(w("Payment cards", "결제 카드")), "wallet row")
        let add = element("kelo.wallet.add")
        XCTAssertTrue(add.waitForExistence(timeout: 40), "the wallet did not open (Face ID match sent?)\n\(app.debugDescription)")
        pause(1.5)
        // a card saved to ask each time offers turning auto-pay on right in its row
        let rowTurnOn = app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "kelo.wallet.turn-on.")).firstMatch
        XCTAssertTrue(rowTurnOn.waitForExistence(timeout: 5), "no turn-on link in a card row\n\(app.debugDescription)")
        shot("wallet-auto-list")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("wallet-auto-list-2")
        _ = scrollTo(add, max: 6)
        pause(1)
        shot("wallet-auto-list-3")

        // ---- the cards: nickname + Default badge, a card's own history, turning auto-pay on without retyping
        app.swipeDown(velocity: .fast); app.swipeDown(velocity: .fast)
        pause(0.8)
        if env["KELO_GRANTS_PART"] != "grants" {
            walletCards()
            app.swipeDown(velocity: .fast); app.swipeDown(velocity: .fast)
            pause(0.8)
        }

        // a card that asks each time (the work card is saved that way in both mock modes)
        tap(element("kelo.wallet.card.c2"), "work card")
        pause(1.5)
        shot("wallet-auto-card")
        let turnOn = element("kelo.wallet.autoOn")
        _ = scrollTo(turnOn, max: 6)
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("wallet-auto-card-2")
        back()
        guard all else { return }
        back()

        // ---- the keychain page of the Coupang login
        pause(1)
        let vault = element(w("Keychain", "키체인"))
        tap(vault, "keychain row")
        pause(2.5)
        tap(containing(w("Coupang", "쿠팡")), "Coupang login", timeout: 15)
        pause(2)
        let section = element(w("Schedules that use this login without asking", "이 로그인을 묻지 않고 쓰는 예약"))
        if !scrollTo(section, max: 8) { _ = scrollTo(containing(w("Revoke", "허용 취소")), max: 4) }
        reveal(section)
        pause(1)
        shot("grants-login")
        var n = 0
        while !element("kelo.header").exists && n < 4 { back(); n += 1 }

        // ---- schedule 4 on the work page
        tap(element("kelo.header"), "header")
        pause(2.5)
        let weekly = containing(w("Weekly water order", "주간 생수 주문"))
        _ = scrollTo(weekly, max: 10)
        tap(weekly, "weekly water order")
        pause(2.5)
        shot("grants-schedule")
        let allowed = element(w("Allowed without asking", "묻지 않고 하도록 허용됨"))
        _ = scrollTo(allowed, max: 10)
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("grants-schedule-2")
    }
}
