import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 내 기기: the devices that can open this assistant, adding one (a pairing link and its countdown) and removing one.
@MainActor @Observable final class DevicesModel {
    private(set) var devices: [Device] = []
    private(set) var current: Int64?
    private(set) var pair: (link: String, until: Date)?
    private(set) var left = 0
    private(set) var error = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let onThisDeviceRemoved: () -> Void
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored private var changes = 0
    @ObservationIgnored let jobs = Jobs()

    /// `onThisDeviceRemoved`: this phone itself was removed, so the app signs out.
    init(api: KeloApi, onThisDeviceRemoved: @escaping () -> Void) { self.api = api; self.onThisDeviceRemoved = onThisDeviceRemoved }

    var pairLink: String? { pair?.link }

    /// Reads the list; a newer read replaces a running one, and an answer older than a removal made since is dropped.
    func load() async {
        loading?.cancel()
        let started = changes
        let task = Task { @MainActor [self] in
            guard let d = try? await api.devices(), !Task.isCancelled, changes == started else { return }
            devices = d.devices; current = d.current
        }
        loading = task
        await task.value
    }

    /// Runs while a pairing link shows (`.task(id: pairLink)` of the visible page, so it ends with the page): counts down, and
    /// reads the list every 2 seconds so the new device shows as soon as it joins.
    func followPairing() async {
        while !Task.isCancelled, let p = pair {
            let s = max(0, Int(p.until.timeIntervalSinceNow.rounded()))
            left = s
            if s == 0 { pair = nil; return }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if Task.isCancelled { return }
            await load()
        }
    }

    func add() {
        error = ""
        jobs.run { [self] in
            do {
                let p: PairStart = try await api.post("/api/devices/pair", body: ["origin": .string(api.baseUrl)])
                guard let link = p.link, !link.isEmpty else { error = L(R.string.devices_no_link); return }
                pair = (link, Date().addingTimeInterval(Double(p.expiresIn)))
                left = p.expiresIn
            } catch {
                self.error = ApiMessage.text(error)
            }
        }
    }

    func closePairing() { pair = nil }

    func remove(_ d: Device) {
        jobs.run { [self] in
            do {
                try await api.removeDevice(d.id)
                changes += 1
                if d.id == current { onThisDeviceRemoved() } else { await load() }
            } catch {
                self.error = ApiMessage.text(error)
            }
        }
    }
}

/// 마이 → 자원: what this assistant may use on the server machine. Reloaded every 10 seconds while the page shows (`poll()` runs in
/// the page's `.task`, so it stops when the page goes away).
@MainActor @Observable final class ResourcesModel {
    private(set) var load = ResourcesLoad()
    /// Goes up on "try again": the page's `.task(id:)` starts the polling over.
    private(set) var attempt = 0
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi) { self.api = api }

    func retry() { load = load.retrying(); attempt += 1 }

    func poll() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(nanoseconds: 10_000_000_000)
        }
    }

    func refresh() async {
        do {
            let x = try await api.resources()
            if !Task.isCancelled { load = load.loaded(x) }
        } catch is CancellationError {
            return
        } catch {   // offline, an error answer, or numbers this app cannot read
            if !Task.isCancelled { load = load.readFailed() }
        }
    }
}
