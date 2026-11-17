import Foundation
import Testing
@testable import KeloCore

/// Port of android agent/VncRelayTest.kt: the first frame, the fallbacks, bytes held for a page that is not attached yet, and the
/// bridge messages between the app and the page.
final class FakeVncSocket: VncTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var queue: [Result<VncInbound, Error>] = []
    private var waiter: CheckedContinuation<VncInbound, Error>?
    private var sentBytes: [Data] = []
    private var isClosed = false

    struct Ended: Error {}

    var sent: [Data] { lock.withLock { sentBytes } }
    var closed: Bool { lock.withLock { isClosed } }

    func push(_ m: VncInbound) { deliver(.success(m)) }
    func drop() { deliver(.failure(Ended())) }

    private func deliver(_ r: Result<VncInbound, Error>) {
        let w: CheckedContinuation<VncInbound, Error>? = lock.withLock {
            if let w = waiter { waiter = nil; return w }
            queue.append(r)
            return nil
        }
        w?.resume(with: r)
    }

    func receive() async throws -> VncInbound {
        try await withCheckedThrowingContinuation { c in
            let ready: Result<VncInbound, Error>? = lock.withLock {
                if !queue.isEmpty { return queue.removeFirst() }
                waiter = c
                return nil
            }
            if let ready { c.resume(with: ready) }
        }
    }

    func send(_ data: Data) { lock.withLock { sentBytes.append(data) } }

    func close() {
        lock.withLock { isClosed = true }
        drop()
    }
}

/// What the opener was asked for, and the socket it hands out.
@MainActor
final class VncHarness {
    let socket = FakeVncSocket()
    var tickets = 0
    var opened: (ticket: String, view: VncViewSize)?
    let view = VncViewSize(width: 500, height: 900, density: 2.75)
    var relay: VncRelay!
    private var connecting: Task<Void, Never>?

    init(ticket: @escaping @Sendable () async throws -> String = { "tk" }, openError: Error? = nil, answerMs: Int = 300) {
        relay = VncRelay(
            ticket: { [unowned self] in await self.countTicket(); return try await ticket() },
            open: { [unowned self] t, v in
                if let openError { throw openError }
                await self.recordOpen(t, v)
                return self.socket
            },
            answerMs: answerMs)
    }

    private func countTicket() { tickets += 1 }
    private func recordOpen(_ t: String, _ v: VncViewSize) { opened = (t, v) }

    /// Runs connect, delivers `deliver` once the socket is open (or nothing), and waits for connect to return.
    func connect(deliver: (VncHarness) -> Void = { _ in }) async {
        connecting = Task { await relay.connect(view) }
        await settle { self.opened != nil || (self.relay.state != .idle && self.relay.state != .opening) }
        deliver(self)
        await connecting?.value
    }

    func hello(_ text: String) { socket.push(.text(text)) }
    func server(_ bytes: [UInt8]) { socket.push(.binary(Data(bytes))) }

    /// Waits (up to two seconds) for `cond`.
    func settle(_ cond: @MainActor () -> Bool) async {
        var n = 0
        while !cond() && n < 1000 { try? await Task.sleep(nanoseconds: 2_000_000); n += 1 }
    }

    /// Lets the relay's reader take what was pushed.
    func drain() async { for _ in 0..<20 { await Task.yield() }; try? await Task.sleep(nanoseconds: 20_000_000) }
}

