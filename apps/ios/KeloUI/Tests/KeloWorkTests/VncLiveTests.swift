import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloWork

/// A VNC socket that says what the test pushes.
private final class PushSocket: VncTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var queue: [VncInbound] = []
    private var waiter: CheckedContinuation<VncInbound, Error>?
    private var isClosed = false
    struct Ended: Error {}
    var closed: Bool { lock.withLock { isClosed } }

    func push(_ m: VncInbound) {
        let w: CheckedContinuation<VncInbound, Error>? = lock.withLock {
            if let w = waiter { waiter = nil; return w }
            queue.append(m); return nil
        }
        w?.resume(returning: m)
    }
    func receive() async throws -> VncInbound {
        try await withCheckedThrowingContinuation { c in
            let next: VncInbound? = lock.withLock {
                if !queue.isEmpty { return queue.removeFirst() }
                if isClosed { return nil }
                waiter = c; return nil
            }
            if let next { c.resume(returning: next) } else if closed { c.resume(throwing: Ended()) }
        }
    }
    func send(_ data: Data) {}
    func close() {
        let w: CheckedContinuation<VncInbound, Error>? = lock.withLock { isClosed = true; let w = waiter; waiter = nil; return w }
        w?.resume(throwing: Ended())
    }
}

@MainActor @Suite struct VncLiveModelTests {
    private func model(_ tab: String = "tab1", sockets: [PushSocket]) -> (LiveModel, () -> Int) {
        let t = RoutedTransport()
        t.down = true   // the live socket's ticket fails: only the VNC side is under test
        let m = LiveModel(api: t.api(), tabId: tab)
        var made = 0
        m.makeRelay = { _, _ in
            let socket = sockets[min(made, sockets.count - 1)]
            made += 1
            return VncRelay(ticket: { "tk" }, open: { _, _ in socket }, answerMs: 200)
        }
        return (m, { made })
    }

