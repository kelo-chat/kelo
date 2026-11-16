import XCTest

/// Screens of shipping addresses, the in-chat address form and the one final order card (KELO_SHIPPING=1), against
/// apps/desktop/tools/mock_server.py (its mock_parts/shipping.py answers the address endpoints and scripts an order reply for a message
/// with "머그" / "mug": the address form, then confirm_purchase with items, shipping, card and coupon):
///   - the wallet's shipping section (`shipping-list`), a saved address's page (`shipping-edit`: the KR form with the Korean mock,
///     the US form with the English one), a new address with its required rows marked (`shipping-new-errors`);
///   - the chat: the address form prefilled (`shipping-form`, `shipping-form-2` further down), the postal code check
///     (`shipping-form-errors`), the folded line and the order card (`shipping-order`, `shipping-order-2`).
/// KELO_SHIP_PART: all (default) | wallet | chat. KELO_SHIP_LONG=1 gives the first address a long nickname first (wrap check).
/// The wallet opens behind the owner's check: enroll Face ID in the simulator and send matches from the Mac while this runs
/// (`xcrun simctl spawn <device> notifyutil -p com.apple.BiometricKit_Sim.pearl.match`). Other variables as KeloScreensTests:
/// KELO_SERVER, KELO_KEY, KELO_LANG (ko | en | de), KELO_TAG, KELO_SHOTS. Files: `<name>-<tag>.png`, e.g. shipping-list-ko-light.png.
final class KeloShippingShotsTests: KeloShotCase {

    /// The app's own words for the few labels the walk taps (en, ko, de).

    override func shotName(_ screen: String) -> String { "\(screen)-\(tag)" }

