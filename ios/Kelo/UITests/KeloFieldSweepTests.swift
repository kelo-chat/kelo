import XCTest

/// Every My and Connectors screen, opened one by one against desktop/tools/mock_server.py, with a screenshot of each
/// (`<tag>-sweep-<page>.png`) and a check of what is on it: a one-value field taller than one line, text that runs past the screen's
/// edge. A problem fails the test with the screen and the element named. Run with
///   tools/build/ios.sh sim --only KeloUITests/KeloFieldSweepTests/testSweep [--lang ko] [--shots DIR]
/// Other variables as KeloScreensTests; without KELO_SERVER the test is skipped.
final class KeloFieldSweepTests: XCTestCase {
    private let env = ProcessInfo.processInfo.environment
    private var lang: String { env["KELO_LANG"] ?? "en" }
    private var tag: String { env["KELO_TAG"] ?? lang }
    private var app: XCUIApplication!
    private var problems: [String] = []

    private func w(_ en: String, _ ko: String) -> String { lang == "ko" ? ko : en }
    override func setUp() { continueAfterFailure = true }
    private func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

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

    private func signIn(_ key: String) {
        let withKey = element(w("Connect with access key", "접속 키로 연결하기"))
        if withKey.waitForExistence(timeout: 10) {
            withKey.tap()
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

    /// What is wrong on the screen now: a one-line field taller than one line, a text past the screen's edge.
    private func check(_ name: String) {
        let width = app.windows.firstMatch.frame.width
        for f in app.textFields.allElementsBoundByIndex + app.secureTextFields.allElementsBoundByIndex where f.exists && f.frame.height > 0 {
            if f.frame.height > 64 {
                problems.append("\(name): field \"\(f.placeholderValue ?? f.label)\" is \(Int(f.frame.height)) pt tall (wraps)")
            }
        }
        for t in app.staticTexts.allElementsBoundByIndex where t.exists && t.frame.width > 0 && t.frame.minY > 0 {
            if t.frame.maxX > width + 1 && t.frame.minX < width {
                problems.append("\(name): \"\(t.label.prefix(40))\" runs past the right edge")
            }
        }
    }

    private func safe(_ s: String) -> String {
        String(s.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? Character($0) : "_" }.prefix(32))
    }

    private var backButton: XCUIElement { element(w("Back", "뒤로")) }

    /// Opens every row of the page under `tab` that leads to another page, takes its picture and checks it, then comes back.
    private func sweep(tab: String, prefix: String, skip: [String]) {
        element(tab).tap()
        pause(2)
        shot("sweep-\(prefix)")
        check(prefix)
        var seen = Set<String>()
        for _ in 0..<10 {
            let rows = app.buttons.allElementsBoundByIndex.filter {
                $0.exists && $0.isHittable && !$0.label.isEmpty && $0.frame.width > app.windows.firstMatch.frame.width * 0.6
                    && !seen.contains($0.label) && !skip.contains(where: $0.label.contains) && !$0.identifier.hasPrefix("kelo.tab.")
            }
            if rows.isEmpty { app.swipeUp(); pause(0.8) }
            guard let row = rows.first ?? app.buttons.allElementsBoundByIndex.first(where: {
                $0.exists && $0.isHittable && !$0.label.isEmpty && $0.frame.width > app.windows.firstMatch.frame.width * 0.6
                    && !seen.contains($0.label) && !skip.contains(where: $0.label.contains) && !$0.identifier.hasPrefix("kelo.tab.")
            }) else { break }
            let label = row.label
            seen.insert(label)
            row.tap()
            pause(1.6)
            if backButton.exists {
                let name = "\(prefix)-\(safe(label))"
                shot("sweep-\(name)")
                check(name)
                app.swipeUp(); pause(0.6)
                shot("sweep-\(name)-lower")
                check("\(name)-lower")
                backButton.tap()
                pause(1.2)
            } else if app.sheets.count > 0 || app.alerts.count > 0 {
                let cancel = element(w("Cancel", "취소"))
                if cancel.exists { cancel.tap() } else { app.swipeDown() }
                pause(0.8)
            }
            if !element(tab).isHittable { break }
        }
    }

    func testSweep() throws {
        guard env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no mock server given") }
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
        signIn(key)
        pause(2)
        let skip = [w("Sign out", "로그아웃"), w("Delete", "삭제"), w("Disconnect", "연결 해제"), w("Reset", "초기화"), w("Install", "설치")]
        sweep(tab: "kelo.tab.my", prefix: "my", skip: skip)
        sweep(tab: "kelo.tab.connectors", prefix: "conn", skip: skip)
        if let dir = env["KELO_SHOTS"] {
            try? problems.joined(separator: "\n").write(toFile: dir + "/\(tag)-sweep-problems.txt", atomically: true, encoding: .utf8)
        }
        XCTAssertTrue(problems.isEmpty, "screens with a wrapped field or text past the edge:\n" + problems.joined(separator: "\n"))
    }
}
