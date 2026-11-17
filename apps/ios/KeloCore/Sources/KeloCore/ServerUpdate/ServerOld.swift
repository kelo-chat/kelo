import Foundation

/// "Update the server" on the server-too-old screen (docs/version-notices.md, part 2; Android shell/ServerOldUpdate.kt). The screen
/// offers the button only when GET /api/server-update answers for this device; otherwise it says where to update by hand
/// (`installURL`, the update section). The work is the one My → Server update does: check the release list, start the update, follow it
/// through the server's restart; when it is done the screen asks the server's version again and closes.
public enum ServerOldRules {
    public static let installURL = URL(string: "https://kelo.chat/install")!

    public enum Phase: Equatable, Sendable {
        /// Asking whether the server can update itself
        case probing
        /// No in-app update here (an older server, not the owner's device, built from source): the short text and the link
        case manual
        /// The button shows
        case ready(version: String?)
        case running(fraction: Double, step: String?, reconnecting: Bool)
        case done
        /// `detail` is the server's own reason (its language), when it gave one
        case failed(detail: String?)
    }

    /// What the status says: nothing to press, a running update to follow, or the button.
    public static func phase(of st: ServerUpdateStatus) -> Phase {
        guard st.state == ServerUpdateStatus.ok else { return .manual }
        if st.isRunning, let j = st.job { return .running(fraction: ServerUpdateRules.fraction(j), step: ServerUpdateRules.step(j), reconnecting: false) }
        let v = st.latest?.version.trimmingCharacters(in: .whitespaces) ?? ""
        return .ready(version: v.isEmpty ? nil : v)
    }

    /// The first look: GET /api/server-update (a refusal, an old server or any failure = manual).
    public static func probe(_ load: () async -> ServerUpdateRules.Load) async -> Phase {
        switch await load() {
        case .shown(let st): return phase(of: st)
        case .hidden, .failed: return .manual
        }
    }

    /// How a followed run ended.
    public static func finished(_ st: ServerUpdateStatus) -> Phase {
        if st.job?.state == ServerUpdateJob.done { return .done }
        let e = st.job?.error?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return .failed(detail: e.isEmpty ? nil : e)
    }

    /// Check the list again, start the update, follow it; `onPhase` gets every step. An update that cannot start (nothing newer, one
    /// is running already, built from source) is `.failed` with the server's reason.
    public static func run(
        check: () async throws -> ServerUpdateStatus,
        start: () async throws -> ServerUpdateStatus,
        follow: (ServerUpdateStatus, @escaping @Sendable (ServerUpdateRules.Progress) async -> Void) async -> ServerUpdateRules.Followed,
        onPhase: @escaping @Sendable (Phase) async -> Void
    ) async -> Phase {
        let last: Phase
        do {
            var st = try await check()
            if !st.isRunning {
                guard ServerUpdateRules.showUpdateButton(st), st.state == ServerUpdateStatus.ok else { await onPhase(.failed(detail: nil)); return .failed(detail: nil) }
                st = try await start()
            }
            await onPhase(phase(of: st))
            let f = await follow(st) { p in
                if let j = p.status.job { await onPhase(.running(fraction: ServerUpdateRules.fraction(j), step: ServerUpdateRules.step(j), reconnecting: p.reconnecting)) }
            }
            switch f {
            case .finished(let s): last = finished(s)
            case .gaveUp: last = .failed(detail: nil)
            case .refused(let e): last = .failed(detail: e.detail.isEmpty ? nil : e.detail)
            }
        } catch is CancellationError {
            return .probing
        } catch let e as ServerUpdateFailure {
            last = .failed(detail: e.status == ApiError.noResponse || e.detail.isEmpty ? nil : e.detail)
        } catch {
            last = .failed(detail: nil)
        }
        await onPhase(last)
        return last
    }
}
