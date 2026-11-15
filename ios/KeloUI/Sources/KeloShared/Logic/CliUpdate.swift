import Foundation
import KeloCore

// The owner updating the claude / agy CLI in its container from the app (Android my/CliUpdate.kt). POST /api/providers/{name}/cli/update
// starts it (the sidecar installs the newer CLI into its own home volume), then GET of the same path is asked every `pollSeconds`
// until it is done or failed. When it fails the old version keeps working.

/// The server's answer for an update: state idle | running | done | failed.
public struct CliUpdateStatus: Decodable, Equatable, Sendable {
    public var state = "idle"
    public var to: String?
    public var ok: Bool?
    /// why it failed, already in the user's language
    public var error: String?
    /// the newest version was already in use: nothing was installed
    public var unchanged = false
    /// "Claude Code" / "Antigravity CLI"
    public var product = ""

    public init(state: String = "idle", ok: Bool? = nil, error: String? = nil, product: String = "") {
        self.state = state; self.ok = ok; self.error = error; self.product = product
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        state = j.str("state", "idle")
        to = j.optStr("to")
        ok = j.optBool("ok")
        error = j.optStr("error")
        unchanged = j.bool("unchanged", false)
        product = j.str("product", "")
    }
}

public enum CliUpdateRules {
    public static let pollSeconds: Double = 2
    /// An install is cut off after 10 minutes on the server; the app stops asking a little later.
    public static let giveUpSeconds: Double = 12 * 60

    public enum Outcome: Equatable, Sendable {
        case running
        case updated(product: String, version: String)
        case unchanged(product: String)
        case failed(String)
    }

    /// The 업데이트 button: a newer CLI is out, this sign-in is the owner's and the CLI runs in a container that can update itself.
    public static func showButton(_ cli: CliStatus, busy: Bool) -> Bool { cli.installed && cli.updateAvailable && cli.updatable && !busy }

    public static func finished(_ st: CliUpdateStatus) -> Bool { st.state == "done" || st.state == "failed" }

    /// Which CLI gets its own card at the top of 마이 → 모델: the one a newer-CLI notice named (`asked`, a provider id), when it is
    /// not the `selected` service (that one shows its box in the form already) and the server lists it with a CLI. nil: none.
    public static func focus(_ asked: String?, selected: String, withCli: [String]) -> String? {
        guard let a = asked, !a.isEmpty, a != selected, withCli.contains(a) else { return nil }
        return a
    }

    public static func outcome(_ st: CliUpdateStatus, fallbackProduct: String = "") -> Outcome {
        let product = st.product.isEmpty ? fallbackProduct : st.product
        if st.state == "failed" || st.ok == false { return .failed(st.error ?? "") }
        if st.state == "done" { return st.unchanged ? .unchanged(product: product) : .updated(product: product, version: st.to ?? "") }
        return .running
    }

    public static func path(_ provider: String) -> String { "/api/providers/\(KeloApi.segment(provider))/cli/update" }

    /// Start the update and follow it to the end. A refusal (409: a chat is using the CLI, another update runs) comes back as failed.
    public static func run(_ api: KeloApi, provider: String, onProgress: (CliUpdateStatus) -> Void = { _ in }) async -> CliUpdateStatus {
        var st: CliUpdateStatus
        do {
            st = try await api.post(path(provider), body: .object([:]), as: CliUpdateStatus.self)
        } catch {
            return CliUpdateStatus(state: "failed", ok: false, error: (error as? ApiError)?.detail ?? "\(error)")
        }
        onProgress(st)
        let until = Date().addingTimeInterval(giveUpSeconds)
        var misses = 0
        while !finished(st) && Date() < until {
            try? await Task.sleep(nanoseconds: UInt64(pollSeconds * 1_000_000_000))
            do {
                st = try await api.get(path(provider), as: CliUpdateStatus.self)
                misses = 0
            } catch {
                let e = error as? ApiError
                misses += 1
                if !(e?.isTransient ?? false) || misses > 5 {
                    return CliUpdateStatus(state: "failed", ok: false, error: e?.detail ?? "\(error)", product: st.product)
                }
            }
            onProgress(st)
        }
        return st
    }
}
