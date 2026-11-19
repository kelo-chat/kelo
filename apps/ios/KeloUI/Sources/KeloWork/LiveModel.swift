import Foundation
import Observation
import Network
import KeloCore
import KeloShared

/// The picture shown and what it is of.
struct LiveShown {
    let image: PlatformImage
    let w: Int
    let h: Int
    let cssW: Int
    let cssH: Int
    let url: String
    let title: String
    /// The newest picture's secure flag (nil: the server did not say, e.g. the frame-by-frame path).
    var secure: Bool? = nil
    /// The frame's number (what an input says the owner was looking at) and the protocol / focus flags of its header.
    var seq: Int64 = 0
    var liveV: Int = 0
    var editing = false
    /// live_v 3: the focused field's keyboard kind and opaque id.
    var kind = "text"
    var field = ""
}

/// The server's answer to the touch with this id (taps and long presses ask for one).
struct TouchReply: Equatable {
    let id: Int
    let reply: InputReply
}

/// One tab's live view (Android agent/LiveClient.kt) on KeloCore's LiveSocket / liveFrame, driven by `LiveMachine`: the WebSocket
/// after a one-use ticket, or frames one at a time when it cannot be opened. Input goes over the WebSocket when it is open, else
/// POST …/input in order. Typed text is passed straight into the one message that carries it and is never kept or logged.
@MainActor @Observable
final class LiveModel {
    let api: KeloApi
    let tabId: String

    private(set) var mode: LiveMode = .connecting
    private(set) var end: LiveEnd?
    private(set) var status = LiveStatus()
    private(set) var frame: LiveShown?
    private(set) var controlBusy = false
    private(set) var pointer: LivePointer? = nil
    private(set) var tapBusy = false
    var isPageBusy: Bool { status.pageBusy || status.stale || tapBusy }
    /// A short note over the picture ("the browser couldn't do that").
    var notice: String?
    /// The newest answer to a touch, for its mark (landed / did not reach the page).
    private(set) var touchReply: TouchReply?
    /// A text field has focus on the page and the server says so (live protocol 2): the soft keyboard comes up. While the VNC screen
    /// shows the frames are off, so the meta line carries it (LiveHeader.editing).
    var editing: Bool { LiveHeader.editing(meta: meta, frame: frame?.editing) }
    /// The server takes live typing (live_v 3): the owner's keys go straight into the page through a hidden capture field, no visible type line.
    var liveTyping: Bool { !LiveKinds.isDevice(tabId) && LiveHeader.liveTyping(meta: meta, frameLiveV: frame?.liveV) }
    /// The focused field of the page (kind of keyboard, which field), nil when none has focus.
    var typing: Typing? {
        if let meta { return meta.editing ? Typing(kind: meta.kind, field: meta.field) : nil }
        if let f = frame, f.editing { return Typing(kind: f.kind, field: f.field) }
        return nil
    }
    /// Bumps whenever the capture field must start again from `RemoteTyping.empty` (the page's field is no longer what the app thinks).
    private(set) var typingResets = 0
    @ObservationIgnored private var typer: RemoteTypingSender?
    private func typerOrNew() -> RemoteTypingSender {
        if let typer { return typer }
        let t = RemoteTypingSender { [weak self] in self?.sendRaw($0) }
        typer = t
        return t
    }
    /// The capture field's new state: what changed goes to the page at once (see `RemoteTypingSender`).
    func typingUpdate(_ t: Typed) { typerOrNew().update(t) }
    /// The page keeps what the owner saw: a composition still showing is committed.
    func typingCommit() { typer?.commit() }
    /// The capture field starts again from empty (no steps); false while composing, then nothing changes.
    func typingReset() -> Bool { typer?.reset() ?? true }
    /// Named keys for the page (Enter, Tab, arrows, Backspace from the key bar): what composes is committed first, the keys go as one `edit`,
    /// and the capture field starts again (the page's caret moved or its text changed behind the buffer's back).
    func typingKeys(_ names: [String]) {
        typer?.commit()
        if !names.isEmpty { sendRaw(LiveMessages.edit(names.prefix(RemoteTyping.maxSteps).map { .key($0) })) }
        typer?.reset()
        typingResets += 1
    }
    /// Text the browser copied, waiting for the owner's tap on the copy chip; nil when there is none. Kept in memory only.
    private(set) var offeredClip: String?
    /// The tab's X screen over VNC (noVNC in a web view): tried first once the page is live and knows its size; the JPEG picture is the
    /// fallback (busy, unavailable, no answer in 4 s, socket lost). Android shared agent/VncRelay.kt + LivePage.kt.
    private(set) var vnc: VncState = .idle
    private(set) var vncRelay: VncRelay?
    /// The tab's address and title from a `meta` line while the frames are off; nil while the frame headers carry them.
    private(set) var meta: LiveMeta?
    var showsVnc: Bool { VncRules.showsVnc(vnc) }

