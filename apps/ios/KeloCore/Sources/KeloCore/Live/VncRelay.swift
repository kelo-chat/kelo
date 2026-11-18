import Foundation

/*
 * The VNC screen of one browser tab (Android shared agent/VncRelay.kt): the tab's X screen, drawn by noVNC in a web view, instead of
 * the JPEG picture. The socket is the server's `/api/browser/sessions/{id}/vnc`, opened with the live view's one-time ticket and the
 * live view's connection code (KE2E when the server key is pinned). The first text frame says whether the screen is ours or why not;
 * everything after it is raw RFB bytes both ways.
 */

/// Where the VNC screen stands. Anything but `.showing` means the JPEG picture is what the page shows.
public enum VncState: Equatable, Sendable {
    /// Nothing asked yet
    case idle
    /// The socket is being opened and the first frame awaited
    case opening
    /// The server's X screen was sized to `width` x `height` for this tab: the page can draw it
    case showing(width: Int, height: Int)
    /// The screen could not be had: the live view's picture stays (the fallback)
    case failed(VncFailure)
    /// The socket ended after it was showing: the fallback again
    case closed
}

public enum VncFailure: Equatable, Sendable { case busy, unavailable, unauthorized, noAnswer, noConnection }

/// The live area in physical pixels and the screen density, as the live view's `view` message carries them.
public struct VncViewSize: Equatable, Sendable {
    public let width: Int, height: Int, density: Double
    public init(width: Int, height: Int, density: Double) { self.width = width; self.height = height; self.density = density }

    /// From view points and the screen scale (the same rounding as `ViewReporter`).
    public init(widthPoints: Double, heightPoints: Double, scale: Double) {
        self.init(width: ViewReporter.physical(points: widthPoints, scale: scale),
                  height: ViewReporter.physical(points: heightPoints, scale: scale), density: max(scale, 1))
    }
}

/// Pure rules of the VNC screen (the first frame, which screen shows, when to open it).
public enum VncRules {
    /// How long the server has to answer with its first frame before the live picture is kept
    public static let answerMs = 4_000
    /// Frames the server may send before the page is attached; past this the screen is given up
    public static let earlyLimit = 4_096

    public enum Hello: Equatable, Sendable {
        case ready(width: Int, height: Int)
        case refused(VncFailure)
        case unknown
    }

    /// The first text frame: `{"type":"vnc","width":W,"height":H}` or `{"type":"error","code":…}`.
    public static func hello(_ text: String) -> Hello {
        guard let v = try? JSON.parse(text), v.objectValue != nil else { return .unknown }
        switch v["type"]?.stringValue {
        case "vnc":
            let w = v["width"]?.intValue ?? 0, h = v["height"]?.intValue ?? 0
            return w > 0 && h > 0 ? .ready(width: w, height: h) : .unknown
        case "error": return .refused(failure(v["code"]?.stringValue))
        default: return .unknown
        }
    }

    /// The server's error code: "busy" = another tab holds the screen; anything else the screen cannot be had for.
    public static func failure(_ code: String?) -> VncFailure {
        switch code {
        case "busy": return .busy
        case "unauthorized": return .unauthorized
        default: return .unavailable
        }
    }

    /// The ticket request failed: 401 = unauthorized (the app's own handling signs out), 404 = no such tab, anything else = no connection.
    public static func ticketFailure(status: Int) -> VncFailure {
        switch status {
        case 401: return .unauthorized
        case 404: return .unavailable
        default: return .noConnection
        }
    }

    /// The VNC screen is what the page shows only while it is showing; otherwise the JPEG picture (the fallback).
    public static func showsVnc(_ state: VncState) -> Bool { if case .showing = state { return true }; return false }

    /// Live frames over the WebSocket are sent only while the picture is shown: off while the VNC screen shows.
    public static func framesOn(_ state: VncState) -> Bool { !showsVnc(state) }

    /// Pause after the live area stops changing before the screen is opened again at the new size (a rotation settles in steps).
    public static let resizeDebounceMs = 300

    /// Is the live area different enough from the size the screen was opened with to open it again? A rotation or split screen changes the
    /// width or the density, or the height with no keyboard up. The keyboard only shortens the area (`keyboardUp`): the screen stays and
    /// the keyboard covers it, so nothing is reopened for that.
    public static func needsReconnect(opened: VncViewSize?, now: VncViewSize?, keyboardUp: Bool) -> Bool {
        guard let opened, let now, now.width > 0, now.height > 0, now.density > 0 else { return false }
        if opened.width != now.width || opened.density != now.density { return true }
        return opened.height != now.height && !keyboardUp
    }