@MainActor @Suite struct VncRelayTests {
    @Test func aVncFrameOpensTheScreenWithTheTabsQuery() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        #expect(h.relay.state == .showing(width: 500, height: 900))
        #expect(h.opened?.ticket == "tk")
        #expect(h.opened?.view == VncViewSize(width: 500, height: 900, density: 2.75))
    }

    @Test func aBusyTabIsRefusedAndTheSocketCloses() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"error","code":"busy"}"#) }
        #expect(h.relay.state == .failed(.busy))
        #expect(h.socket.closed)
    }

    @Test func unavailableUnauthorizedAndUnknownAnswersFallBack() async {
        for (text, why) in [(#"{"type":"error","code":"unavailable"}"#, VncFailure.unavailable),
                            (#"{"type":"error","code":"unauthorized"}"#, .unauthorized),
                            (#"{"type":"error","code":"whatever"}"#, .unavailable),
                            (#"{"type":"error"}"#, .unavailable),
                            (#"{"type":"vnc","width":0,"height":900}"#, .unavailable),
                            (#"{"type":"hello"}"#, .unavailable),
                            ("not json", .unavailable)] {
            let h = VncHarness()
            await h.connect { $0.hello(text) }
            #expect(h.relay.state == .failed(why), "\(text)")
            #expect(h.socket.closed, "\(text)")
        }
    }

    @Test func noAnswerWithinTheWaitKeepsThePictureAndClosesTheSocket() async {
        let h = VncHarness(answerMs: 40)
        await h.connect()
        #expect(h.relay.state == .failed(.noAnswer))
        #expect(h.socket.closed)
        // a first frame that comes too late changes nothing
        h.hello(#"{"type":"vnc","width":500,"height":900}"#)
        try? await Task.sleep(nanoseconds: 20_000_000)
        #expect(h.relay.state == .failed(.noAnswer))
    }

    @Test func theDefaultWaitIsFourSeconds() {
        #expect(VncRules.answerMs == 4_000)
    }

    @Test func theSocketEndingBeforeTheFirstFrameIsNoConnection() async {
        let h = VncHarness()
        await h.connect { $0.socket.drop() }
        #expect(h.relay.state == .failed(.noConnection))
    }

    @Test func theSocketEndingWhileShowingFallsBack() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        h.socket.drop()
        await h.settle { h.relay.state == .closed }
        #expect(h.relay.state == .closed)
        #expect(!VncRules.showsVnc(h.relay.state))
        #expect(VncRules.framesOn(h.relay.state))
    }

    @Test func ticketFailuresFallBackWithoutOpeningASocket() async {
        for (error, why) in [(ApiError(status: 401, detail: ""), VncFailure.unauthorized), (ApiError(status: 404, detail: ""), .unavailable),
                             (ApiError(status: 502, detail: ""), .noConnection), (ApiError(status: 0, detail: "offline"), .noConnection)] {
            let h = VncHarness(ticket: { throw error })
            await h.connect()
            #expect(h.relay.state == .failed(why), "status \(error.status)")
            #expect(h.opened == nil)
        }
        let h = VncHarness(ticket: { throw URLError(.notConnectedToInternet) })
        await h.connect()
        #expect(h.relay.state == .failed(.noConnection))
    }

    @Test func aSocketThatCannotBeOpenedIsNoConnection() async {
        let h = VncHarness(openError: ApiError(status: 0, detail: "bad address"))
        await h.connect()
        #expect(h.relay.state == .failed(.noConnection))
    }

    @Test func connectRunsOnlyOnce() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        await h.relay.connect(h.view)
        #expect(h.tickets == 1)
        #expect(h.relay.state == .showing(width: 500, height: 900))
    }

    @Test func serverBytesBeforeThePageIsAttachedAreHeldAndDeliveredInOrder() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#); $0.server([1, 2]); $0.server([3]) }
        await h.drain() // let the reader take both frames
        var got: [Data] = []
        h.relay.attach { got.append($0) }
        #expect(got == [Data([1, 2]), Data([3])])
        h.server([4])
        await h.settle { got.count == 3 }
        #expect(got.last == Data([4]))
    }

    @Test func aDetachedPageGetsNothingMore() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        var got: [Data] = []
        let token = h.relay.attach { got.append($0) }
        h.relay.detach(token)
        h.server([9])
        try? await Task.sleep(nanoseconds: 30_000_000)
        #expect(got.isEmpty)
    }

    @Test func tooManyFramesForAPageThatNeverComesGiveUpTheScreen() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        for _ in 0...VncRules.earlyLimit { h.server([0]) }
        await h.settle { h.relay.state == .closed }
        #expect(h.relay.state == .closed)
        #expect(h.socket.closed)
    }

    @Test func thePagesBytesGoToTheServerOnlyWhileShowing() async {
        let h = VncHarness()
        h.relay.send(Data([7]))
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        h.relay.send(Data([1, 2, 3]))
        h.relay.send(Data([4]))
        #expect(h.socket.sent == [Data([1, 2, 3]), Data([4])])
        h.relay.close()
        h.relay.send(Data([5]))
        #expect(h.socket.sent.count == 2)
    }

    @Test func closingWhileShowingIsClosedAndTheSocketEnds() async {
        let h = VncHarness()
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        h.relay.close()
        #expect(h.relay.state == .closed)
        #expect(h.socket.closed)
    }

    @Test func closingWhileOpeningSettlesConnect() async {
        let h = VncHarness()
        await h.connect { $0.relay.close() }
        #expect(h.relay.state == .closed)
    }

    @Test func stateChangesAreReported() async {
        let h = VncHarness()
        var seen: [VncState] = []
        h.relay.onState = { seen.append($0) }
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
        #expect(seen == [.opening, .showing(width: 500, height: 900)])
    }
}

@Suite struct VncRulesTests {
    private let size = VncViewSize(width: 500, height: 900, density: 3)

    @Test func theLivePictureIsTheFallbackAndFramesGoOffOnlyWhileShowing() {
        #expect(VncRules.framesOn(.idle) && VncRules.framesOn(.opening) && VncRules.framesOn(.closed))
        #expect(VncRules.framesOn(.failed(.busy)))
        #expect(!VncRules.framesOn(.showing(width: 1, height: 1)))
        #expect(VncRules.showsVnc(.showing(width: 1, height: 1)))
        #expect(!VncRules.showsVnc(.failed(.noAnswer)))
    }

    @Test func theScreenOpensOnceWhenThePageIsLiveAndSized() {
        #expect(VncRules.shouldConnect(.idle, area: size, liveEnded: false))
        #expect(!VncRules.shouldConnect(.idle, area: nil, liveEnded: false))
        #expect(!VncRules.shouldConnect(.idle, area: VncViewSize(width: 0, height: 900, density: 3), liveEnded: false))
        #expect(!VncRules.shouldConnect(.idle, area: size, liveEnded: true))
        #expect(!VncRules.shouldConnect(.opening, area: size, liveEnded: false))
        #expect(!VncRules.shouldConnect(.failed(.busy), area: size, liveEnded: false))
        #expect(!VncRules.shouldConnect(.closed, area: size, liveEnded: false))
    }

    @Test func theSizeComesFromPointsAndScale() {
        #expect(VncViewSize(widthPoints: 390, heightPoints: 600.4, scale: 3) == VncViewSize(width: 1170, height: 1801, density: 3))
    }

    @Test func theSocketPathCarriesTicketAndSize() {
        #expect(VncRules.path(tabId: "tab 1", ticket: "a+b", view: size).hasPrefix("/api/browser/sessions/tab%201/vnc?ticket=a%2Bb&width=500&height=900&density=3"))
        #expect(VncSocket.socketURL(baseUrl: "https://kelo.example", tabId: "t", ticket: "x", view: size)?.absoluteString
                == "wss://kelo.example/api/browser/sessions/t/vnc?ticket=x&width=500&height=900&density=3.0")
        #expect(VncSocket.socketURL(baseUrl: "http://10.0.0.2:8080", tabId: "t", ticket: "x", view: size)?.scheme == "ws")
        #expect(VncSocket.socketURL(baseUrl: "ftp://x", tabId: "t", ticket: "x", view: size) == nil)
    }

    @Test func framesMessageIsTheLiveSocketsSwitch() {
        #expect(LiveMessages.frames(on: false).compactString == #"{"on":false,"type":"frames"}"#)
        #expect(LiveMessages.frames(on: true).compactString == #"{"on":true,"type":"frames"}"#)
    }

    @Test func helloReadsTheFirstFrame() {
        #expect(VncRules.hello(#"{"type":"vnc","width":1,"height":2}"#) == .ready(width: 1, height: 2))
        #expect(VncRules.hello(#"{"type":"error","code":"busy"}"#) == .refused(.busy))
        #expect(VncRules.hello("[]") == .unknown)
    }
}