    @ObservationIgnored private var machine = LiveMachine()
    @ObservationIgnored private var socket: LiveSocket?
    @ObservationIgnored private var runTask: Task<Void, Never>?
    @ObservationIgnored private var statusTask: Task<Void, Never>?
    @ObservationIgnored private var inputChain: Task<Void, Never>?
    @ObservationIgnored private var tapBusyTask: Task<Void, Never>?
    @ObservationIgnored private var running = false
    @ObservationIgnored private var lastSeq: Int64 = 0
    @ObservationIgnored private var socketOpen = false
    @ObservationIgnored private var viewReporter = ViewReporter()
    @ObservationIgnored private var viewOffer: (w: Double, h: Double, scale: Double)?
    @ObservationIgnored private var touchIds = 0
    @ObservationIgnored private var awaiting: [Int] = []   // ids of touches sent over the WebSocket whose answer has not come

    @ObservationIgnored private let cost: NetworkCost
    /// Whether the server should send JPEG frames over the live socket (false while the VNC screen shows)
    @ObservationIgnored private var framesOn = true
    /// The live area, once the page has measured it
    @ObservationIgnored private var vncArea: VncViewSize?
    /// The soft keyboard is up (it shortens the live area; that is not a size change for the VNC screen)
    @ObservationIgnored private var keyboardUp = false
    @ObservationIgnored private var reopenTask: Task<Void, Never>?
    /// How the tab's VNC screen is made (replaced in tests)
    @ObservationIgnored var makeRelay: (KeloApi, String) -> VncRelay = { VncRelay(api: $0, tabId: $1) }
    /// Puts text on the phone's clipboard (replaced in tests)
    @ObservationIgnored var copyText: (String) -> Void = { Clipboard.copy($0) }

    init(api: KeloApi, tabId: String, cost: NetworkCost = .shared) { self.api = api; self.tabId = tabId; self.cost = cost }

    /// The tab's own row (its title, host and status in the header); nil until read, or when it is gone.
    private(set) var tab: BrowserTab?
    /// The tabs of this task's window; the chips follow them (read again every few seconds and after a tap).
    private(set) var tabs: [LiveTab] = []

    func refreshTabs() async {
        if let l = try? await api.browserTabs(thumbnails: false), let t = l.items.first(where: { $0.id == tabId }), !Task.isCancelled { tabs = t.tabs }
    }

    func tabAction(_ t: LiveTab, close: Bool) async {
        try? await api.browserTabAction(tabId, tab: t.id, close: close)
        await refreshTabs()
    }

    func loadTab() async {
        let found = (try? await api.browserTabs(thumbnails: false))?.items.first { $0.id == tabId }
        if !Task.isCancelled { tab = found }
    }

    var quality: LiveQuality { LiveQuality.pick(metered: cost.metered) }

    func start() {
        guard !running else { return }
        running = true
        // a screen that closed or failed is replaced when the page comes back on screen, so it is tried again
        if vnc != .idle { vncRelay?.close(); vncRelay = nil; vnc = .idle; offeredClip = nil }
        run(machine.mode == .polling ? .poll() : machine.start())
        connectVncIfWanted()
    }

    /// Off screen: the connection closes. `giveBack` also hands a held tab back to the agent.
    func stop(giveBack: Bool = true) {
        typer?.commit()
        running = false
        runTask?.cancel(); runTask = nil
        statusTask?.cancel(); statusTask = nil
        tapBusyTask?.cancel(); tapBusyTask = nil
        tapBusy = false
        pointer = nil
        socket?.close(); socket = nil
        socketOpen = false
        awaiting = []
        viewReporter.reset()
        vncRelay?.close()
        offeredClip = nil
        reopenTask?.cancel(); reopenTask = nil
        framesOn = true
        // only a hand-over hold goes back when the page is left; the owner's own button hold stays until they hand it back
        if giveBack && LiveRules.giveBackOnLeave(status) {
            status.youControl = false; status.controller = "agent"
            let api = api, id = tabId
            Task { _ = try? await api.browserControl(id, take: false) }
        }
    }

