import XCTest
import UIKit

/// Screens of the 2026-11 fix batch, against desktop/tools/mock_server.py with KELO_MOCK_BATCH=1, KELO_MOCK_SECRET_ENTRIES=1 and
/// KELO_MOCK_NO_ASKS=1 in its environment (KELO_BATCH=1 for this test). A sent message gets the batch reply: one photo looked at twice
/// (the chip says one photo), two events added one after the other with a card each (one chip, both cards under it), then a yes/no
/// question with tap answers. Then a picture pasted into the composer from the clipboard goes into the tray, and the Work page lists
/// the open secure entries in "답이 필요해요" at its top; a row leads to its card in the chat, marked, and its 취소 clears the badge.
/// Shots: <tag>-batch-chips, -batch-replies, -batch-paste, -batch-work, -batch-entry, -batch-cancelled. Other variables as
/// KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS.
final class KeloBatchShotsTests: XCTestCase {
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
        e.tap()
        return true
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

    func testBatch() throws {
        guard env["KELO_BATCH"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("no batch walk asked")
        }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)

        // ---- items 1 and 2: one photo counted once; two events asked one after the other, one chip with both cards under it
        let composer = element("kelo.composer")
        composer.tap()
        composer.typeText(w("Add the flights in this photo", "이 사진 속 항공편 일정에 넣어줘"))
        tap(element("kelo.send"), "send")
        let allow = app.buttons.matching(NSPredicate(format: "label == %@", w("Allow", "허용"))).firstMatch
        XCTAssertTrue(allow.waitForExistence(timeout: 25), "no open card\n\(app.debugDescription)")
        XCTAssertTrue(containing(w("Looked at 1 photos", "사진 1장 봄")).waitForExistence(timeout: 5), "the photo chip counts one photo")
        // the app's own one-time question about notifications: not now
        let later = app.buttons.matching(NSPredicate(format: "label == %@", w("Later", "나중에"))).firstMatch
        if later.waitForExistence(timeout: 3) { later.tap() }
        app.swipeDown(velocity: .slow)
        pause(1.5)
        shot("batch-chips")
        allow.tap()
        // ---- item 7: tap answers under the normal reply
        let yes = app.buttons.matching(NSPredicate(format: "label == %@", w("Yes", "네"))).firstMatch
        XCTAssertTrue(yes.waitForExistence(timeout: 20), "no yes/no buttons\n\(app.debugDescription)")
        pause(1.5)
        shot("batch-replies")

        // ---- item 3: a picture on the clipboard pasted into the composer goes into the tray
        let size = CGSize(width: 320, height: 200)
        let picture = UIGraphicsImageRenderer(size: size).image { ctx in
            UIColor.systemTeal.setFill(); ctx.fill(CGRect(origin: .zero, size: size))
            UIColor.white.setFill(); ctx.fill(CGRect(x: 40, y: 40, width: 120, height: 80))
        }
        UIPasteboard.general.image = picture
        composer.tap()
        pause(0.8)
        composer.press(forDuration: 1.2)
        let paste = app.menuItems[w("Paste", "붙여넣기")].firstMatch
        if !paste.waitForExistence(timeout: 5) { composer.doubleTap(); _ = paste.waitForExistence(timeout: 5) }
        tap(paste, "paste menu item")
        let allowPaste = app.alerts.buttons[w("Allow Paste", "붙여넣기 허용")]
        if allowPaste.waitForExistence(timeout: 2) { allowPaste.tap() }
        pause(3)
        shot("batch-paste")

        // ---- item 8: the header opens the Work page with "답이 필요해요" at its top; a row goes to its card, 취소 clears the badge
        app.swipeDown(velocity: .slow)
        pause(0.8)
        tap(element("kelo.header"), "header")
        let row = element("kelo.work.entry.sr-key")
        XCTAssertTrue(row.waitForExistence(timeout: 20), "no secure entry row in Work\n\(app.debugDescription)")
        pause(1.5)
        shot("batch-work")
        row.tap()
        XCTAssertTrue(element("kelo.secretEntry.key").waitForExistence(timeout: 15), "the chat did not show the key card\n\(app.debugDescription)")
        pause(0.6)
        shot("batch-entry")
        // the other two entries (a sign-in and a card) are cancelled from the Work page, the key on its card: the badge goes
        pause(1.5)
        let cancels = app.buttons.matching(identifier: "kelo.secretEntry.cancel").allElementsBoundByIndex
        if let cancel = cancels.first(where: { $0.isHittable }) { cancel.tap() } else { XCTFail("no cancel on the card on screen") }
        pause(1)
        tap(element("kelo.header"), "header again")
        for id in ["sr-key", "sr-login", "sr-card"] {
            let r = element("kelo.work.entry.\(id)")
            if r.waitForExistence(timeout: 10) {
                r.descendants(matching: .any).matching(identifier: "kelo.work.entry.cancel").firstMatch.tap()
                pause(1)
            }
        }
        pause(1.5)
        shot("batch-cancelled")
        tap(app.buttons.matching(NSPredicate(format: "label == %@", w("Back", "뒤로"))).firstMatch, "back")
        let header = element("kelo.header")
        XCTAssertTrue(header.waitForExistence(timeout: 5))
        pause(1)
        XCTAssertFalse(header.label.contains(w("waiting", "기다리는")), "the badge words are gone: \(header.label)")
        shot("batch-badge-gone")
    }
}