@MainActor @Suite struct VncBridgeTests {
    private let allBytes = Data((0...255).map { UInt8($0) })

    private func decodeScript(_ script: String) throws -> String {
        let prefix = "window.keloVncReceive(", suffix = ")"
        #expect(script.hasPrefix(prefix) && script.hasSuffix(suffix))
        let literal = String(script.dropFirst(prefix.count).dropLast(suffix.count))
        // a JavaScript string literal of these escapes is also a JSON string
        return try #require(JSONSerialization.jsonObject(with: Data(literal.utf8), options: [.fragmentsAllowed]) as? String)
    }

    @Test func messagesRoundTripThroughBase64() throws {
        #expect(VncBridge.fromPage(VncBridge.data(allBytes)) == .bytes(allBytes))
        #expect(VncBridge.fromPage(#"{"t":"ready"}"#) == .ready)
        #expect(VncBridge.fromPage(#"{"t":"d","b":"!!!"}"#) == .unknown)
        #expect(VncBridge.fromPage(#"{"t":"zzz"}"#) == .unknown)
        #expect(VncBridge.fromPage("nope") == .unknown)
        #expect(VncBridge.open() == #"{"t":"open"}"#)
        #expect(VncBridge.hold(true) == #"{"t":"hold","on":true}"#)
        #expect(VncBridge.hold(false) == #"{"t":"hold","on":false}"#)
    }

