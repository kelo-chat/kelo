import SwiftUI
import Foundation
import KeloCore
import KeloShared

/// The server's oldest supported iPhone build (`min_app_version.ios` in GET /api/profile, ASSISTANT_MIN_APP_IOS in
/// src/assistant/server.py) and where an older build is sent to update (Android update/UpdateRules.belowMin).
public enum AppUpdateRules {
    /// The running build is older than the server's minimum. The build number is CFBundleVersion as a whole number; one that is
    /// not (or none at all, or no minimum) never stops the app.
    public static func belowMin(build: String?, min: Int64?) -> Bool {
        guard let min, min > 0, let b = build.flatMap({ Int64($0.trimmingCharacters(in: .whitespaces)) }), b > 0 else { return false }
        return b < min
    }

    /// Where the button goes: a TestFlight copy opens TestFlight (Kelo's page there when the App Store id is known); otherwise
    /// Kelo's App Store page, or a search for Kelo in the App Store while no id is set (`KeloAppStoreID` in Info.plist).
    public static func storeURL(appStoreId: String?, testFlight: Bool) -> URL {
        let id = (appStoreId ?? "").trimmingCharacters(in: .whitespaces)
        let known = !id.isEmpty && id.allSatisfy(\.isNumber)
        let text: String
        switch (testFlight, known) {
        case (true, true): text = "itms-beta://beta.itunes.apple.com/v1/app/\(id)"
        case (true, false): text = "itms-beta://"
        case (false, true): text = "itms-apps://apps.apple.com/app/id\(id)"
        case (false, false): text = "itms-apps://search.itunes.apple.com/WebObjects/MZSearch.woa/wa/search?media=software&term=Kelo"
        }
        return URL(string: text)!
    }

    /// This app's build number (CFBundleVersion).
    public static var runningBuild: String? { Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String }

    /// Installed from TestFlight: its receipt is the sandbox one.
    public static var isTestFlight: Bool { Bundle.main.appStoreReceiptURL?.lastPathComponent == "sandboxReceipt" }

    public static var appStoreId: String? { Bundle.main.object(forInfoDictionaryKey: "KeloAppStoreID") as? String }

    /// "0.9.0 (26111011)": Kelo's release (version.json) and the App Store build number, as My shows it.
    public static var versionLine: String { AppInfoLine.line(version: KeloVersion.version, build: runningBuild) }
}

/// The server says this build is too old: this covers the whole app until it is updated (Android update/UpdateUi.kt
/// UpdateRequiredScreen). 업데이트 opens Kelo in the App Store (or TestFlight).
struct UpdateRequiredView: View {
    @Environment(\.kelo) private var c
    @Environment(\.openURL) private var openURL

    var body: some View {
        ZStack {
            c.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 0) {
                    KeloIconView(.arrowDown, size: 40, tint: c.accent)
                    Text(L(R.string.update_required_title))
                        .font(KeloFont.titleLarge).foregroundStyle(c.ink).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 16)
                        .accessibilityAddTraits(.isHeader)
                    Text(L(R.string.update_required_body))
                        .font(KeloFont.body).foregroundStyle(c.muted).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: 420)
                        .padding(.top, 8)
                    let version = AppUpdateRules.versionLine
                    if !version.isEmpty {
                        Text(L(R.string.my_version, version)).font(KeloFont.small).foregroundStyle(c.muted)
                            .multilineTextAlignment(.center).padding(.top, 8)
                    }
                    PrimaryButton(L(R.string.update_now)) {
                        openURL(AppUpdateRules.storeURL(appStoreId: AppUpdateRules.appStoreId, testFlight: AppUpdateRules.isTestFlight))
                    }
                    .frame(maxWidth: 420)
                    .padding(.top, 24)
                }
                .padding(24)
                .frame(maxWidth: .infinity)
                .containerRelativeFrame(.vertical, alignment: .center) { h, _ in h }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("update-required")
    }
}

/// One full-screen notice over the app with an icon, a title, a body and one button (the server-side gates below).
private struct GateView: View {
    let icon: KeloIcon
    let title: String
    let message: String
    let note: String
    let button: String
    let action: () -> Void
    let id: String
    @Environment(\.kelo) private var c