    /// Open the screen now? Only once, when the page is live and knows its size.
    public static func shouldConnect(_ state: VncState, area: VncViewSize?, liveEnded: Bool) -> Bool {
        guard state == .idle, !liveEnded, let a = area else { return false }
        return a.width > 0 && a.height > 0 && a.density > 0
    }

    /// The path (below the server address) of a tab's VNC socket with the one-time ticket and the screen size.
    public static func path(tabId: String, ticket: String, view: VncViewSize) -> String {
        "/api/browser/sessions/\(KeloApi.segment(tabId))/vnc?ticket=\(KeloApi.query(ticket))&width=\(view.width)&height=\(view.height)&density=\(view.density)"
    }
}

/// What the server sent.
public enum VncInbound: Equatable, Sendable {
    case text(String)
    case binary(Data)
}

/// The socket under a `VncRelay` (a fake in tests). `send` queues the bytes in call order and returns at once.
public protocol VncTransport: AnyObject, Sendable {
    /// The next message; throws when the socket ends.
    func receive() async throws -> VncInbound
    func send(_ data: Data)
    func close()
}

/// One tab's VNC screen. `connect` runs once: it asks for the ticket, opens the socket and waits up to `answerMs` for the first frame.
/// The state ends as `.showing` (the page can attach), `.failed` (the live picture stays) or `.closed` (the socket ended later).
@MainActor
public final class VncRelay {
    public typealias Ticket = @Sendable () async throws -> String
    public typealias Opener = @Sendable (_ ticket: String, _ view: VncViewSize) async throws -> VncTransport

    public private(set) var state: VncState = .idle {
        didSet {
            guard state != oldValue else { return }
            onState?(state)
        }
    }
    /// Told on every change of `state`.
    public var onState: ((VncState) -> Void)?
    /// The live-area size the screen was last opened with (the server sized its X screen to it); changes when the screen is opened again.
    public private(set) var openedWith: VncViewSize?
    /// Told each time the screen was (re)opened, with the size it was opened with.
    public var onOpened: ((VncViewSize) -> Void)?
    /// Text the browser copied, waiting for the owner to take it (the copy chip); nil when there is none. Held in memory only, never logged,
    /// and gone when the screen closes or the owner takes it.
    public private(set) var clipboard: String? {
        didSet {
            guard clipboard != oldValue else { return }
            onClipboard?(clipboard)
        }
    }
    /// Told on every change of `clipboard`.
    public var onClipboard: ((String?) -> Void)?
    /// The first frame of the socket being opened has not come yet (state `.opening`, or `.showing` while a new size is being opened).
    private var awaiting = false

    private let ticket: Ticket
    private let opener: Opener
    private let answerMs: Int
    private var transport: VncTransport?
    /// Callbacks of an older socket are ignored
    private var gen = 0
    private var sink: (token: Int, deliver: (Data) -> Void, reset: () -> Void)?
    private var tokens = 0
    /// Server bytes that came before the page was attached
    private var early: [Data] = []
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var tasks: [Task<Void, Never>] = []

    public init(ticket: @escaping Ticket, open: @escaping Opener, answerMs: Int = VncRules.answerMs) {
        self.ticket = ticket; self.opener = open; self.answerMs = answerMs
    }

    /// The tab's VNC screen, with the one-time ticket of the live view and the live view's connection code.
    public convenience init(api: KeloApi, tabId: String, session: URLSession = .shared) {
        self.init(ticket: { try await LiveSocket(api: api, tabId: tabId).ticket().ticket },
                  open: { ticket, view in try await VncSocket.open(api: api, tabId: tabId, ticket: ticket, view: view, session: session) })
    }

    /// Opens the screen (once) and returns when it is settled: showing, failed or closed.
    public func connect(_ view: VncViewSize) async {
        guard state == .idle else { return }
        state = .opening
        awaiting = true
        await openSocket(view, reopening: false)
    }

    /// The live area changed size (rotation, split screen): closes the /vnc socket and opens a new one with the new size (the server sizes
    /// the X screen again on acquire). The state stays `.showing` meanwhile, so the page keeps the last picture; the page starts its new
    /// session when the server's first frame arrives. A new screen that cannot be had ends as `.failed`: the live view's picture takes over.
    /// Does nothing unless the screen is showing and no screen is being opened.
    public func reopen(_ view: VncViewSize) async {
        guard VncRules.showsVnc(state), !awaiting else { return }
        awaiting = true
        gen += 1
        early.removeAll()
        let old = transport
        transport = nil
        tasks.forEach { $0.cancel() }
        tasks.removeAll()
        old?.close()
        await openSocket(view, reopening: true)
    }

