import XCTest

/// Walks the app against a pretend Kelo server and saves a screenshot of each page. Run through xcodebuild with
///   TEST_RUNNER_KELO_SERVER=http://127.0.0.1:<port>  TEST_RUNNER_KELO_KEY=<the mock's token>
///   TEST_RUNNER_KELO_LANG=ko|en  TEST_RUNNER_KELO_TAG=ko-light  TEST_RUNNER_KELO_SHOTS=<folder on the Mac>
///   TEST_RUNNER_KELO_BIGTEXT=1 (optional: Dynamic Type at an accessibility size)
/// The mock is desktop/tools/mock_server.py (started on another port). Without KELO_SERVER the test is skipped, so a plain
/// `xcodebuild test` stays green. The app must start signed out (uninstall it and reset the simulator's keychain first).
final class KeloScreensTests: KeloShotCase {

    /// The few words the walk taps, in both languages it runs in.
    func w(_ key: String) -> String {
        let words: [String: (String, String)] = [
            "withKey": ("Connect with access key", "접속 키로 연결하기"),
            "key": ("Access key", "접속 키"),
            "connect": ("Connect", "연결"),
            "allow": ("Allow", "허용"),
            "model": ("Model", "모델"),
            "effort": ("Thinking level", "생각 수준"),
            "close": ("Close", "닫기"),
            "back": ("Back", "뒤로"),
            "clear": ("Delete chat history", "대화 기록 삭제"),
            "delete": ("Delete", "삭제"),
            "message": ("When is my dentist appointment?", "치과 예약 언제야?"),
            "stopAllowing": ("Stop allowing", "그만 허용"),
            "operator": ("Operator settings", "운영자 설정"),
            "beta": ("Beta requests", "베타 신청"),
            "permissions": ("Permissions", "권한"),
        ]
        let pair = words[key]!
        return lang == "ko" ? pair.1 : pair.0
    }

    override func element(_ label: String) -> XCUIElement {
        let p = NSPredicate(format: "label == %@ OR identifier == %@", label, label)
        return app.descendants(matching: .any).matching(p).firstMatch
    }

    @discardableResult
    func tap(_ label: String, timeout: TimeInterval = 8) -> Bool {
        let e = element(label)
        guard e.waitForExistence(timeout: timeout) else {
            XCTFail("not found: \(label)\n\(app.debugDescription)")
            return false
        }
        if !e.isHittable { app.swipeUp() }
        e.tap()
        return true
    }

    private func scrollTo(_ label: String, max: Int = 8) -> XCUIElement {
        let e = element(label)
        var n = 0
        while (!e.exists || !e.isHittable) && n < max { app.swipeUp(); n += 1 }
        return e
    }

