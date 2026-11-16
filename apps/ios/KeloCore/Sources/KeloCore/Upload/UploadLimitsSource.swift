import Foundation

/// The upload limits of ONE signed-in session: asks GET /api/uploads/limits once and keeps the answer. A `KeloApi` makes one (the app
/// builds a new `KeloApi` on each sign-in or new pairing, so the answer is asked again then).
/// An old server answers 404 (or something unreadable): `UploadLimits.legacy`, kept. Any other failure (no network, a server error):
/// legacy for this upload only, nothing kept, so the next upload asks again.
public actor UploadLimitsSource {
    /// Not retained: the `KeloApi` owns its source (`KeloApi.uploadLimits`), and a retain cycle would keep every session alive.
    private unowned let api: KeloApi
    private var known: UploadLimits?
    private var asking: Task<UploadLimits, Never>?

    public init(api: KeloApi) { self.api = api }

    public func get() async -> UploadLimits {
        if let known { return known }
        if let asking { return await asking.value }
        let task = Task { await ask() }
        asking = task
        let limits = await task.value
        asking = nil
        return limits
    }

    private func ask() async -> UploadLimits {
        do {
            let limits = UploadLimits(json: try await api.request("GET", "/api/uploads/limits")) ?? .legacy
            known = limits
            return limits
        } catch let e as ApiError {
            // 404: an old server. A 2xx answer that is not JSON (KeloApi reports it as status 0, "unreadable answer"): unreadable.
            if e.status == 404 || (e.isNetwork && e.detail == "unreadable answer") { known = .legacy }
            return .legacy
        } catch {
            return .legacy
        }
    }
}