    private func openSocket(_ view: VncViewSize, reopening: Bool) async {
        gen += 1
        let mine = gen
        let t: String
        do { t = try await ticket() } catch let e as ApiError {
            if mine == gen { fail(VncRules.ticketFailure(status: e.status)) }; return
        } catch {
            if mine == gen { fail(.noConnection) }; return
        }
        guard mine == gen else { return }
        let socket: VncTransport
        do { socket = try await opener(t, view) } catch {
            if mine == gen { fail(.noConnection) }; return
        }
        guard mine == gen else { socket.close(); return }
        transport = socket
        tasks.append(Task { [weak self] in await self?.read(socket, mine, view, reopening) })
        let ns = UInt64(answerMs) * 1_000_000
        tasks.append(Task { [weak self] in
            try? await Task.sleep(nanoseconds: ns)
            self?.timedOut(mine)
        })
        if awaiting { await withCheckedContinuation { waiters.append($0) } }
    }

    /// The first frame is settled (or the attempt failed): whoever waits on it goes on.
    private func settle() {
        awaiting = false
        let w = waiters
        waiters = []
        w.forEach { $0.resume() }
    }

    private func fail(_ why: VncFailure) {
        state = .failed(why)
        settle()
    }

    /// Attaches the page: the bytes held so far go to it first, in order. Returns the token `detach` takes.
    /// `reset` is called when a new socket for a new screen size delivers its first frame, before any of its bytes.
    @discardableResult
    public func attach(_ deliver: @escaping (Data) -> Void, reset: @escaping () -> Void = {}) -> Int {
        tokens += 1
        for b in early { deliver(b) }
        early.removeAll()
        sink = (tokens, deliver, reset)
        return tokens
    }

    public func detach(_ token: Int) { if sink?.token == token { sink = nil } }

    /// The page's RFB bytes go to the server as they are (only while showing).
    public func send(_ bytes: Data) {
        guard VncRules.showsVnc(state) else { return }
        transport?.send(bytes)
    }

    /// The browser copied this text (already capped by `ClipboardRules.offer`): the owner may take it with a tap.
    public func offerClipboard(_ text: String) { clipboard = text }

    /// The owner took it or let it go: nothing is kept.
    public func clearClipboard() { clipboard = nil }

    /// The page went away for good: the socket closes.
    public func close() {
        gen += 1
        let t = transport
        transport = nil
        sink = nil
        early.removeAll()
        clipboard = nil
        tasks.forEach { $0.cancel() }
        tasks.removeAll()
        if state == .opening || VncRules.showsVnc(state) { state = .closed }
        settle()
        t?.close()
    }

    private func timedOut(_ mine: Int) {
        guard mine == gen, awaiting else { return }
        gen += 1
        let t = transport
        transport = nil
        fail(.noAnswer)
        t?.close()
    }

    private func read(_ socket: VncTransport, _ mine: Int, _ view: VncViewSize, _ reopening: Bool) async {
        do {
            while !Task.isCancelled {
                let m = try await socket.receive()
                guard mine == gen else { return }
                switch m {
                case .text(let text): hello(text, socket, view, reopening)
                case .binary(let data): serverBytes(data, socket)
                }
            }
        } catch {
            ended(mine)
        }
    }

    private func hello(_ text: String, _ socket: VncTransport, _ view: VncViewSize, _ reopening: Bool) {
        guard awaiting else { return }
        switch VncRules.hello(text) {
        case .ready(let w, let h):
            openedWith = view
            state = .showing(width: w, height: h)
            if reopening { sink?.reset() }   // before any byte of this socket reaches the page
            settle()
            onOpened?(view)
        case .refused(let why): refuse(why, socket)
        case .unknown: refuse(.unavailable, socket)
        }
    }

    private func refuse(_ why: VncFailure, _ socket: VncTransport) {
        gen += 1
        transport = nil
        fail(why)
        socket.close()
    }

    private func serverBytes(_ data: Data, _ socket: VncTransport) {
        guard VncRules.showsVnc(state) else { return }
        if let s = sink { s.deliver(data); return }
        early.append(data)
        if early.count > VncRules.earlyLimit {
            gen += 1
            transport = nil
            early.removeAll()
            state = .closed
            socket.close()
        }
    }

    private func ended(_ mine: Int) {
        guard mine == gen else { return }
        gen += 1
        transport?.close()
        transport = nil
        let before = awaiting
        if before { fail(.noConnection) }   // no first frame yet: the screen was never (re)opened
        else if case .showing = state { state = .closed }
    }
}