    private func run(_ step: LiveStep) {
        guard running else { return }
        runTask?.cancel()
        switch step {
        case .openSocket(_, let delay):
            mode = machine.mode
            runTask = Task { [weak self] in
                if delay > 0 { try? await Task.sleep(nanoseconds: UInt64(delay) * 1_000_000) }
                await self?.openSocket()
            }
        case .poll(let delay):
            mode = .polling
            socket?.close(); socket = nil; socketOpen = false
            runTask = Task { [weak self] in
                if delay > 0 { try? await Task.sleep(nanoseconds: UInt64(delay) * 1_000_000) }
                await self?.pollLoop()
            }
            if statusTask == nil { statusTask = Task { [weak self] in await self?.statusLoop() } }
        case .end(let reason):
            mode = .ended; end = reason
            vncRelay?.close()
            offeredClip = nil
            socket?.close(); socket = nil; socketOpen = false
            statusTask?.cancel(); statusTask = nil
        }
    }

    private func openSocket() async {
        let ticket: LiveTicket
        do {
            ticket = try await LiveSocket(api: api, tabId: tabId).ticket()
        } catch let e as ApiError {
            if Task.isCancelled { return }
            run(e.status == 404 ? machine.noBrowser() : machine.connectFailed())
            return
        } catch {
            if Task.isCancelled { return }
            run(machine.connectFailed())
            return
        }
        if Task.isCancelled { return }
        let s = LiveSocket(api: api, tabId: tabId)
        socket = s
        var opened = false
        do {
            for try await ev in s.connect(ticket: ticket.ticket, quality: quality) {
                if Task.isCancelled { return }
                if !opened {
                    opened = true; socketOpen = true; machine.opened(); mode = .live
                    if !framesOn { sendFrames(false) }
                }
                switch ev {
                case .message(let m):
                    switch m {
                    case .status(let st):
                        status = st
                        if let p = st.pointer { pointer = p }
                        flushView()
                    case .pointer(let p):
                        pointer = p
                    case .meta(let m): meta = m
                    case .ended(let reason): run(machine.ended(reason)); return
                    case .inputReply(let r):
                        if r.input == "edit" {   // an `edit` the page did not take: what it shows is no longer known, the capture field starts again
                            if r.busy || r.stale || !r.ok { typer?.forceReset(); typingResets += 1 }
                        } else { answered(r) }
                    case .error(let code, let forType):
                        if (forType == "tap" || forType == "long_press") && !awaiting.isEmpty { awaiting.removeFirst() }   // that touch gets no answer
                        serverError(code)
                    default: break
                    }
                case .frame(let h, let data):
                    show(data, seq: h.seq, w: h.w, h: h.h, cssW: h.cssW, cssH: h.cssH, url: h.url, title: h.title, secure: h.secure, liveV: h.liveV, editing: h.editing, kind: h.kind, field: h.field)
                }
            }
            socketOpen = false
            if Task.isCancelled { return }
            run(machine.closed(1000))
        } catch let closed as LiveClosed {
            socketOpen = false
            if Task.isCancelled { return }
            run(opened || closed.code >= 4000 ? machine.closed(closed.code) : machine.connectFailed())
        } catch {
            socketOpen = false
            if Task.isCancelled { return }
            run(opened ? machine.closed(1006) : machine.connectFailed())
        }
    }

    private func pollLoop() async {
        var failures = 0
        while running && !Task.isCancelled {
            var code = 0
            do {
                if let f = try await api.liveFrame(tabId: tabId, after: lastSeq, quality: quality) {
                    code = 200
                    status.youControl = f.youControl
                    status.controller = f.controller.isEmpty ? "agent" : f.controller
                    show(f.jpeg, seq: f.seq, w: f.w, h: f.h, cssW: f.cssW, cssH: f.cssH, url: f.url, title: f.title)
                    flushView()
                } else {
                    code = 204
                }
            } catch let e as ApiError {
                code = e.status
            } catch {
                if Task.isCancelled { return }
                code = 0
            }
            if Task.isCancelled { return }
            failures = code == 200 || code == 204 ? 0 : failures + 1
            if code == 401 { return }
            switch machine.polled(status: code, failures: failures) {
            case .end(let r): run(.end(r)); return
            case .poll(let d): if d > 0 { try? await Task.sleep(nanoseconds: UInt64(d) * 1_000_000) }
            default: break
            }
        }
    }