    private func id(_ identifier: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "identifier == %@", identifier)).firstMatch
    }

    private func starting(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", text)).firstMatch
    }

    @discardableResult
    override func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) -> Bool {
        guard e.waitForExistence(timeout: timeout) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return false }
        reveal(e)
        e.tap()
        return true
    }

    /// Scrolls until `e` sits clear of the floating header and the bottom (bars, the composer, the keyboard).
    private func reveal(_ e: XCUIElement, bottom: CGFloat = 130) {
        let h = app.windows.firstMatch.frame.height
        var n = 0
        while n < 12 && e.exists && (!e.isHittable || e.frame.maxY > h - bottom) {
            app.swipeUp(velocity: .slow); n += 1
        }
        n = 0
        while n < 6 && e.exists && e.frame.minY < 110 {
            app.swipeDown(velocity: .slow); n += 1
        }
    }

    private func back() {
        let b = element(w("Back", "뒤로", "Zurück"))
        if b.waitForExistence(timeout: 3) { b.tap() } else { app.swipeRight() }
        pause(1)
    }

    private func hideKeyboard() {
        if app.keyboards.firstMatch.exists {
            let done = app.keyboards.buttons.matching(NSPredicate(format: "label IN %@", ["Return", "return", "Done", "완료", "Fertig", "Next"])).firstMatch
            if done.exists { done.tap() }
            // the chat's list hides the keyboard when dragged down into it (scrollDismissesKeyboard(.interactively))
            var n = 0
            while n < 3 && app.keyboards.firstMatch.exists {
                let w = app.windows.firstMatch
                w.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
                    .press(forDuration: 0.05, thenDragTo: w.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.97)))
                pause(0.6); n += 1
            }
        }
    }

    /// Types into a text field found by its identifier, replacing what it held.
    private func type(_ identifier: String, _ text: String, what: String) {
        let f = id(identifier)
        guard f.waitForExistence(timeout: 8) else { XCTFail("no field \(what)\n\(app.debugDescription)"); return }
        reveal(f, bottom: 380)
        f.tap()
        pause(0.4)
        let field = f.textFields.firstMatch.exists ? f.textFields.firstMatch : (f.textViews.firstMatch.exists ? f.textViews.firstMatch : f)
        let cur = (field.value as? String) ?? ""
        if !cur.isEmpty && cur != field.placeholderValue {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: cur.count))
        }
        field.typeText(text)
        pause(0.3)
    }


    // ---- the wallet ------------------------------------------------------------------------------------------------------------

    private func openWallet() {
        // a tab button sits in the bar: tapped where it is, no scrolling first
        let my = element("kelo.tab.my")
        XCTAssertTrue(my.waitForExistence(timeout: 10), "no My tab")
        my.tap()
        pause(1)
        if !starting(w("Payment cards", "결제 카드", "Zahlungskarten")).waitForExistence(timeout: 4) { my.tap() }
        pause(1.5)
        tap(starting(w("Payment cards", "결제 카드", "Zahlungskarten")), "wallet row")
        XCTAssertTrue(id("kelo.wallet.add").waitForExistence(timeout: 40), "the wallet did not open (Face ID match sent?)\n\(app.debugDescription)")
        pause(1.5)
    }

    /// Scrolls the wallet list down to the shipping section and shows it whole.
    private func toShippingSection() {
        let add = id("kelo.shipping.add")
        var n = 0
        while n < 10 && (!add.exists || !add.isHittable || add.frame.maxY > app.windows.firstMatch.frame.height - 40) {
            app.swipeUp(velocity: .slow); n += 1
        }
        pause(1)
    }

    private func walletPart() {
        openWallet()
        let first = env["KELO_SHIP_EDIT"] ?? (lang == "ko" ? "ship_1a2b3c4d5e6f" : "ship_9f8e7d6c5b4a")
        if env["KELO_SHIP_LONG"] == "1" {
            // a long nickname and a long spaceless word: the rows and tags must wrap, never clip
            toShippingSection()
            tap(id("kelo.shipping.row.\(first)"), "first address")
            pause(2)
            type("kelo.shipping.nickname", w("Grandparents' summer house by the lake", "할머니 할아버지 댁 호숫가 여름 별장", "Ferienwohnungsverwaltungsgesellschaft am See"),
                 what: "nickname")
            hideKeyboard()
            tap(id("kelo.shipping.saveProfile"), "save")
            pause(2.5)
        }
        toShippingSection()
        shot("shipping-list")
        // a saved address: its country's form, filled
        tap(id("kelo.shipping.row.\(first)"), "first address")
        pause(2.5)
        shot("shipping-edit")
        app.swipeUp(velocity: .slow)
        pause(1)
        shot("shipping-edit-2")
        back()
        // a new address: the required rows are marked when saving empty
        toShippingSection()
        tap(id("kelo.shipping.add"), "add address")
        pause(2.5)
        shot("shipping-new")
        let save = id("kelo.shipping.saveProfile")
        tap(save, "save")
        pause(1)
        app.swipeDown(velocity: .fast); app.swipeDown(velocity: .fast)
        pause(0.8)
        shot("shipping-new-errors")
        back()
        back()
    }

    // ---- the chat --------------------------------------------------------------------------------------------------------------

    private func chatPart() {
        tap(element("kelo.tab.chat"), "chat tab", timeout: 5)
        pause(1)
        let composer = element("kelo.composer")
        XCTAssertTrue(composer.waitForExistence(timeout: 10))
        composer.tap()
        composer.typeText(w("Order 2 blue mugs", "블루 머그 2개 주문해줘", "Bestell 2 blaue Mugs (mug)"))
        tap(element("kelo.send"), "send")
        // after the first message the app asks once whether to say when replies are ready: not now
        let alert = app.alerts.firstMatch
        if alert.waitForExistence(timeout: 5) {
            let later = alert.buttons.matching(NSPredicate(format: "label IN %@", ["Later", "나중에", "Später"])).firstMatch
            if later.exists { later.tap() } else { alert.buttons.element(boundBy: alert.buttons.count - 1).tap() }
            pause(0.8)
        }
        let send = id("kelo.shipping.send")
        XCTAssertTrue(send.waitForExistence(timeout: 30), "no address form\n\(app.debugDescription)")
        hideKeyboard()
        pause(2.5)
        // the form's top: title, site, what, country and the first rows
        let title = element(w("Where should it be delivered?", "어디로 보낼까요?", "Wohin soll geliefert werden?"))
        if title.exists {
            // drag the title itself up under the header (a plain swipe flicks too far, or not at all)
            var n = 0
            while n < 4 && (title.frame.minY > 170 || title.frame.minY < 100) {
                let w = app.windows.firstMatch
                let from = w.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: w.frame.width / 2, dy: title.frame.midY))
                let to = w.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: w.frame.width / 2, dy: 140))
                from.press(forDuration: 0.1, thenDragTo: to, withVelocity: .slow, thenHoldForDuration: 0.4)
                pause(0.8); n += 1
            }
        }
        pause(1)
        shot("shipping-form")
        let us = (env["KELO_MOCK_SHIP_FORM"] ?? (lang == "ko" ? "kr" : "us")) == "us"
        if us {
            type("kelo.address.field.address_line1", "1600 Amphitheatre Pkwy", what: "address line 1")
            type("kelo.address.field.locality", "Mountain View", what: "city")
            hideKeyboard()
            tap(id("kelo.address.field.admin_area"), "state")
            pause(1.2)
            let search = id("kelo.pick.search")
            if search.waitForExistence(timeout: 3) { search.tap(); search.typeText("Calif"); pause(0.6) }
            tap(id("kelo.pick.CA"), "California")
            pause(1)
            type("kelo.address.field.postal_code", "9404", what: "zip")
        } else {
            type("kelo.address.field.postal_code", "0623", what: "postal code")
        }
        hideKeyboard()
        // the postal code is checked here before anything is sent
        tap(send, "use this address")
        pause(1.2)
        let field = id("kelo.address.field.postal_code")
        if field.exists { reveal(field, bottom: 300) }
        pause(0.8)
        shot("shipping-form-errors")
        type("kelo.address.field.postal_code", us ? "94043" : "06236", what: "postal code")
        hideKeyboard()
        if id("kelo.shipping.pay.card").exists { tap(id("kelo.shipping.pay.site"), "pay on the site") }
        reveal(send, bottom: 200)
        pause(1)
        shot("shipping-form-2")
        tap(send, "use this address")
        // folded into one line, then the one final order card
        XCTAssertTrue(id("kelo.shipping.done").waitForExistence(timeout: 15), "the form did not fold\n\(app.debugDescription)")
        let total = id("kelo.order.total")
        XCTAssertTrue(total.waitForExistence(timeout: 30), "no order card\n\(app.debugDescription)")
        pause(2)
        let done = id("kelo.shipping.done")
        var n = 0
        while n < 10 && done.exists && done.frame.minY < 110 { app.swipeDown(velocity: .slow); n += 1 }
        while n < 20 && done.exists && done.frame.minY > 160 { app.swipeUp(velocity: .slow); n += 1 }
        pause(1)
        shot("shipping-order")
        reveal(total, bottom: 260)
        let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("Allow", "허용", "Erlauben"))).firstMatch
        if allow.exists { reveal(allow, bottom: 140) }
        pause(1)
        shot("shipping-order-2")
    }

    func testShipping() throws {
        guard env["KELO_SHIPPING"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no shipping walk asked") }
        app = XCUIApplication()
        let locale = ["ko": "ko_KR", "de": "de_DE"][lang] ?? "en_US"
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", locale]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        signIn(key)
        let part = env["KELO_SHIP_PART"] ?? "all"
        if part == "all" || part == "wallet" { walletPart() }
        if part == "all" || part == "chat" { chatPart() }
    }
}
