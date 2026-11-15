import XCTest

/// 마이 → Kelo 서버 자원 (KELO_RESOURCES=1) against desktop/tools/mock_server.py, whose answer this test switches with
/// PUT /mock/resources (tools/mock_parts/my.py): first a server that cannot answer (the error with "try again", never a blank page),
/// then, after "try again", the exact answer that left the Android page blank (minimum/recommended memory as floats, 536870912.0).
/// Other variables as KeloScreensTests (KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG, KELO_SHOTS); screens are named
/// `<tag>-resources-error.png` and `<tag>-resources-phone.png`. Dark mode: `xcrun simctl ui <device> appearance dark` before the run.
/// Without KELO_RESOURCES the test is skipped.
final class KeloResourcesShotsTests: KeloShotCase {

    override func setUp() { continueAfterFailure = false }

    private func containing(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
    }

    /// What the mock answers GET /api/resources with from now on: fail | phone | phone-ints.
    private func mock(_ mode: String) {
        var r = URLRequest(url: URL(string: env["KELO_SERVER"]! + "/mock/resources")!, timeoutInterval: 10)
        r.httpMethod = "PUT"
        r.setValue("Bearer \(env["KELO_KEY"]!)", forHTTPHeaderField: "Authorization")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = Data(#"{"mode":"\#(mode)"}"#.utf8)
        let done = expectation(description: "mock \(mode)")
        var code = 0
        URLSession.shared.dataTask(with: r) { _, resp, _ in
            code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 15)
        XCTAssertEqual(code, 200, "the mock did not switch to \(mode)")
    }

    func testResources() throws {
        guard env["KELO_RESOURCES"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else { throw XCTSkip("no resources walk asked") }
        mock("fail")
        launchApp()
        signIn(key)
        tap(element("kelo.tab.my"), "My tab")
        pause(1.5)
        tap(containing(w("Kelo server resources", "Kelo 서버 자원")), "resources row")

        // the first read fails: the error and "try again", never a blank page
        let failed = containing(w("Couldn't load the server's resources", "서버 자원 정보를 불러오지 못했어요"))
        XCTAssertTrue(failed.waitForExistence(timeout: 15), "no error shown\n\(app.debugDescription)")
        let retry = app.buttons.matching(NSPredicate(format: "label == %@", w("Try again", "다시 시도"))).firstMatch
        XCTAssertTrue(retry.waitForExistence(timeout: 5), "no try again\n\(app.debugDescription)")
        pause(0.8)
        shot("resources-error")

        // the server answers again with the phone's exact answer (floats and all): "try again" shows its numbers
        mock("phone")
        retry.tap()
        XCTAssertTrue(containing("assistant limits").waitForExistence(timeout: 15), "the numbers did not show\n\(app.debugDescription)")
        XCTAssertTrue(containing("512MB").exists, "the minimum memory (536870912.0) should read as 512MB\n\(app.debugDescription)")
        XCTAssertFalse(failed.exists, "the error should be gone")
        pause(1.0)
        shot("resources-phone")
    }
}