    @Test func aScriptCarriesAMessageAsAJavaScriptString() throws {
        for text in [VncBridge.data(allBytes), VncBridge.hold(true), "quote \" backslash \\ newline \n tab \t sep \u{2028}\u{2029} é 한"] {
            #expect(try decodeScript(VncBridge.script(for: text)) == text)
        }
        #expect(!VncBridge.script(for: "a\u{2028}b").contains("\u{2028}"))
    }

    /// The page's side is a function that runs scripts; this one keeps them.
    private final class Page { var scripts: [String] = [] }

    private func showing(_ h: VncHarness) async {
        await h.connect { $0.hello(#"{"type":"vnc","width":500,"height":900}"#) }
    }

    @Test func theBridgeRoundTripsBytesBothWays() async throws {
        let h = VncHarness()
        await showing(h)
        let page = Page()
        let bridge = VncPageBridge(relay: h.relay) { page.scripts.append($0) }
        bridge.start()
        bridge.hold(true)

        // the page is not ready: server bytes wait
        h.server([1, 2, 3])
        await h.drain()
        #expect(page.scripts.isEmpty)

        // ready: open and hold come first, then what waited, in order
        bridge.pageMessage(#"{"t":"ready"}"#)
        #expect(try page.scripts.map(decodeScript) == [VncBridge.open(), VncBridge.hold(true), VncBridge.data(Data([1, 2, 3]))])

        // server -> page afterwards goes straight through
        h.server([4, 5])
        await h.settle { page.scripts.count == 4 }
        #expect(try decodeScript(page.scripts[3]) == VncBridge.data(Data([4, 5])))

        // page -> server
        bridge.pageMessage(VncBridge.data(allBytes))
        #expect(h.socket.sent == [allBytes])

        // hold follows the take-over state
        bridge.hold(false)
        #expect(try decodeScript(page.scripts.last!) == VncBridge.hold(false))
    }

    @Test func theHoldSetBeforeReadyIsWhatThePageGets() async throws {
        let h = VncHarness()
        await showing(h)
        let page = Page()
        let bridge = VncPageBridge(relay: h.relay) { page.scripts.append($0) }
        bridge.start()
        bridge.hold(true)
        bridge.hold(false)
        bridge.pageMessage(#"{"t":"ready"}"#)
        #expect(try page.scripts.map(decodeScript) == [VncBridge.open(), VncBridge.hold(false)])
    }

    @Test func aReleasedBridgeDeliversNothing() async throws {
        let h = VncHarness()
        await showing(h)
        let page = Page()
        let bridge = VncPageBridge(relay: h.relay) { page.scripts.append($0) }
        bridge.start()
        bridge.pageMessage(#"{"t":"ready"}"#)
        let before = page.scripts.count
        bridge.release()
        h.server([1])
        bridge.hold(true)
        bridge.pageMessage(#"{"t":"ready"}"#)
        try? await Task.sleep(nanoseconds: 30_000_000)
        #expect(page.scripts.count == before)
    }
}

// MARK: - rotation: the screen is opened again at the new size

/// A relay whose every open gets a new socket, so a screen opened again can be told from the first one.
@MainActor
final class VncReopenHarness {
    var sockets: [FakeVncSocket] = []
    var views: [VncViewSize] = []
    var relay: VncRelay!
    let portrait = VncViewSize(width: 500, height: 900, density: 2.75)
    let landscape = VncViewSize(width: 900, height: 500, density: 2.75)

    init(answerMs: Int = 400) {
        relay = VncRelay(
            ticket: { "tk" },
            open: { [unowned self] _, v in await self.make(v) },
            answerMs: answerMs)
    }

    private func make(_ v: VncViewSize) -> FakeVncSocket {
        let s = FakeVncSocket()
        sockets.append(s); views.append(v)
        return s
    }

    func settle(_ cond: @MainActor () -> Bool) async {
        var n = 0
        while !cond() && n < 1000 { try? await Task.sleep(nanoseconds: 2_000_000); n += 1 }
    }

    func showPortrait() async {
        let t = Task { await relay.connect(portrait) }
        await settle { !self.sockets.isEmpty }
        sockets[0].push(.text(#"{"type":"vnc","width":500,"height":900}"#))
        await t.value
    }

    /// Runs reopen, lets `answer` say what the new socket sends, and waits for reopen to return.
    func reopen(_ view: VncViewSize, answer: (VncReopenHarness) -> Void) async {
        let t = Task { await relay.reopen(view) }
        await settle { self.sockets.count >= 2 }
        answer(self)
        await t.value
    }
}

@MainActor @Suite struct VncRotationTests {
    @Test func aSizeChangeClosesTheSocketAndOpensANewOneWithTheNewSizeKeepingTheScreenShowing() async {
        let h = VncReopenHarness()
        await h.showPortrait()
        await h.reopen(h.landscape) { $0.sockets[1].push(.text(#"{"type":"vnc","width":900,"height":500}"#)) }
        #expect(h.sockets[0].closed)                                  // the old /vnc socket is closed
        #expect(h.views == [h.portrait, h.landscape])
        #expect(h.relay.state == .showing(width: 900, height: 500))
        #expect(h.relay.openedWith == h.landscape)
    }

    @Test func theLastPictureStaysWhileTheNewSocketIsOpening() async {
        let h = VncReopenHarness()
        await h.showPortrait()
        var seen: [VncState] = []
        h.relay.onState = { seen.append($0) }
        await h.reopen(h.landscape) { h in
            #expect(h.relay.state == .showing(width: 500, height: 900))    // not opening, not failed: the page keeps drawing the old screen
            h.sockets[1].push(.text(#"{"type":"vnc","width":900,"height":500}"#))
        }
        #expect(seen == [.showing(width: 900, height: 500)])
    }

    @Test func thePageIsToldToStartOverBeforeTheNewBytesAndOldBytesNeverReachIt() async throws {
        let h = VncReopenHarness()
        await h.showPortrait()
        var events: [String] = []
        h.relay.attach({ _ in events.append("bytes") }, reset: { events.append("reset") })
        h.sockets[0].push(.binary(Data([1])))
        await h.settle { events == ["bytes"] }
        await h.reopen(h.landscape) { h in
            h.sockets[1].push(.text(#"{"type":"vnc","width":900,"height":500}"#))
            h.sockets[1].push(.binary(Data([3])))
        }
        h.sockets[0].push(.binary(Data([2])))                          // the old socket is still talking
        await h.settle { events.count >= 3 }
        try? await Task.sleep(nanoseconds: 30_000_000)
        #expect(events == ["bytes", "reset", "bytes"])
    }

    @Test func thePagesBytesGoToTheNewSocketOnly() async {
        let h = VncReopenHarness()
        await h.showPortrait()
        await h.reopen(h.landscape) { $0.sockets[1].push(.text(#"{"type":"vnc","width":900,"height":500}"#)) }
        h.relay.send(Data([9]))
        #expect(h.sockets[0].sent.isEmpty)
        #expect(h.sockets[1].sent == [Data([9])])
    }

    @Test func aNewScreenThatIsRefusedOrSilentOrDroppedFallsBackToThePicture() async {
        let busy = VncReopenHarness()
        await busy.showPortrait()
        await busy.reopen(busy.landscape) { $0.sockets[1].push(.text(#"{"type":"error","code":"busy"}"#)) }
        #expect(busy.relay.state == .failed(.busy))
        #expect(VncRules.framesOn(busy.relay.state))

        let silent = VncReopenHarness(answerMs: 60)
        await silent.showPortrait()
        await silent.relay.reopen(silent.landscape)
        #expect(silent.relay.state == .failed(.noAnswer))

        let dropped = VncReopenHarness()
        await dropped.showPortrait()
        await dropped.reopen(dropped.landscape) { $0.sockets[1].drop() }
        #expect(dropped.relay.state == .failed(.noConnection))
    }

    @Test func aSecondChangeWhileOneIsOpeningDoesNotOpenAnotherSocket() async {
        let h = VncReopenHarness()
        await h.showPortrait()
        let first = Task { await h.relay.reopen(h.landscape) }
        await h.settle { h.sockets.count == 2 }
        await h.relay.reopen(VncViewSize(width: 700, height: 700, density: 2.75))
        #expect(h.sockets.count == 2)
        h.sockets[1].push(.text(#"{"type":"vnc","width":900,"height":500}"#))
        await first.value
    }

    @Test func nothingIsReopenedUnlessTheScreenIsShowing() async {
        let h = VncReopenHarness()
        await h.relay.reopen(h.landscape)
        #expect(h.sockets.isEmpty)
    }

    @Test func theRulesTellARotationFromAKeyboard() {
        let portrait = VncViewSize(width: 500, height: 900, density: 2.75)
        let landscape = VncViewSize(width: 900, height: 500, density: 2.75)
        #expect(VncRules.needsReconnect(opened: portrait, now: landscape, keyboardUp: false))
        #expect(VncRules.needsReconnect(opened: portrait, now: landscape, keyboardUp: true))          // the width changed: not the keyboard
        #expect(VncRules.needsReconnect(opened: portrait, now: VncViewSize(width: 500, height: 450, density: 2.75), keyboardUp: false))   // split screen
        #expect(!VncRules.needsReconnect(opened: portrait, now: VncViewSize(width: 500, height: 450, density: 2.75), keyboardUp: true))  // the keyboard only shortens it
        #expect(!VncRules.needsReconnect(opened: portrait, now: portrait, keyboardUp: false))
        #expect(VncRules.needsReconnect(opened: portrait, now: VncViewSize(width: 500, height: 900, density: 3), keyboardUp: false))
        #expect(!VncRules.needsReconnect(opened: nil, now: landscape, keyboardUp: false))
        #expect(!VncRules.needsReconnect(opened: portrait, now: nil, keyboardUp: false))
        #expect(!VncRules.needsReconnect(opened: portrait, now: VncViewSize(width: 0, height: 0, density: 2.75), keyboardUp: false))
        #expect(VncRules.resizeDebounceMs == 300)
    }

    @Test func theResetMessageReachesThePageInOrder() async throws {
        let h = VncReopenHarness()
        await h.showPortrait()
        var scripts: [String] = []
        let bridge = VncPageBridge(relay: h.relay) { scripts.append($0) }
        bridge.start()
        bridge.pageMessage(#"{"t":"ready"}"#)
        scripts.removeAll()
        await h.reopen(h.landscape) { h in
            h.sockets[1].push(.text(#"{"type":"vnc","width":900,"height":500}"#))
            h.sockets[1].push(.binary(Data([7])))
        }
        await h.settle { scripts.count >= 2 }
        #expect(VncBridge.reset() == #"{"t":"reset"}"#)
        #expect(scripts == [VncBridge.script(for: VncBridge.reset()), VncBridge.script(for: VncBridge.data(Data([7])))])
    }
}