    var body: some View {
        ZStack {
            c.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 0) {
                    KeloIconView(icon, size: 40, tint: c.accent)
                    Text(title).font(KeloFont.titleLarge).foregroundStyle(c.ink).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true).padding(.top, 16).accessibilityAddTraits(.isHeader)
                    Text(message).font(KeloFont.body).foregroundStyle(c.muted).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: 420).padding(.top, 8)
                    if !note.isEmpty {
                        Text(note).font(KeloFont.small).foregroundStyle(c.muted).multilineTextAlignment(.center).padding(.top, 8)
                    }
                    PrimaryButton(button, action: action).frame(maxWidth: 420).padding(.top, 24)
                }
                .padding(24)
                .frame(maxWidth: .infinity)
                .containerRelativeFrame(.vertical, alignment: .center) { h, _ in h }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(id)
    }
}

/// The server's KE2E key changed (409 e2e_key_changed): this phone pairs again with a new QR code. Nothing goes to the server until
/// then (no plaintext fallback).
struct PairAgainView: View {
    let onPair: () -> Void

    var body: some View {
        GateView(icon: .shield, title: L(R.string.e2e_key_changed_title), message: L(R.string.e2e_key_changed_body), note: "",
                 button: L(R.string.e2e_pair_again), action: onPair, id: "pair-again")
    }
}

/// What the server-too-old screen shows beyond its text: the "Update the server" button when the server can update itself for this
/// device, its progress, and the way out when it cannot (docs/version-notices.md part 2; Android shell/ServerOldUpdate.kt).
@MainActor @Observable
final class ServerOldModel {
    private(set) var phase: ServerOldRules.Phase = .probing
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let onDone: () -> Void

    init(api: @escaping () -> KeloApi?, onDone: @escaping () -> Void) { self.api = api; self.onDone = onDone }

    /// Asks whether the update button can show; safe to call again (Try again).
    func probe() {
        if case .running = phase { return }
        task?.cancel()
        task = Task { [weak self] in
            guard let self else { return }
            guard let api = self.api() else { self.phase = .manual; return }
            let p = await ServerOldRules.probe { await ServerUpdateRules.load(api) }
            if Task.isCancelled { return }
            self.phase = p
            if case .running = p, case .shown(let st) = await ServerUpdateRules.load(api) { await self.follow(api, from: st) }
        }
    }

    func update() {
        guard let api = api() else { return }
        if case .running = phase { return }
        task?.cancel()
        task = Task { [weak self] in
            let end = await ServerOldRules.run(
                check: { try await ServerUpdateRules.check(api) },
                start: { try await ServerUpdateRules.startUpdate(api) },
                follow: { st, on in await ServerUpdateRules.follow(api, from: st, onProgress: on) },
                onPhase: { p in await MainActor.run { self?.phase = p } })
            if end == .done { self?.onDone() }
        }
    }

    private func follow(_ api: KeloApi, from st: ServerUpdateStatus) async {
        let f = await ServerUpdateRules.follow(api, from: st) { [weak self] p in
            guard let j = p.status.job else { return }
            await MainActor.run { self?.phase = .running(fraction: ServerUpdateRules.fraction(j), step: ServerUpdateRules.step(j), reconnecting: p.reconnecting) }
        }
        switch f {
        case .finished(let s): phase = ServerOldRules.finished(s)
        case .gaveUp: phase = .failed(detail: nil)
        case .refused(let e): phase = .failed(detail: e.detail.isEmpty ? nil : e.detail)
        }
        if phase == .done { onDone() }
    }
}

/// The server is older than this app needs (docs/versioning.md: X-Kelo-Server below min_server, or a pinned server that answers
/// without KE2E). "Update the server" shows when the server can update itself for this device and follows the update, then asks the
/// version again and the screen closes; otherwise the short text and a link to the update guide. "Try again" asks once more.
struct ServerTooOldView: View {
    let server: String
    let onRetry: () -> Void
    let api: () -> KeloApi?
    @State private var model: ServerOldModel
    @Environment(\.kelo) private var c
    @Environment(\.openURL) private var openURL

    init(server: String, api: @escaping () -> KeloApi?, onRetry: @escaping () -> Void) {
        self.server = server; self.onRetry = onRetry; self.api = api
        _model = State(initialValue: ServerOldModel(api: api, onDone: onRetry))
    }