    @Test func theVncScreenOpensWhenThePageKnowsItsSizeAndTheServerSaysYes() async {
        let s = PushSocket()
        let (m, made) = model(sockets: [s])
        m.start()
        #expect(made() == 0)
        m.reportVncArea(width: 390, height: 600, scale: 3)
        s.push(.text(#"{"type":"vnc","width":1170,"height":1800}"#))
        #expect(await eventually("showing") { m.showsVnc })
        #expect(m.vnc == .showing(width: 1170, height: 1800))
        #expect(made() == 1)
        m.stop()
    }

    @Test func aBusyTabKeepsTheJpegPicture() async {
        let s = PushSocket()
        let (m, _) = model(sockets: [s])
        m.start()
        m.reportVncArea(width: 390, height: 600, scale: 3)
        s.push(.text(#"{"type":"error","code":"busy"}"#))
        #expect(await eventually("failed") { m.vnc == .failed(.busy) })
        #expect(!m.showsVnc)
        m.stop()
    }

    @Test func silenceFallsBackAfterTheWait() async {
        let s = PushSocket()
        let (m, _) = model(sockets: [s])
        m.start()
        m.reportVncArea(width: 390, height: 600, scale: 3)
        #expect(await eventually("no answer") { m.vnc == .failed(.noAnswer) })
        #expect(!m.showsVnc)
        m.stop()
    }

    @Test func aSocketThatEndsWhileShowingFallsBack() async {
        let s = PushSocket()
        let (m, _) = model(sockets: [s])
        m.start()
        m.reportVncArea(width: 390, height: 600, scale: 3)
        s.push(.text(#"{"type":"vnc","width":1170,"height":1800}"#))
        #expect(await eventually("showing") { m.showsVnc })
        s.close()
        #expect(await eventually("closed") { m.vnc == .closed })
        #expect(!m.showsVnc)
        m.stop()
    }

    @Test func comingBackOnScreenTriesTheVncScreenAgain() async {
        let a = PushSocket(), b = PushSocket()
        let (m, made) = model(sockets: [a, b])
        m.start()
        m.reportVncArea(width: 390, height: 600, scale: 3)
        a.push(.text(#"{"type":"error","code":"busy"}"#))
        #expect(await eventually("failed") { m.vnc == .failed(.busy) })
        m.stop()
        m.start()
        #expect(await eventually("second try") { made() == 2 })
        b.push(.text(#"{"type":"vnc","width":1170,"height":1800}"#))
        #expect(await eventually("showing") { m.showsVnc })
        m.stop()
        #expect(b.closed)
    }

    @Test func theSizeIsOnlyUsedOnceAndNotWhileStopped() async {
        let s = PushSocket()
        let (m, made) = model(sockets: [s])
        m.reportVncArea(width: 390, height: 600, scale: 3)   // not started: nothing opens
        #expect(made() == 0)
        m.start()
        #expect(await eventually("opens on start") { made() == 1 })
        m.reportVncArea(width: 390, height: 500, scale: 3)   // the keyboard came up
        m.reportVncArea(width: 0, height: 0, scale: 3)
        try? await Task.sleep(nanoseconds: 30_000_000)
        #expect(made() == 1)
        m.stop()
    }

    @Test func theAndroidDeviceViewNeverAsksForAnXScreen() async {
        let s = PushSocket()
        let (m, made) = model("android", sockets: [s])
        m.start()
        m.reportVncArea(width: 390, height: 600, scale: 3)
        try? await Task.sleep(nanoseconds: 30_000_000)
        #expect(made() == 0 && m.vnc == .idle)
        m.stop()
    }
}

/// What the opener of a relay was asked for, and the sockets it hands out in turn.
private final class Opens: @unchecked Sendable {
    private let lock = NSLock()
    private var asked: [VncViewSize] = []
    let sockets: [PushSocket]
    init(_ sockets: [PushSocket]) { self.sockets = sockets }
    var views: [VncViewSize] { lock.withLock { asked } }
    func next(_ v: VncViewSize) -> PushSocket { lock.withLock { asked.append(v); return sockets[min(asked.count, sockets.count) - 1] } }
}

@MainActor @Suite struct VncRotationModelTests {
    private func showing(_ opens: Opens) async -> LiveModel {
        let t = RoutedTransport()
        t.down = true
        let m = LiveModel(api: t.api(), tabId: "tab1")
        m.makeRelay = { _, _ in VncRelay(ticket: { "tk" }, open: { _, v in opens.next(v) }, answerMs: 500) }
        m.start()
        m.reportVncArea(width: 390, height: 600, scale: 3)
        opens.sockets[0].push(.text(#"{"type":"vnc","width":1170,"height":1800}"#))
        #expect(await eventually("showing") { m.showsVnc })
        return m
    }

    @Test func aRotationOpensTheScreenAgainAtTheNewSizeAfterTheDebounceKeepingTheLastPicture() async {
        let opens = Opens([PushSocket(), PushSocket()])
        let m = await showing(opens)
        m.reportVncArea(width: 600, height: 390, scale: 3)                  // rotated
        try? await Task.sleep(nanoseconds: 150_000_000)
        #expect(opens.views.count == 1)                                     // 300 ms have not passed
        #expect(await eventually("the new socket") { opens.views.count == 2 })
        #expect(opens.views[1] == VncViewSize(width: 1800, height: 1170, density: 3))
        #expect(m.showsVnc)                                                 // the last picture stays while it opens
        #expect(opens.sockets[0].closed)
        opens.sockets[1].push(.text(#"{"type":"vnc","width":1800,"height":1170}"#))
        #expect(await eventually("new size") { m.vnc == .showing(width: 1800, height: 1170) })
        m.stop()
    }

    @Test func aRotationThatSettlesInStepsOpensOneSocketAtTheLastSize() async {
        let opens = Opens([PushSocket(), PushSocket()])
        let m = await showing(opens)
        m.reportVncArea(width: 500, height: 450, scale: 3)
        try? await Task.sleep(nanoseconds: 120_000_000)
        m.reportVncArea(width: 600, height: 390, scale: 3)
        #expect(await eventually("the new socket") { opens.views.count == 2 })
        try? await Task.sleep(nanoseconds: 450_000_000)
        #expect(opens.views.count == 2)
        #expect(opens.views[1] == VncViewSize(width: 1800, height: 1170, density: 3))
        opens.sockets[1].push(.text(#"{"type":"vnc","width":1800,"height":1170}"#))
        m.stop()
    }

    @Test func theKeyboardIsNotASizeChange() async {
        let opens = Opens([PushSocket(), PushSocket()])
        let m = await showing(opens)
        m.setKeyboard(up: true)
        m.reportVncArea(width: 390, height: 330, scale: 3)                  // shorter, same width
        try? await Task.sleep(nanoseconds: 450_000_000)
        #expect(opens.views.count == 1)
        m.reportVncArea(width: 390, height: 600, scale: 3)                  // the keyboard went away
        m.setKeyboard(up: false)
        try? await Task.sleep(nanoseconds: 450_000_000)
        #expect(opens.views.count == 1 && m.showsVnc)
        m.stop()
    }

    @Test func aScreenThatCannotBeOpenedAgainFallsBackToThePicture() async {
        let opens = Opens([PushSocket(), PushSocket()])
        let m = await showing(opens)
        m.reportVncArea(width: 600, height: 390, scale: 3)
        #expect(await eventually("the new socket") { opens.views.count == 2 })
        opens.sockets[1].push(.text(#"{"type":"error","code":"busy"}"#))
        #expect(await eventually("fallback") { m.vnc == .failed(.busy) })
        #expect(!m.showsVnc)
        m.stop()
    }
}

@Suite struct VncAssetsTests {
    private func url(_ path: String, scheme: String = "kelo-vnc", host: String = "app") -> URL { URL(string: "\(scheme)://\(host)\(path)")! }

    @Test func thePageAndNoVncModulesAreServedWithTheirTypes() {
        let page = VncAssets.answer(for: VncAssets.page)
        #expect(page.status == 200 && page.mime == "text/html" && !page.body.isEmpty)
        for path in ["/view.js", "/core/rfb.js", "/core/display.js", "/vendor/pako/lib/zlib/inflate.js"] {
            let a = VncAssets.answer(for: url(path))
            #expect(a.status == 200 && a.mime == "text/javascript" && !a.body.isEmpty, "\(path)")
        }
        #expect(VncAssets.answer(for: url("/LICENSE.txt")).status == 200)
        #expect(VncAssets.answer(for: url("/SOURCE.md")).mime == "text/plain")
    }

    @Test func everyModuleThePageImportsIsThere() throws {
        // each `import … from './x.js'` / `'../y.js'` in noVNC's core resolves to a bundled file
        let root = VncAssets.root
        let walker = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var checked = 0
        let pattern = try NSRegularExpression(pattern: #"from\s+['"](\.{1,2}/[^'"]+)['"]"#)
        for case let file as URL in walker where file.pathExtension == "js" {
            let text = try String(contentsOf: file, encoding: .utf8)
            for m in pattern.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                let rel = String(text[Range(m.range(at: 1), in: text)!])
                let target = file.deletingLastPathComponent().appendingPathComponent(rel).standardizedFileURL
                #expect(FileManager.default.fileExists(atPath: target.path), "\(file.lastPathComponent) imports \(rel)")
                checked += 1
            }
        }
        #expect(checked > 20)
    }

    @Test func nothingOutsideTheFolderOrTheSchemeIsServed() {
        for bad in [url("/../Info.plist"), url("/core/../../view.html"), url("/core"), url("/nope.js"), url("/"), url(""),
                    url("/view.html", scheme: "https"), url("/view.html", host: "evil"), url("/%2e%2e/x")] {
            #expect(VncAssets.answer(for: bad).status == 404, "\(bad)")
        }
        #expect(VncAssets.allows(VncAssets.page))
        #expect(!VncAssets.allows(URL(string: "https://example.com/")))
        #expect(!VncAssets.allows(URL(string: "kelo-vnc://other/view.html")))
        #expect(!VncAssets.allows(nil))
    }

    @Test func thePageSpeaksTheNamesTheAppListensTo() throws {
        let js = String(decoding: VncAssets.answer(for: url("/view.js")).body, as: UTF8.self)
        #expect(js.contains("messageHandlers.keloVnc"))
        #expect(VncWebHost.handlerName == "keloVnc")
        #expect(js.contains("window.keloVncReceive"))
        #expect(VncBridge.script(for: "x").hasPrefix("window.keloVncReceive("))
        #expect(js.contains("'reset'"))       // the message the app sends when the screen is opened again at a new size
        #expect(VncBridge.reset() == #"{"t":"reset"}"#)
    }

    @Test func theAndroidAndIPhonePagesAreTheSameFiles() throws {
        // Android's assets/vnc is the original: the iPhone folder is a copy and the two must not drift apart
        let here = URL(fileURLWithPath: #filePath)   // apps/ios/KeloUI/Tests/KeloWorkTests/…
        let apps = here.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let android = apps.appendingPathComponent("android/kelo/app/src/main/assets/vnc")
        guard FileManager.default.fileExists(atPath: android.path) else { return }   // a checkout without the Android app
        for name in ["view.js", "view.html", "SOURCE.md", "LICENSE.txt", "core/rfb.js", "vendor/pako/lib/zlib/inflate.js"] {
            let a = try Data(contentsOf: android.appendingPathComponent(name))
            #expect(a == VncAssets.answer(for: url("/" + name)).body, "\(name)")
        }
    }
}

/// The real page in a real web view: noVNC loads from the bundled folder over `kelo-vnc://`, takes the server's first bytes through the
/// bridge and answers with its own (the RFB version line), which reach the server's side of the relay.
@MainActor @Suite struct VncWebViewTests {
    @Test func noVncRunsInTheWebViewAndTalksThroughTheBridge() async {
        let socket = PushSocket()
        let sent = SentBox()
        let recording = RecordingSocket(socket, sent)
        let relay = VncRelay(ticket: { "tk" }, open: { _, _ in recording }, answerMs: 5_000)
        let connecting = Task { await relay.connect(VncViewSize(width: 390, height: 600, density: 1)) }
        socket.push(.text(#"{"type":"vnc","width":390,"height":600}"#))
        await connecting.value
        #expect(relay.state == .showing(width: 390, height: 600))
        let host = VncWebHost(relay: relay)
        socket.push(.binary(Data("RFB 003.008\n".utf8)))
        let answered = await eventually("the page answers the version line") { sent.all.contains { String(decoding: $0, as: UTF8.self) == "RFB 003.008\n" } }
        host.release()
        relay.close()
        #expect(answered)
    }
}

private final class SentBox: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [Data] = []
    var all: [Data] { lock.withLock { items } }
    func add(_ d: Data) { lock.withLock { items.append(d) } }
}

private final class RecordingSocket: VncTransport, @unchecked Sendable {
    let inner: PushSocket, box: SentBox
    init(_ inner: PushSocket, _ box: SentBox) { self.inner = inner; self.box = box }
    func receive() async throws -> VncInbound { try await inner.receive() }
    func send(_ data: Data) { box.add(data) }
    func close() { inner.close() }
}
