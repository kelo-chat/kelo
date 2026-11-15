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
    let url: String
    let title: String
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
    /// A short note over the picture ("the browser couldn't do that").
    var notice: String?

    @ObservationIgnored private var machine = LiveMachine()
    @ObservationIgnored private var socket: LiveSocket?
    @ObservationIgnored private var runTask: Task<Void, Never>?
    @ObservationIgnored private var statusTask: Task<Void, Never>?
    @ObservationIgnored private var inputChain: Task<Void, Never>?
    @ObservationIgnored private var running = false
    @ObservationIgnored private var lastSeq: Int64 = 0
    @ObservationIgnored private var socketOpen = false

    @ObservationIgnored private let cost: NetworkCost

    init(api: KeloApi, tabId: String, cost: NetworkCost = .shared) { self.api = api; self.tabId = tabId; self.cost = cost }

    /// The tab's own row (its title, host and status in the header); nil until read, or when it is gone.
    private(set) var tab: BrowserTab?

    func loadTab() async {
        let found = (try? await api.browserTabs(thumbnails: false))?.items.first { $0.id == tabId }
        if !Task.isCancelled { tab = found }
    }

    var quality: LiveQuality { LiveQuality.pick(metered: cost.metered) }

    func start() {
        guard !running else { return }
        running = true
        run(machine.mode == .polling ? .poll() : machine.start())
    }

    /// Off screen: the connection closes. `giveBack` also hands a held tab back to the agent.
    func stop(giveBack: Bool = true) {
        running = false
        runTask?.cancel(); runTask = nil
        statusTask?.cancel(); statusTask = nil
        socket?.close(); socket = nil
        socketOpen = false
        if giveBack && status.youControl {
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
                if !opened { opened = true; socketOpen = true; machine.opened(); mode = .live }
                switch ev {
                case .message(let m):
                    switch m {
                    case .status(let st): status = st
                    case .ended(let reason): run(machine.ended(reason)); return
                    case .error(let code, _): serverError(code)
                    default: break
                    }
                case .frame(let h, let data):
                    show(data, seq: h.seq, w: h.w, h: h.h, url: h.url, title: h.title)
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
                    show(f.jpeg, seq: f.seq, w: f.w, h: f.h, url: f.url, title: f.title)
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

    private func show(_ data: Data, seq: Int64, w: Int, h: Int, url: String, title: String) {
        guard let img = PlatformImage(data: data) else { return }
        if seq > 0 { lastSeq = max(lastSeq, seq) }
        let size = img.size
        frame = LiveShown(image: img, w: w > 0 ? w : Int(size.width), h: h > 0 ? h : Int(size.height), url: url, title: title)
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

    // ---- control and input

    enum ControlResult: Equatable { case ok, held, gone, failed(String) }

    func control(_ take: Bool) async -> ControlResult {
        controlBusy = true
        defer { controlBusy = false }
        do {
            let a = try await api.browserControl(tabId, take: take)
            status.youControl = a.youControl
            status.controller = a.controller.isEmpty ? status.controller : a.controller
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

    /// One input event (tap, scroll, key, text, back…), in order.
    func send(_ event: JSON) {
        let previous = inputChain
        let s = socketOpen && mode == .live ? socket : nil
        let api = api, id = tabId
        inputChain = Task { [weak self] in
            await previous?.value
            if let s {
                do { try await s.send(event); return } catch {}
            }
            do {
                try await api.browserInput(id, event)
            } catch let e as ApiError {
                switch e.status {
                case 409: self?.serverError("not_controller")
                case 422, 502: self?.notice = L(R.string.live_input_failed)
                default: if e.isNetwork { self?.notice = L(R.string.live_input_failed) }
                }
            } catch {}
        }
    }

    /// Typed text, in pieces the server takes. Nothing is kept after this.
    func type(_ text: String) { LiveMessages.chunks(text).forEach { send(LiveMessages.text($0)) } }
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