    func testWalk() throws {
        guard env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no mock server given") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()

        // connect: the first screen, already in the phone's language (there is no language step)
        XCTAssertFalse(element("→").waitForExistence(timeout: 2), "a language screen came first\n\(app.debugDescription)")
        XCTAssertTrue(element(w("withKey")).waitForExistence(timeout: 10), "the connect screen is not in \(lang)\n\(app.debugDescription)")
        pause(1)
        shot("02-connect")
        connectWithKey(key: key) { shot("03-connect-key") }

        // chat
        let composer = element("kelo.composer")
        XCTAssertTrue(composer.waitForExistence(timeout: 15), "chat did not open\n\(app.debugDescription)")
        pause(2)
        shot("04-chat")
        composer.tap()
        composer.typeText(w("message"))
        tap("kelo.send")
        pause(4.5)
        shot("05-streaming")
        let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("allow"))).firstMatch
        if allow.waitForExistence(timeout: 15) {
            pause(0.5)
            shot("06-approval")
            allow.tap()
            pause(3)
            shot("07-reply-done")
        } else {
            shot("06-approval-missing")
            XCTFail("no approval card\n\(app.debugDescription)")
        }

        // model sheet: one folded "Model" row, the list inside, the thinking level for a provider that has one
        tap("kelo.modelChip")
        pause(1)
        shot("08-model-sheet")
        tap("kelo.modelListRow")
        pause(1)
        shot("09-model-list")
        let claude = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Claude'")).firstMatch
        if claude.waitForExistence(timeout: 3) { claude.tap() } else { XCTFail("no Claude section\n\(app.debugDescription)") }
        pause(1.5)
        shot("10-model-other-provider")
        let sonnet = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Sonnet 5.5'")).firstMatch
        if sonnet.waitForExistence(timeout: 5) { sonnet.tap() } else { XCTFail("no Sonnet row\n\(app.debugDescription)") }
        pause(1.5)
        shot("11-model-after-pick")
        if element(w("effort")).waitForExistence(timeout: 3) {
            tap(w("effort"))
            pause(1)
            shot("12-model-levels")
        }
        if element(w("close")).exists { tap(w("close")) } else { app.swipeDown() }
        pause(1)

        // the work page from the header
        tap("kelo.header")
        pause(2)
        shot("13-work")
        app.swipeUp()
        pause(0.5)
        shot("14-work-more")
        if element(w("back")).exists { tap(w("back")) } else if element(w("close")).exists { tap(w("close")) }
        else { app.swipeRight() }
        pause(1)

        // tabs
        tap("kelo.tab.goals")
        pause(2)
        shot("15-goals")
        tap("kelo.tab.connectors")
        pause(2)
        shot("16-connectors")
        tap("kelo.tab.my")
        pause(2)
        shot("17-my")
        let clear = scrollTo(w("clear"))
        pause(0.5)
        shot("18-my-lower")
        if clear.exists {
            clear.tap()
            pause(1)
            shot("19-clear-dialog")
            let del = app.buttons.matching(NSPredicate(format: "label == %@", w("delete"))).firstMatch
            if del.waitForExistence(timeout: 3) { del.tap() }
            pause(1.5)
            tap("kelo.tab.chat")
            pause(2)
            shot("20-chat-cleared")
        } else {
            XCTFail("no clear row\n\(app.debugDescription)")
        }
    }

    /// The parity walk (KELO_PARITY=1, with the parity mock part): questions from a background job at the bottom of the chat, one
    /// answered and folded, the hand-over card, the model sheet's height, the Work page's task permission and 그만 허용, the
    /// operator page's beta requests, 이 기기에서 with the dialer, and the tab labels.
    /// The chat's time lines (TimeLabels) against a mock started with --spread: a live message today, then the history from the top
    /// (an earlier year, earlier this year, yesterday). Screens: `<tag>-timelines-today.png`, `<tag>-timelines-history.png`.
    func testTimeLines() throws {
        guard env["KELO_SERVER"] != nil, env["KELO_TIMELINES"] == "1", let key = env["KELO_KEY"] else { throw XCTSkip("no mock server given") }
        launchApp()
        XCTAssertTrue(element(w("withKey")).waitForExistence(timeout: 10))
        connectWithKey(key: key)
        let composer = element("kelo.composer")
        XCTAssertTrue(composer.waitForExistence(timeout: 15), "chat did not open\n\(app.debugDescription)")
        pause(2)
        composer.tap()
        pause(1)
        let intro = app.buttons["Continue"]   // a fresh simulator's swipe-typing introduction covers the keyboard
        if intro.exists { intro.tap(); pause(1) }
        for _ in 0..<3 where !((composer.value(forKey: "hasKeyboardFocus") as? Bool) ?? false) {
            composer.tap()
            pause(1)
        }
        composer.typeText(w("message"))
        tap("kelo.send")
        let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("allow"))).firstMatch
        if allow.waitForExistence(timeout: 15) { allow.tap() }
        pause(8)
        // drag the messages down (from the upper middle of the screen, away from the keyboard and the cards): the keyboard goes
        // away and older messages come into view
        func drag(_ to: Double) {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25))
                .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: to)))
            pause(1)
        }
        drag(0.75)
        if app.keyboards.firstMatch.exists { drag(0.75) }
        pause(1)
        shot("timelines-today")
        for _ in 0..<8 { drag(0.9) }
        pause(1.5)
        shot("timelines-history")
    }

    static let longKo = "다음 주 치과 예약 언제였는지 확인해 줘. 끝나고 바로 한강에서 10km 달리기 하려고 하니까, 시간이 정해져 있으면 캘린더에 넣고 앞뒤로 30분씩 비워 둬"
    static let longEn = "Can you check when my dentist appointment is next week? I want to go for a 10 km run by the river right after, so put it in my calendar and keep 30 minutes free on either side"

    /// The emoji on the user's own messages (Reactions) against a mock started with --reactions (KELO_REACTIONS=1; with
    /// --final-reaction 🏃‍♂️ and KELO_FINAL_REACTION=🏃‍♂️ for a ZWJ sequence): the history's 🗓️ and 📝 chips, then a long message over
    /// several lines and a very short one ("ㅇㅇ" / "ok"), each with 👀 while its reply runs and the final emoji once the approval
    /// is allowed and the reply ends; each chip is checked against its bubble (near the end corner, or centred under a short one).
    /// Screens: `<tag>-reactions-history.png`, `<tag>-reactions-long-read.png`, `-long-done`, `-short-read`, `-short-done`.
    func testReactions() throws {
        guard env["KELO_REACTIONS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no reactions walk asked") }
        let ko = lang == "ko"
        let readWord = ko ? "읽음" : "Read"
        func reacted(_ e: String) -> String { (ko ? "반응: " : "Reaction: ") + e }
        app = XCUIApplication()
        // the notification question after the first message is marked as asked already, so it does not cover the 👀 moment
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", ko ? "ko_KR" : "en_US", "-notify.asked_in_chat", "YES"]
        app.launch()
        XCTAssertTrue(element(w("withKey")).waitForExistence(timeout: 10))
        connectWithKey(key: key)
        let composer = element("kelo.composer")
        XCTAssertTrue(composer.waitForExistence(timeout: 15), "chat did not open\n\(app.debugDescription)")
        pause(2)
        // the system's "save password?" prompt after signing in with a key
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        func dismissPrompts() {
            for target in [app!, springboard] {
                for label in ["Not Now", "지금 안 함", "Later", "나중에"] where target.buttons[label].waitForExistence(timeout: 1) {
                    target.buttons[label].tap(); pause(1)
                }
            }
        }
        dismissPrompts()
        func drag(_ from: Double, _ to: Double) {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: from))
                .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: to)))
            pause(1)
        }
        // on screen: below the floating header (the avatar and name) and above the composer
        func inView(_ e: XCUIElement) -> Bool {
            guard e.exists else { return false }
            let f = e.frame, top = app.frame.height * 0.2
            return f.minY > top && f.maxY < composer.frame.minY - 8
        }
        let calendar = element(reacted("\u{1F5D3}\u{FE0F}")), outline = element(reacted("\u{1F4DD}"))
        XCTAssertTrue(outline.waitForExistence(timeout: 10), "no 📝 chip\n\(app.debugDescription)")
        // the history first: both chips on screen at the top of the chat. Fast flings straight to the top: slow drags that
        // stop with an older row half in view at the top have hung the app's main thread (with or without reactions).
        // KELO_HISTORY=0 leaves this out (the flings still hang the app now and then on a busy machine).
        if env["KELO_HISTORY"] != "0" {
            for _ in 0..<3 where !(inView(calendar) && inView(outline)) { app.swipeDown(velocity: .fast); pause(2) }
            XCTAssertTrue(inView(calendar) && inView(outline), "history chips not both in view")
            pause(1)
            shot("reactions-history")
        }
        // then new messages: a long one over several lines, then a very short answer. Each gets 👀 while its reply runs and the
        // reply's own emoji once the approval is allowed and the reply ends (KELO_FINAL_REACTION, e.g. 🏃‍♂️; 🦷 by default)
        let finalEmoji = env["KELO_FINAL_REACTION"] ?? "\u{1F9B7}"
        // the chip of the message `text` against its bubble: near the bubble's end corner (or centred under a short one), over
        // its bottom edge and hanging below it, never past its start edge. The message's element is the whole bubble; the chip's
        // is what shows of it (its 2 pt shadow halo left out): 12 pt in from the bubble's end edge, 9 pt below its bottom edge.
        func checkPlacement(_ chip: XCUIElement, under text: String) {
            let words = app.staticTexts.matching(NSPredicate(format: "label == %@", text)).allElementsBoundByIndex.last
            guard let words, chip.exists else { return XCTFail("no bubble or chip for \(text)") }
            let t = words.frame, c = chip.frame
            if t.width >= c.width + 4 + 20 {
                XCTAssertEqual(c.maxX, t.maxX - 12, accuracy: 2.5, "chip not 12 pt in from the bubble's end edge: \(c) \(t)")
            } else {
                XCTAssertEqual(c.midX, t.midX, accuracy: 2.5, "chip not centred under the short bubble: \(c) \(t)")
            }
            XCTAssertGreaterThanOrEqual(c.minX, t.minX - 0.5, "chip past the bubble's start edge")
            XCTAssertLessThan(c.minY, t.maxY, "chip not over the bubble's bottom edge")
            XCTAssertEqual(c.maxY, t.maxY + 9, accuracy: 2.5, "chip does not hang 9 pt below the bubble: \(c) \(t)")
        }
        func exchange(_ text: String, _ name: String) {
            composer.tap()
            pause(1)
            let intro = app.buttons["Continue"]   // a fresh simulator's swipe-typing introduction covers the keyboard
            if intro.exists { intro.tap(); pause(1) }
            for _ in 0..<3 where !((composer.value(forKey: "hasKeyboardFocus") as? Bool) ?? false) {
                composer.tap()
                pause(1)
            }
            composer.typeText(text)
            tap("kelo.send")
            // 👀 at once, on the message just sent: the shot first (the reply soon pushes the message up), then the check
            pause(0.6)
            shot("reactions-\(name)-read")
            let read = element(readWord)
            XCTAssertTrue(read.waitForExistence(timeout: 10), "no 👀 chip\n\(app.debugDescription)")
            XCTAssertEqual(read.label, readWord)
            if inView(read) { checkPlacement(read, under: text) }
            // the reply's approval card (a background job's card waits at the bottom too: the new card is the one under its own lines)
            let anchors = app.staticTexts.matching(NSPredicate(format: "label == %@", ko ? "치과 예약" : "Dentist"))
            // this reply's card: the newest one under the message just sent (an earlier reply's card is above it)
            func card() -> XCUIElement? {
                let sent = app.staticTexts.matching(NSPredicate(format: "label == %@", text)).allElementsBoundByIndex.last?.frame.minY ?? 0
                return anchors.allElementsBoundByIndex.filter { $0.frame.minY > sent }.max { $0.frame.minY < $1.frame.minY }
            }
            let deadline = Date().addingTimeInterval(25)
            while card() == nil && Date() < deadline { pause(0.5) }
            guard let anchor = card() else { return XCTFail("no approval card for the reply\n\(app.debugDescription)") }
            pause(1)
            dismissPrompts()
            // KELO_READ_WAIT=1: the keyboard away and the message pulled into view below the floating name tag, 👀 while the card
            // waits (dragging the list while the reply runs has often hung the app, so it is asked for on its own)
            if env["KELO_READ_WAIT"] == "1" {
                drag(0.25, 0.75)
                if !inView(read) { drag(0.3, 0.75) }
            }
            if env["KELO_READ_WAIT"] == "1", inView(read) {
                XCTAssertEqual(read.label, readWord)
                checkPlacement(read, under: text)
                pause(1)
                shot("reactions-\(name)-read-waiting")
            }
            let y = anchor.frame.minY
            let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("allow"))).allElementsBoundByIndex
                .filter { $0.frame.minY > y }.min { $0.frame.minY < $1.frame.minY }
            guard let allow else { return XCTFail("no Allow on the reply's card\n\(app.debugDescription)") }
            if !allow.isHittable { app.keyboards.firstMatch.exists ? drag(0.25, 0.75) : drag(0.7, 0.3) }
            allow.tap()
            // the reply ends and 👀 gives way to the final emoji: the newest chip with it is this message's
            pause(6)   // the rest of the reply streams in
            if app.keyboards.firstMatch.exists { drag(0.25, 0.75) }
            let finals = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", reacted(finalEmoji)))
            func done() -> XCUIElement { finals.allElementsBoundByIndex.max { $0.frame.minY < $1.frame.minY } ?? finals.firstMatch }
            // long drags only: short ones that leave the list's end at the composer's edge have hung the app's main thread
            for _ in 0..<8 where !inView(done()) {
                if !done().exists || done().frame.minY < app.frame.height * 0.2 { drag(0.3, 0.7) } else { drag(0.7, 0.3) }
            }
            XCTAssertTrue(done().exists, "no \(finalEmoji) chip\n\(app.debugDescription)")
            XCTAssertTrue(inView(done()), "\(finalEmoji) chip not in view")
            XCTAssertFalse(element(readWord).exists, "👀 still shown after the reply ended")
            checkPlacement(done(), under: text)
            pause(1.5)
            shot("reactions-\(name)-done")
        }
        exchange(ko ? Self.longKo : Self.longEn, "long")
        exchange(ko ? "ㅇㅇ" : "ok", "short")
        let send = element("kelo.send")
        // the floating composer (safe-area inset with a scrim): scrolled up a little, the "↓" clear of Send
        let pill = app.descendants(matching: .any).matching(NSPredicate(format: "label ENDSWITH %@", ko ? "최신 메시지로 이동" : "Jump to latest message")).firstMatch
        if pill.waitForExistence(timeout: 3) {
            XCTAssertFalse(pill.frame.intersects(send.frame), "the ↓ covers Send")
            XCTAssertLessThanOrEqual(pill.frame.maxY, composer.frame.minY + 2, "the ↓ sits on the composer")
            shot("composer-scrolled")
            pill.tap()
            pause(2.5)
        }
        // at the end: the last message and its answers above the composer
        shot("composer-bottom")
        // the keyboard open: the composer right on it
        composer.tap()
        pause(2)
        let keyboard = app.keyboards.firstMatch
        if keyboard.waitForExistence(timeout: 5) {
            // Send sits in the composer box's bottom row. The keyboard element's frame starts under the suggestion bar (about 44 pt),
            // so Send's bottom is about 60 pt above it when the box rests on the keyboard
            let gap = keyboard.frame.minY - send.frame.maxY
            XCTAssertTrue(gap >= -1 && gap < 70, "composer not on the keyboard (gap \(gap))")
        }
        shot("composer-keyboard")
    }

    func testParity() throws {
        guard env["KELO_PARITY"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no parity walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        if env["KELO_BIGTEXT"] == "1" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"]
        }
        app.launch()
        if element(w("withKey")).waitForExistence(timeout: 10) {
            connectWithKey(key: key)
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 20), "chat did not open\n\(app.debugDescription)")
        let ask = element("kelo.ask")
        XCTAssertTrue(ask.waitForExistence(timeout: 10), "no waiting question at the bottom of the chat\n\(app.debugDescription)")
        pause(1.5)
        shot("parity-01-chat-asks")
        let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("allow"))).firstMatch
        if allow.waitForExistence(timeout: 5) { allow.tap() } else { XCTFail("no Allow on the job's question\n\(app.debugDescription)") }
        pause(2)
        shot("parity-03-chat-ask-folded")

        tap("kelo.modelChip")
        pause(1.2)
        shot("parity-04-model-sheet")
        if element(w("close")).exists { tap(w("close")) } else { app.swipeDown() }
        pause(1)

        tap("kelo.header")
        pause(2.5)
        shot("parity-05-work-grant")
        if element(w("stopAllowing")).waitForExistence(timeout: 5) {
            tap(w("stopAllowing"))
            pause(1.5)
            shot("parity-06-work-grant-stopped")
        } else {
            XCTFail("no task permission card\n\(app.debugDescription)")
        }
        if element(w("back")).exists { tap(w("back")) } else if element(w("close")).exists { tap(w("close")) } else { app.swipeRight() }
        pause(1)

        tap("kelo.tab.connectors")
        pause(2)
        let device = scrollTo(lang == "ko" ? "이 기기에서" : "On this phone", max: 10)
        pause(0.5)
        if device.exists { shot("parity-07-connectors-device") }
        tap("kelo.tab.my")
        pause(2)
        shot("parity-08-my-tabs")
        let op = scrollTo(w("operator"), max: 10)
        if op.exists {
            op.tap()
            pause(2)
            _ = scrollTo(w("beta"), max: 10)
            app.swipeUp()
            pause(1)
            shot("parity-09-operator-beta")
            if element(w("back")).exists { tap(w("back")) }
        } else {
            XCTFail("no operator row\n\(app.debugDescription)")
        }
    }

    /// The header while the server cannot be reached: signs in (KELO_SERVER, KELO_KEY) when the app starts signed out, then
    /// waits until the header says the server is out of reach (stop the pretend server once the chat is open) and saves
    /// KELO_ONLY_SHOT. Without KELO_ONLY_SHOT it is skipped.
    func testShotAsIs() throws {
        guard let name = env["KELO_ONLY_SHOT"] else { throw XCTSkip("no shot asked") }
        launchApp()
        let next = element("→")
        if next.waitForExistence(timeout: 5) { next.tap() }
        if let key = env["KELO_KEY"], element(w("withKey")).waitForExistence(timeout: 5) {
            connectWithKey(key: key)
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 20))
        let offline = lang == "ko" ? "서버에 연결할 수 없어요" : "Can't reach the server"
        let header = app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'kelo.header' AND label CONTAINS %@", offline)).firstMatch
        XCTAssertTrue(header.waitForExistence(timeout: Double(env["KELO_WAIT"] ?? "120") ?? 120), "header never went offline")
        pause(1.5)
        shot(name)
    }

    /// An answer to write (KELO_TYPED=1, Korean, against the mock with the diet form): picks "직접 입력할게요" and "있어요 (직접 입력)",
    /// types into the boxes they open, saves the chat, then the goals tab (a goal Kelo is working on next to one waiting).
    func testTypedAnswer() throws {
        guard env["KELO_TYPED"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no typed-answer walk asked") }
        launchApp()
        let next = element("→")
        if next.waitForExistence(timeout: 5) { next.tap() }
        if element(w("withKey")).waitForExistence(timeout: 5) {
            connectWithKey(key: key)
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 20))
        pause(2)
        func reveal(_ e: XCUIElement) {
            var n = 0
            while (!e.exists || !e.isHittable) && n < 8 { app.swipeDown(); n += 1 }
        }
        let weight = element("직접 입력할게요")
        reveal(weight)
        weight.tap()
        let box1 = app.textFields.matching(NSPredicate(format: "placeholderValue == %@", "현재 체중과 목표 체중")).firstMatch
        XCTAssertTrue(box1.waitForExistence(timeout: 5), "no text box for the weight")
        box1.tap()
        box1.typeText("현재 80 목표 70")
        let diet = element("있어요 (직접 입력)")
        if !diet.isHittable { app.swipeUp() }
        diet.tap()
        let box2 = app.textFields.matching(NSPredicate(format: "placeholderValue == %@", "식단 제한")).firstMatch
        XCTAssertTrue(box2.waitForExistence(timeout: 5), "no text box for the diet (an older server's words)")
        box2.tap()
        box2.typeText("유당불내증")
        pause(1.5)
        shot("typed-answer")
        tap(lang == "ko" ? "다짐" : "Goals")
        pause(3)
        shot("goals-working")
    }

    /// Typed answer fields (KELO_FIELDS=1, against `mock_server.py --form`): the form under the last message (a date, a number, a
    /// choice with the app's own "Other", days with a time), then "Other" opened and typed into, then the time picker.
    func testAnswerFields() throws {
        guard env["KELO_FIELDS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no answer-fields walk asked") }
        launchApp()
        let next = element("→")
        if next.waitForExistence(timeout: 5) { next.tap() }
        if element(w("withKey")).waitForExistence(timeout: 5) {
            connectWithKey(key: key)
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 20))
        pause(2)
        // the system's "save password?" prompt after signing in with a key
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for target in [app!, springboard] {
            for label in ["Not Now", "지금 안 함"] where target.buttons[label].waitForExistence(timeout: 1) { target.buttons[label].tap(); pause(1) }
        }
        let other = element(lang == "ko" ? "직접 정하기" : "Other")
        var n = 0
        while (!other.exists || !other.isHittable) && n < 8 { app.swipeDown(); n += 1 }
        // not under the floating name tag at the top
        if other.exists && other.frame.minY < app.frame.height * 0.25 {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35))
                .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6)))
        }
        pause(1)
        shot("form-fields")
        other.tap()
        // the box that opens under the answers (a multi-line field: a text view, found by its hint)
        func box(_ hint: String) -> XCUIElement {
            let p = NSPredicate(format: "placeholderValue == %@", hint)
            let view = app.textViews.matching(p).firstMatch
            return view.waitForExistence(timeout: 3) ? view : app.textFields.matching(p).firstMatch
        }
        let otherBox = box(lang == "ko" ? "어떤 파일로 받을까요?" : "Which file type?")
        XCTAssertTrue(otherBox.waitForExistence(timeout: 5), "no Other box")
        otherBox.tap()
        otherBox.typeText("Markdown")
        let number = box(lang == "ko" ? "개" : "tables")
        if number.waitForExistence(timeout: 3) { number.tap(); number.typeText("3") }
        app.swipeUp()
        pause(1.5)
        shot("form-fields-other")
        let time = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "9:00")).firstMatch
        if time.waitForExistence(timeout: 3) {
            time.tap()
            pause(1.5)
            shot("form-fields-time")
        }
    }

    /// What to type into the server field: the field may hold the last address already.
    /// The model sheet folding (KELO_FOLD=1, against the mock): the model list and the Claude section, each shot right after the
    /// tap (while it grows or shrinks) and once it has settled. Signs in first when the app starts signed out.
    func testModelFold() throws {
        guard env["KELO_FOLD"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no fold walk asked") }
        launchApp()
        if element(w("withKey")).waitForExistence(timeout: 8) {
            connectWithKey(key: key)
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 20))
        pause(3)
        // the system's "save password?" prompt after signing in with a key
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for target in [app!, springboard] {
            for label in ["Not Now", "지금 안 함"] where target.buttons[label].waitForExistence(timeout: 1) { target.buttons[label].tap(); pause(1) }
        }
        shot("fold-00-chat")
        let chip = element("kelo.modelChip")
        if !chip.isHittable { app.swipeDown() }   // a keyboard or a first-run note over the composer
        chip.tap()
        pause(1)
        let row = element("kelo.modelListRow")
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()
        shot("fold-list-opening")
        pause(1)
        shot("fold-list-open")
        let claude = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Claude'")).firstMatch
        guard claude.waitForExistence(timeout: 3) else { return XCTFail("no Claude section\n\(app.debugDescription)") }
        claude.tap()
        shot("fold-provider-opening")
        pause(1.5)
        shot("fold-provider-open")
        claude.tap()
        shot("fold-provider-closing")
        pause(1)
        shot("fold-provider-closed")
    }

    /// The required-update screen (KELO_UPDATE=1, against a mock started with --min-app-version '{"ios": 999}'): signs in when the
    /// app starts signed out, then waits for the screen that covers the app and saves it.
    func testUpdateRequired() throws {
        guard env["KELO_UPDATE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no update-required shot asked") }
        launchApp()
        let next = element("→")
        if next.waitForExistence(timeout: 5) { next.tap() }
        if element(w("withKey")).waitForExistence(timeout: 8) {
            connectWithKey(key: key)
        }
        let title = lang == "ko" ? "업데이트가 필요해요" : "Update Kelo to continue"
        XCTAssertTrue(app.staticTexts[title].waitForExistence(timeout: 30), "no required-update screen\n\(app.debugDescription)")
        pause(1.5)
        shot("update-required")
    }

    /// 마이 → 사용량's "where tokens went" card and 마이 → 모델's background work model row (KELO_USAGE=1). Signs in in KELO_LANG
    /// (ko or en), or in KELO_SHOW_LANG (de) for the narrow-width check on a small phone.
    /// Screens: `<tag>-usage-jobs.png`, `<tag>-usage-jobs-month.png`, `<tag>-model-background.png`.
    func testUsageAndModel() throws {
        guard env["KELO_USAGE"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no usage walk asked") }
        let show = env["KELO_SHOW_LANG"] ?? lang   // e.g. de for the narrow-width check
        let locales = ["ko": "ko_KR", "en": "en_US", "de": "de_DE"]
        let signIn: [String: (String, String)] = ["en": ("Connect with access key", "Connect"), "ko": ("접속 키로 연결하기", "연결"),
                                                  "de": ("Mit Zugangsschlüssel verbinden", "Verbinden")]
        let (withKey, connect) = signIn[show] ?? signIn["en"]!
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(show))", "-AppleLocale", locales[show] ?? "en_US"]
        app.launch()
        if element(withKey).waitForExistence(timeout: 10) {
            connectWithKey(key: key, withKey: withKey, connect: connect)
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 25), "chat did not open\n\(app.debugDescription)")
        let words: [String: [String: String]] = [
            "usage": ["en": "Usage", "ko": "사용량", "de": "Nutzung"],
            "model": ["en": "Model", "ko": "모델", "de": "Modell"],
            "jobs": ["en": "Where tokens went", "ko": "토큰을 쓴 곳", "de": "Wofür Tokens verbraucht wurden"],
            "month": ["en": "This month", "ko": "이번 달", "de": "Diesen Monat"],
            "same": ["en": "Same as main model", "ko": "기본 모델과 같음", "de": "Wie das Hauptmodell"],
            "back": ["en": "Back", "ko": "뒤로", "de": "Zurück"],
        ]
        func t(_ k: String) -> String { words[k]?[show] ?? words[k]!["en"]! }
        func row(_ label: String) -> XCUIElement {
            app.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", label, label + ",")).firstMatch
        }
        // drag (no fling) until an element's top sits at or above the given share of the screen height
        func lift(_ e: XCUIElement, to share: CGFloat) {
            var n = 0
            while (!e.exists || e.frame.minY > app.frame.height * share) && n < 40 {
                app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7))
                    .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.62)),
                           withVelocity: .slow, thenHoldForDuration: 0.3)
                pause(0.4)
                n += 1
            }
        }

        tap("kelo.tab.my")
        pause(1.5)
        let usage = row(t("usage"))
        var n = 0
        while !usage.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        usage.tap()
        pause(2.5)
        let jobs = element(t("jobs"))
        lift(jobs, to: 0.25)
        pause(1)
        shot("usage-jobs")
        lift(element(t("month")), to: 0.25)
        pause(1)
        shot("usage-jobs-month")

        tap(t("back"))   // sub pages cover the tab bar
        pause(1.5)
        let model = row(t("model"))
        n = 0
        while !model.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        model.tap()
        pause(2.5)
        let same = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", t("same"))).firstMatch
        lift(same, to: 0.55)
        pause(1)
        shot("model-background")
    }

    /// On the connect screen: "connect with key", the mock's address and the key typed, then connect. `beforeConnect` runs once the
    /// fields are filled (a screenshot of them).
    private func connectWithKey(key: String, withKey: String? = nil, connect: String? = nil, beforeConnect: () -> Void = {}) {
        tap(withKey ?? w("withKey"))
        let server = app.textFields.firstMatch
        XCTAssertTrue(server.waitForExistence(timeout: 5))
        server.tap()
        server.typeText(serverText(server.value as? String, server))
        let secure = app.secureTextFields.firstMatch
        secure.tap()
        secure.typeText(key)
        beforeConnect()
        let submit = app.buttons.matching(NSPredicate(format: "label == %@", connect ?? w("connect"))).firstMatch
        if !submit.isHittable { app.swipeUp() }
        submit.tap()
    }

    private func serverText(_ current: String?, _ field: XCUIElement) -> String {
        let s = env["KELO_SERVER"]!
        if let current, !current.isEmpty, current != field.placeholderValue {
            field.press(forDuration: 1.0)
            if app.menuItems["Select All"].waitForExistence(timeout: 2) { app.menuItems["Select All"].tap() }
        }
        return s
    }
}
