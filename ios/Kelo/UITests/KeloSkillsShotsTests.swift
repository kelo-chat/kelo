import XCTest

/// Screens of custom skills (KELO_SKILLS=1), against desktop/tools/mock_server.py (its mock_parts/skills.py): 마이 → 기록's "승인할
/// 스킬" with the two sample proposals (a new skill drafted from a task, with the outside-text note, and a change shown as its lines),
/// then a finished task's page with "스킬로 저장", the same button while the draft is written, the Records page with the draft row,
/// and a card just approved. Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG (e.g. ko-light),
/// KELO_SHOTS. Screens: `skills-review-<tag>.png`, `skills-review-change-<tag>.png`, `skills-save-<tag>.png`,
/// `skills-save-drafting-<tag>.png`, `skills-review-drafting-<tag>.png`, `skills-review-approved-<tag>.png`. Start a fresh mock for
/// each run (answers change its lists). Without KELO_SKILLS the test is skipped.
final class KeloSkillsShotsTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var tag: String { env["KELO_TAG"] ?? lang }
    private var app: XCUIApplication!

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }

    override func setUp() { continueAfterFailure = true }

    private func shot(_ name: String) {
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let a = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        a.name = "\(name)-\(tag)"
        a.lifetime = .keepAlways
        add(a)
        if let dir = env["KELO_SHOTS"] {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name)-\(tag).png"))
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
        var n = 0
        while !e.isHittable && n < 10 { app.swipeUp(velocity: .slow); n += 1 }
        e.tap()
        return true
    }

    /// Scrolls until `e` is on screen and its top sits in the upper part of it.
    private func bringUp(_ e: XCUIElement, _ what: String, top: CGFloat = 0.22) {
        var n = 0
        while !(e.exists && e.isHittable) && n < 14 { app.swipeUp(velocity: .slow); n += 1 }
        XCTAssertTrue(e.exists, "\(what) not found\n\(app.debugDescription)")
        n = 0
        while e.exists && e.frame.minY > app.frame.height * (top + 0.18) && n < 6 {
            let from = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7))
            let to = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7 - min(0.45, (e.frame.minY / app.frame.height) - top)))
            from.press(forDuration: 0.05, thenDragTo: to, withVelocity: .slow, thenHoldForDuration: 0.3)
            n += 1
        }
    }

    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

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

    private func openRecords() {
        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(containing(w("Weekly journal", "주간 일지")), "records row")
        pause(3)
    }

    private func back() {
        let b = element(w("Back", "뒤로"))
        if b.exists { b.tap() } else { app.swipeRight() }
        pause(1.2)
    }

    func testSkills() throws {
        guard env["KELO_SKILLS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no skills walk asked") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)

        // 1. the review list under the learned rules: the heading at the top, the new skill's card below it
        openRecords()
        let heading = element(w("Skills to approve", "승인할 스킬"))
        bringUp(heading, "skills heading", top: 0.12)
        pause(1.2)
        shot("skills-review")
        // the change's card with its lines
        let change = element(w("Change to a skill", "스킬 수정"))
        bringUp(change, "change card", top: 0.15)
        pause(1)
        shot("skills-review-change")
        back()

        // 2. a finished task's page (mock task 4) with "Save as skill"
        tap(element("kelo.header"), "header")
        pause(2.5)
        let task = containing(w("Weekly shopping list", "주간 장보기 목록"))
        var n = 0
        while !(task.exists && task.isHittable) && n < 14 { app.swipeUp(velocity: .slow); n += 1 }
        tap(task, "finished task")
        pause(2.5)
        let save = element("kelo.skill.save")
        XCTAssertTrue(save.waitForExistence(timeout: 10), "no Save as skill\n\(app.debugDescription)")
        pause(0.8)
        shot("skills-save")
        save.tap()
        pause(1.0)
        shot("skills-save-drafting")
        back()
        back()

        // 3. the Records page while the draft is written (the mock adds it 3 s after the POST), then a card approved
        tap(element("kelo.tab.my"), "My tab")
        pause(0.8)
        tap(containing(w("Weekly journal", "주간 일지")), "records row")
        pause(1.0)
        let drafting = element(w("Kelo is writing a skill draft…", "Kelo가 스킬 초안을 쓰는 중…"))
        if drafting.waitForExistence(timeout: 4) {
            bringUp(drafting, "drafting row", top: 0.12)
            shot("skills-review-drafting")
        }
        pause(6)   // the draft lands and the list looks again
        bringUp(heading, "skills heading", top: 0.12)
        let approve = app.buttons.matching(NSPredicate(format: "label == %@", w("Approve", "승인"))).firstMatch
        tap(approve, "approve")
        pause(0.4)
        let approved = element(w("Approved. Kelo will use this skill.", "승인했어요. Kelo가 이 스킬을 써요."))
        if approved.waitForExistence(timeout: 2) {
            shot("skills-review-approved")
        } else {
            XCTFail("no approved line\n\(app.debugDescription)")
        }
    }
}