    var body: some View {
        let phase = model.phase
        let canPress: Bool = { if case .ready = phase { return true }; if case .failed = phase { return true }; return false }()
        let busy: Bool = { if case .running = phase { return true }; return phase == .done }()
        ZStack {
            c.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 0) {
                    KeloIconView(.update, size: 40, tint: c.accent)
                    Text(L(R.string.e2e_server_old_title)).font(KeloFont.titleLarge).foregroundStyle(c.ink).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true).padding(.top, 16).accessibilityAddTraits(.isHeader)
                    Text(L(R.string.e2e_server_old_body, KeloVersion.minServer)).font(KeloFont.body).foregroundStyle(c.muted)
                        .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true).frame(maxWidth: 420).padding(.top, 8)
                    if !server.isEmpty {
                        Text(L(R.string.e2e_server_old_version, server)).font(KeloFont.small).foregroundStyle(c.muted)
                            .multilineTextAlignment(.center).padding(.top, 8)
                    }
                    if let note = note(phase) {
                        Text(note).font(KeloFont.small).foregroundStyle(c.muted).multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true).frame(maxWidth: 420).padding(.top, 8)
                            .accessibilityIdentifier("server-too-old-note")
                    }
                    if canPress {
                        PrimaryButton(L(R.string.server_old_update)) { model.update() }.frame(maxWidth: 420).padding(.top, 24)
                        SecondaryButton(L(R.string.e2e_retry), fill: true, action: retry).frame(maxWidth: 420).padding(.top, 8)
                    } else {
                        PrimaryButton(L(R.string.e2e_retry), enabled: !busy, action: retry).frame(maxWidth: 420).padding(.top, 24)
                    }
                    if phase == .manual || { if case .failed = phase { return true }; return false }() {
                        Button { openURL(ServerOldRules.installURL) } label: {
                            Text(L(R.string.server_old_guide)).font(KeloFont.body).foregroundStyle(c.accent).multilineTextAlignment(.center)
                                .fixedSize(horizontal: false, vertical: true).padding(.vertical, 10)
                        }
                        .frame(maxWidth: 420).padding(.top, 4)
                        .accessibilityIdentifier("server-too-old-guide")
                    }
                }
                .padding(24)
                .frame(maxWidth: .infinity)
                .containerRelativeFrame(.vertical, alignment: .center) { h, _ in h }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("server-too-old")
        .task { model.probe() }
    }

    private func retry() { onRetry(); model.probe() }

    private func stepName(_ step: String?) -> String {
        switch step {
        case "preflight": L(R.string.server_update_step_preflight)
        case "download": L(R.string.server_update_step_download)
        case "verify": L(R.string.server_update_step_verify)
        case "backup": L(R.string.server_update_step_backup)
        case "restart": L(R.string.server_update_step_restart)
        case "health": L(R.string.server_update_step_health)
        case "rollback": L(R.string.server_update_step_rollback)
        case "self_update": L(R.string.server_update_step_self_update)
        case "done": L(R.string.server_update_step_done)
        default: L(R.string.server_update_step_working)
        }
    }

    private func note(_ phase: ServerOldRules.Phase) -> String? {
        switch phase {
        case .running(let fraction, let step, let reconnecting):
            if reconnecting { return L(R.string.server_update_waiting) }
            return "\(stepName(step)) \(Int((fraction * 100).rounded()))%"
        case .done: return L(R.string.server_update_done_plain)
        case .failed(let detail): return detail ?? L(R.string.server_old_failed)
        case .manual: return L(R.string.server_old_manual)
        default: return nil
        }
    }
}

/// "A new Kelo app (x.y.z) works best with your server": the one-time banner (docs/version-notices.md part 3). The button opens
/// Kelo's page in the App Store; the cross closes it for this version.
struct AppNoticeBanner: View {
    let version: String
    let onOpen: () -> Void
    let onClose: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 4) {
            KeloIconView(.arrowDown, size: 20, tint: c.accent).padding(.leading, 4)
            Text(L(R.string.app_notice_new, version)).font(KeloFont.body).foregroundStyle(c.ink).multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading).padding(.leading, 6)
            Button(action: onOpen) {
                Text(L(R.string.app_notice_store)).font(KeloFont.body).foregroundStyle(c.accent).multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 8).frame(minHeight: 44)
            }
            .accessibilityIdentifier("kelo.appnotice.open")
            Button(action: onClose) {
                KeloIconView(.close, size: 18, tint: c.muted).frame(width: 44, height: 44)
            }
            .accessibilityLabel(L(R.string.close))
            .accessibilityIdentifier("kelo.appnotice.close")
        }
        .padding(.leading, 10).padding(.trailing, 0).padding(.vertical, 2)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.card).shadow(color: .black.opacity(0.12), radius: 8, y: 2))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.appnotice")
    }
}
