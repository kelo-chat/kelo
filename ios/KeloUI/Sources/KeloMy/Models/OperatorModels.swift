import SwiftUI
import KeloCore
import KeloShared

// 마이 → 운영자: one model per card. Secrets go in and never come back out.

/// A sign-in app (GitHub / Google OAuth client) the operator's server uses for every copy.
@MainActor @Observable final class OAuthAppModel {
    private(set) var oauth: OAuthApp?
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let provider: String
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, provider: String) { self.api = api; self.provider = provider }

    private var path: String { "/api/oauth-apps/\(KeloApi.segment(provider))" }

    /// The saved client id (for the field), nil when it could not be read.
    func load() async -> String? {
        guard let a = try? await api.get(path, as: OAuthApp.self) else { return nil }
        oauth = a
        return a.clientId
    }

    /// `saved` gets the client id the server kept (the secret field is emptied by the page).
    func save(clientId: String, secret: String, saved: @escaping (String) -> Void) {
        message = ""
        jobs.run { [self] in
            do {
                var b: [String: JSON] = ["client_id": .string(clientId.trimmingCharacters(in: .whitespaces))]
                let s = secret.trimmingCharacters(in: .whitespaces)
                if !s.isEmpty { b["client_secret"] = .string(s) }
                let out: OAuthApp = try await api.put(path, body: .object(b))
                oauth = out
                saved(out.clientId)
                message = L(R.string.operator_saved)
            } catch { message = ApiMessage.text(error) }
        }
    }
}

/// The operator's push key (Firebase service account): uploaded as a file, never shown.
@MainActor @Observable final class FirebaseModel {
    private(set) var cfg: FirebaseConfig?
    private(set) var failed = false
    private(set) var busy = false
    private(set) var message = ""
    private(set) var error = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func load() async {
        do { cfg = try await api.get("/api/operator/firebase") } catch { failed = true }
    }

    func upload(_ url: URL) {
        busy = true; error = ""; message = ""
        jobs.run { [self] in
            defer { busy = false }
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            guard var bytes = try? Data(contentsOf: url), OperatorRules.keyFileOk(bytes) else { error = L(R.string.operator_fb_not_key); return }
            defer { bytes.resetBytes(in: 0..<bytes.count) }
            do {
                let out = try await api.requestBytes("PUT", "/api/operator/firebase", body: bytes)
                cfg = try KeloApi.decode(FirebaseConfig.self, out)
                message = L(R.string.operator_fb_saved)
            } catch {
                self.error = ApiMessage.text(error)   // the server's words never contain the key
            }
        }
    }

    func remove() {
        jobs.run { [self] in
            do { cfg = try await api.delete("/api/operator/firebase"); message = ""; error = "" }
            catch { self.error = ApiMessage.text(error) }
        }
    }
}

/// The free-address service's settings (Cloudflare): the API token goes in, never comes back.
@MainActor @Observable final class CloudflareModel {
    private(set) var cfg: CloudflareConfig?
    private(set) var failed = false
    private(set) var form = CloudflareForm()
    private(set) var busy = false
    private(set) var message = ""
    private(set) var error = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func setForm(_ f: CloudflareForm) { form = f }

    func load() async {
        do { let x: CloudflareConfig = try await api.get("/api/operator/cloudflare"); cfg = x; form = OperatorRules.formOf(x) }
        catch { failed = true }
    }

    func save() {
        message = ""; error = ""
        if let p = OperatorRules.problem(form) {
            error = switch p {
            case .token: L(R.string.operator_cf_bad_token)
            case .account: L(R.string.operator_cf_bad_account)
            case .zoneId: L(R.string.operator_cf_bad_zone_id)
            case .zone: L(R.string.operator_cf_bad_zone)
            case .max: L(R.string.operator_cf_bad_max)
            }
            return
        }
        busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                let out: CloudflareConfig = try await api.put("/api/operator/cloudflare", body: OperatorRules.cloudflareBody(form))
                cfg = out; form = OperatorRules.formOf(out)   // the token field empties: it is never shown again
                message = L(R.string.operator_saved)
            } catch { self.error = ApiMessage.text(error) }
        }
    }

    func reclaim() {
        message = ""; error = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                let r: Reclaimed = try await api.post("/api/operator/cloudflare/reclaim")
                message = r.released.isEmpty ? L(R.string.operator_cf_reclaim_none) : P(R.plurals.operator_cf_reclaimed, r.released.count, r.released.count)
                if let x: CloudflareConfig = try? await api.get("/api/operator/cloudflare") { cfg = x }
            } catch { self.error = ApiMessage.text(error) }
        }
    }

    func turnOff() {
        jobs.run { [self] in
            do { let x: CloudflareConfig = try await api.put("/api/operator/cloudflare", body: OperatorRules.cloudflareOff()); cfg = x; form = OperatorRules.formOf(x) }
            catch { self.error = ApiMessage.text(error) }
        }
    }
}

/// 운영자 → 받은 신고: what users reported from inside the app, newest first; mark handled or delete.
@MainActor @Observable final class ReportsModel {
    private(set) var data: ReceivedReports?
    private(set) var failed = false
    private(set) var showHandled = false
    private(set) var error = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var seq = Latest()
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func toggleHandled() { showHandled.toggle() }

    /// The page's `.task(id: showHandled)`: an answer for the other filter, coming back late, is dropped.
    func load() async {
        let n = seq.begin()
        do {
            let d: ReceivedReports = try await api.get(OperatorRules.reportsPath(showHandled: showHandled))
            guard seq.accept(n) else { return }
            data = d; failed = false
        } catch { if seq.accept(n) { failed = true } }
    }

    func mark(_ r: ReceivedReport) { act { [api] in _ = try await api.request("POST", "/api/operator/reports/\(r.id)", body: ["handled": .bool(!r.handled)]) } }
    func delete(_ id: Int64) { act { [api] in _ = try await api.request("DELETE", "/api/operator/reports/\(id)") } }

    private func act(_ f: @escaping @Sendable () async throws -> Void) {
        jobs.run { [self] in
            do { try await f(); await load() } catch { self.error = ApiMessage.text(error) }
        }
    }
}

/// 운영자 → 베타 신청: people asking to try Google sign-in; Approved or Decline.
@MainActor @Observable final class BetaRequestsModel {
    private(set) var data: BetaRequests?
    private(set) var failed = false
    private(set) var error = ""
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var seq = Latest()
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func load() async {
        let n = seq.begin()
        do {
            let d: BetaRequests = try await api.get("/api/operator/beta")
            guard seq.accept(n) else { return }
            data = d; failed = false
        } catch { if seq.accept(n) { failed = true } }
    }

    func answer(_ r: BetaRequestRow, action: String, mail: Bool) {
        error = ""; message = ""
        jobs.run { [self] in
            do {
                let a: BetaAnswer = try await api.post("/api/operator/beta/\(r.id)", body: ["action": .string(action), "email": .bool(mail)])
                message = a.mailed ? L(R.string.operator_beta_mailed, r.email) : a.mailError
                await load()
            } catch { self.error = ApiMessage.text(error) }
        }
    }

    func delete(_ id: Int64) {
        jobs.run { [self] in
            do { _ = try await api.request("DELETE", "/api/operator/beta/\(id)"); await load() } catch { self.error = ApiMessage.text(error) }
        }
    }
}
