import XCTest

/// Wrapping check in any language (KELO_WRAP=1): walks the tabs, the work page, the first rows of the lists and every page behind
/// 마이's rows by position (no word of the language is typed, only the access-key sign-in), one screenshot each, named `<tag>-wrap-NN-...`.
/// Added with the screen check after the structure work; the same file is used on the BEFORE and AFTER trees.
final class KeloWrapWalkTests: KeloShotCase {
    private var n = 0

    private func snap(_ name: String) { n += 1; shot(String(format: "wrap-%02d-%@", n, name)); }

    /// Back one page: the top-left button of the page's bar (an edge swipe is the fallback; it does not always pop).
    private func back() {
        let h = app.frame.height, w = app.frame.width
        if let b = app.buttons.allElementsBoundByIndex.first(where: { $0.exists && $0.isHittable && $0.frame.minY > 20 && $0.frame.minY < h * 0.2 && $0.frame.maxX < w * 0.35 }) { b.tap() }
        else {
            let a = app.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.5))
            a.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.5)))
        }
        pause(1)
    }

    /// Back to a tab bar: closes a sheet or dialog that a row opened (Cancel / Close in any of the walk's languages, else a swipe down),
    /// then the edge swipe back.
    private func recover(_ id: String) {
        let tabBar = element("kelo.tab.\(id)")
        for _ in 0..<4 {
            if tabBar.exists && tabBar.isHittable { return }
            let names = ["Cancel", "취소", "Abbrechen", "Close", "닫기", "Schließen", "Not now", "No", "Nein", "아니요"]
            if let b = app.buttons.allElementsBoundByIndex.first(where: { names.contains($0.label) && $0.isHittable }) { b.tap(); pause(1) }
            else { app.swipeDown(velocity: .fast); pause(0.8); back() }
        }
    }

    private func tab(_ id: String) { _ = tap(element("kelo.tab.\(id)"), "tab \(id)"); pause(1.5) }

    /// The rows (buttons that are not the tab bar) now on screen, top to bottom.
    private func rows() -> [XCUIElement] {
        app.buttons.allElementsBoundByIndex.filter { b in
            b.exists && b.isHittable && !b.identifier.hasPrefix("kelo.tab") && !b.identifier.hasPrefix("kelo.header") && b.frame.height > 36 && b.frame.width > 120
        }
    }

    private func label(_ e: XCUIElement) -> String { e.label.isEmpty ? e.identifier : e.label }

    /// Opens the rows of the current tab's list one by one (at most `limit`), a shot of each page, and comes back. The labels are
    /// collected first (scrolling down), and `skipLast` leaves out the last row (the sign-out row of 마이).
    private func openRows(tab id: String, prefix: String, limit: Int, scrolls: Int, skipLast: Bool = false) {
        tab(id)
        var labels: [String] = []
        for page in 0...scrolls {
            if page > 0 { app.swipeUp(); pause(0.8) }
            let new = rows().map { label($0) }.filter { !labels.contains($0) }
            if new.isEmpty && page > 0 { break }
            labels += new
        }
        if skipLast && labels.count > 1 { labels.removeLast() }
        for (i, name) in labels.prefix(limit).enumerated() {
            tab(id)
            for _ in 0..<scrolls { app.swipeDown(velocity: .fast); pause(0.3) }
            var row = rows().first { label($0) == name }
            var tries = 0
            while row == nil && tries <= scrolls { app.swipeUp(); pause(0.6); row = rows().first { label($0) == name }; tries += 1 }
            guard let r = row else { continue }
            r.tap(); pause(1.8)
            snap("\(prefix)-\(i)")
            back()
            recover(id)
        }
    }

    func testWrapWalk() throws {
        guard env["KELO_WRAP"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no wrap walk asked") }
        continueAfterFailure = true
        launchApp()
        signIn(key, dismissNotifications: true)
        pause(2)
        snap("chat")
        tab("goals"); snap("goals")
        tab("connectors"); snap("connectors")
        tab("my"); snap("my-top")
        app.swipeUp(); pause(0.8); snap("my-mid")
        app.swipeUp(); pause(0.8); snap("my-bottom")
        // the work page (the agent's badge opens it)
        tab("chat")
        if element("kelo.header").waitForExistence(timeout: 5) { element("kelo.header").tap(); pause(2); snap("work-top"); app.swipeUp(); pause(0.8); snap("work-mid"); back(); recover("chat") }
        tab("goals"); openRows(tab: "goals", prefix: "goal", limit: 2, scrolls: 0)
        tab("connectors"); openRows(tab: "connectors", prefix: "connector", limit: 3, scrolls: 0)
        tab("my"); openRows(tab: "my", prefix: "my", limit: 30, scrolls: 4, skipLast: true)
    }
}
