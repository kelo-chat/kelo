import XCTest

/// What every screen walk (the *ShotsTests) shares: the environment (KELO_LANG, KELO_TAG, KELO_SHOTS, KELO_SERVER), the app, the
/// words in the walk's language, one screenshot (attached and, with KELO_SHOTS, written to a file named `<tag>-<name>.png`), finding
/// and tapping an element, and signing in with the access key. A walk subclasses this and keeps only what is its own.
class KeloShotCase: XCTestCase {
    let env = ProcessInfo.processInfo.environment
    var app: XCUIApplication!
    var lang: String { env["KELO_LANG"] ?? "en" }
    var tag: String { env["KELO_TAG"] ?? lang }

    func w(_ en: String, _ ko: String, _ de: String? = nil) -> String {
        switch lang {
        case "ko": ko
        case "de": de ?? en
        default: en
        }
    }

    override func setUp() { continueAfterFailure = true }

    /// The name a screenshot is saved under (an attachment, and with KELO_SHOTS a file `<name>.png`): `<tag>-<screen>`.
    func shotName(_ screen: String) -> String { "\(tag)-\(screen)" }

    func shot(_ name: String) {
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let a = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        a.name = shotName(name)
        a.lifetime = .keepAlways
        add(a)
        if let dir = env["KELO_SHOTS"] {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(shotName(name)).png"))
        }
    }

    /// A fresh launch in the walk's language.
    func launchApp() {
        app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(\(lang))", "-AppleLocale", lang == "ko" ? "ko_KR" : "en_US"]
        app.launch()
    }

    func element(_ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR identifier == %@", label, label)).firstMatch
    }

    @discardableResult
    func tap(_ e: XCUIElement, _ what: String, timeout: TimeInterval = 10) -> Bool {
        guard e.waitForExistence(timeout: timeout) else { XCTFail("not found: \(what)\n\(app.debugDescription)"); return false }
        var n = 0
        while !e.isHittable && n < 8 { app.swipeUp(velocity: .slow); n += 1 }
        e.tap()
        return true
    }

    func pause(_ s: Double) { Thread.sleep(forTimeInterval: s) }

    /// Signs in with the access key when the app opens on the start screen. `server`: the mock's address (default KELO_SERVER);
    /// `dismissNotifications`: also answers the system's notification question (iOS shows it on the first chat).
    func signIn(_ key: String, server: String? = nil, dismissNotifications: Bool = false) {
        let withKey = element(w("Connect with access key", "접속 키로 연결하기", "Mit Zugangsschlüssel verbinden"))
        if withKey.waitForExistence(timeout: 10) {
            tap(withKey, "connect with key")
            let field = app.textFields.firstMatch
            field.tap()
            let cur = (field.value as? String) ?? ""
            if !cur.isEmpty && cur != field.placeholderValue {
                field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: cur.count))
            }
            field.typeText(server ?? env["KELO_SERVER"]!)
            let secure = app.secureTextFields.firstMatch
            secure.tap()
            secure.typeText(key)
            let submit = app.buttons.matching(NSPredicate(format: "label == %@", w("Connect", "연결", "Verbinden"))).firstMatch
            if !submit.isHittable { app.swipeUp() }
            submit.tap()
        }
        XCTAssertTrue(element("kelo.composer").waitForExistence(timeout: 25), "chat did not open\n\(app.debugDescription)")
        if dismissNotifications {
            let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
            for target in [app!, springboard] {
                for label in ["Not Now", "지금 안 함"] where target.buttons[label].waitForExistence(timeout: 1) { target.buttons[label].tap(); pause(1) }
            }
        }
    }
}
