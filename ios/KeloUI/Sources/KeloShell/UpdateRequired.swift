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

/// The server is older than this app needs (docs/versioning.md: X-Kelo-Server below min_server, or a pinned server that answers
/// without KE2E). The owner updates the server; "Try again" asks again.
struct ServerTooOldView: View {
    let server: String
    let onRetry: () -> Void

    var body: some View {
        GateView(icon: .update, title: L(R.string.e2e_server_old_title), message: L(R.string.e2e_server_old_body, KeloVersion.minServer),
                 note: server.isEmpty ? "" : L(R.string.e2e_server_old_version, server), button: L(R.string.e2e_retry), action: onRetry,
                 id: "server-too-old")
    }
}