    /// Without the WebSocket the agent's line and the viewers come from the tab list, every few seconds.
    private func statusLoop() async {
        while running && !Task.isCancelled {
            if let l = try? await api.browserTabs(thumbnails: false), let t = l.items.first(where: { $0.id == tabId }) {
                status.label = t.statusText
                status.viewers = t.viewers
                status.agent.busy = t.status == "working"
            }
            try? await Task.sleep(nanoseconds: 5_000_000_000)
        }
    }

    private func show(_ data: Data, seq: Int64, w: Int, h: Int, cssW: Int = 0, cssH: Int = 0, url: String, title: String,
                      secure: Bool? = nil, liveV: Int? = nil, editing: Bool = false, kind: String = "text", field: String = "") {
        guard let img = PlatformImage(data: data) else { return }
        if seq > 0 { lastSeq = max(lastSeq, seq) }
        meta = nil   // a picture came: its header carries the address and title again
        let size = img.size
        // the frame-by-frame path carries no header flags: keep what the WebSocket's newest frame said
        frame = LiveShown(image: img, w: w > 0 ? w : Int(size.width), h: h > 0 ? h : Int(size.height),
                          cssW: cssW, cssH: cssH, url: url, title: title, secure: secure,
                          seq: seq, liveV: liveV ?? frame?.liveV ?? 0, editing: editing, kind: kind, field: field)
        flushView()
    }

    private func serverError(_ code: String) {
        switch code {
        case "not_controller", "held":
            status.youControl = false
            notice = L(R.string.live_not_controller)
        case "browser":
            notice = L(R.string.live_input_failed)
        default: break
        }
    }

    // ---- the VNC screen

    /// The live area (points and screen scale) as the page measured it. The first time it is known the VNC screen opens.
    func reportVncArea(width: Double, height: Double, scale: Double) {
        guard width >= 1, height >= 1 else { return }
        vncArea = VncViewSize(widthPoints: width, heightPoints: height, scale: scale)
        connectVncIfWanted()
        scheduleVncReopen()
    }

    /// The soft keyboard opened or closed (iOS).
    func setKeyboard(up: Bool) {
        keyboardUp = up
        scheduleVncReopen()
    }

    /// A rotation or split screen: the /vnc socket is opened again at the new size, `VncRules.resizeDebounceMs` after the area settles; the
    /// last picture stays meanwhile. The keyboard only shortens the area: no reopening for that.
    private func scheduleVncReopen() {
        reopenTask?.cancel()
        reopenTask = nil
        guard running, showsVnc, let relay = vncRelay, let area = vncArea,
              VncRules.needsReconnect(opened: relay.openedWith, now: area, keyboardUp: keyboardUp) else { return }
        reopenTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(VncRules.resizeDebounceMs) * 1_000_000)
            guard !Task.isCancelled, let self else { return }
            await relay.reopen(area)
        }
    }

    private func connectVncIfWanted() {
        // the Android device has no X screen to show: its picture is the only view
        guard running, !LiveKinds.isDevice(tabId), vncRelay == nil || vnc == .idle,
              VncRules.shouldConnect(vnc, area: vncArea, liveEnded: mode == .ended), let area = vncArea else { return }
        let relay = vncRelay ?? makeRelay(api, tabId)
        vncRelay = relay
        relay.onState = { [weak self] s in self?.vncChanged(s) }
        relay.onOpened = { [weak self] _ in self?.scheduleVncReopen() }   // the area may have changed while the screen was being opened
        relay.onClipboard = { [weak self] text in self?.offeredClip = text }
        Task { await relay.connect(area) }
    }

    /// The owner tapped the copy chip: the offered text goes to the phone's clipboard, once, and nothing is kept.
    func takeClip() {
        guard let text = offeredClip else { return }
        copyText(text)
        offeredClip = nil
        vncRelay?.clearClipboard()
        notice = L(R.string.live_copied_to_phone)
    }

    private func vncChanged(_ s: VncState) {
        vnc = s
        setFrames(VncRules.framesOn(s))
    }

    /// JPEG frames over the live socket on or off (off while the VNC screen shows); control and input stay.
    func setFrames(_ on: Bool) {
        framesOn = on
        if socketOpen && mode == .live { sendFrames(on) }
    }

    private func sendFrames(_ on: Bool) {
        guard let s = socket else { return }
        Task { try? await s.send(LiveMessages.frames(on: on)) }
    }

    // ---- control and input

    enum ControlResult: Equatable { case ok, held, gone, failed(String) }

    func control(_ take: Bool, handover: Bool = false) async -> ControlResult {
        if !take { typer?.commit() }   // handing back while a syllable forms: the page keeps it
        controlBusy = true
        defer { controlBusy = false }
        do {
            let a = try await api.browserControl(tabId, take: take, handover: handover)
            status.youControl = a.youControl
            status.hold = a.hold; status.holderAway = a.holderAway
            status.controller = a.controller.isEmpty ? status.controller : a.controller
            flushView()
            return .ok
        } catch let e as ApiError {
            switch e.status {
            case 409: return .held
            case 404: return .gone
            default: return .failed(e.detail)
            }
        } catch {
            return .failed("")
        }
    }

    /// The live area's size in points and the screen scale. Goes to the server as `view` while controlling a tab, once the server speaks
    /// live protocol 2: now, after a rotation, after the soft keyboard opened or closed, and when control starts.
    func reportView(width: Double, height: Double, scale: Double) {
        viewOffer = (width, height, scale)
        flushView()
    }

    private func flushView() {
        guard let o = viewOffer else { return }
        if !status.youControl { viewReporter.reset(); return }
        if let m = viewReporter.offer(widthPoints: o.w, heightPoints: o.h, scale: o.scale, liveV: frame?.liveV ?? 0, controlling: true) {
            sendRaw(m)
        }
    }

    /// One touch (tap, long press) that will be answered: stamped with the frame the finger went down on, and its answer shows in
    /// `touchReply` under the returned id.
    @discardableResult
    func sendTouch(_ event: JSON, frameSeq: Int64?) -> Int {
        touchIds += 1
        let id = touchIds
        send(LiveMessages.acked(event), frameSeq: frameSeq, touchId: id)
        return id
    }

    private func answered(_ r: InputReply) {
        guard !awaiting.isEmpty else { return }
        touchReply = TouchReply(id: awaiting.removeFirst(), reply: r)
        if r.busy || !r.ok { notifyTapBusy() }
    }

    /// One input event (tap, scroll, key, text, back…), in order, carrying the number of the frame the owner was looking at
    /// (`frameSeq`, the newest shown when not given) to a server that speaks live protocol 2.
    func send(_ event: JSON, frameSeq: Int64? = nil, touchId: Int? = nil) {
        if event["type"]?.stringValue != "edit" { typer?.flush() }   // the owner's order on the socket
        sendRaw(LiveMessages.stamp(event, frameSeq: frameSeq ?? frame?.seq, liveV: frame?.liveV ?? 0), touchId: touchId)
    }

    private func sendRaw(_ event: JSON, touchId: Int? = nil) {
        let previous = inputChain
        let s = socketOpen && mode == .live ? socket : nil
        let api = api, id = tabId
        inputChain = Task { [weak self] in
            await previous?.value
            if let s {
                do {
                    if let touchId { self?.awaiting.append(touchId) }
                    try await s.send(event)
                    return
                } catch { if touchId != nil { self?.awaiting.removeAll { $0 == touchId } } }
            }
            do {
                let resp = try await api.browserInput(id, event)
                let reply = InputReply(json: resp)
                if let touchId { self?.touchReply = TouchReply(id: touchId, reply: reply) }
                if reply.busy { self?.notifyTapBusy() }
                if reply.stale { self?.notice = L(R.string.live_screen_changed) }
            } catch let e as ApiError {
                switch e.status {
                case 409: self?.serverError("not_controller")
                case 422, 502: self?.notice = L(R.string.live_input_failed)
                default: if e.isNetwork { self?.notice = L(R.string.live_input_failed) }
                }
            } catch {}
        }
    }

    private func notifyTapBusy() {
        tapBusy = true
        tapBusyTask?.cancel()
        tapBusyTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            self?.tapBusy = false
        }
    }

    /// Typed text, in pieces the server takes. Nothing is kept after this.
    func type(_ text: String) {
        if let t = typer {   // a paste: what composes is committed, then the field starts again
            t.flush()
            if !t.reset() { t.commit(); t.reset() }
            typingResets += 1
        }
        LiveMessages.chunks(text).forEach { send(LiveMessages.text($0)) }
    }
}

/// Whether the phone is on a costly network (cellular, a hotspot), for the live view's picture quality.
final class NetworkCost: @unchecked Sendable {
    static let shared = NetworkCost()
    private let monitor = NWPathMonitor()
    private let lock = NSLock()
    private var expensive = false

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self else { return }
            self.lock.lock(); self.expensive = path.isExpensive || path.isConstrained; self.lock.unlock()
        }
        monitor.start(queue: DispatchQueue(label: "chat.kelo.netcost"))
    }

    var metered: Bool { lock.lock(); defer { lock.unlock() }; return expensive }
}
