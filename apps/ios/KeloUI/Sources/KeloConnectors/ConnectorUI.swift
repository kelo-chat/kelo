import SwiftUI
import KeloCore
import KeloShared
#if os(iOS)
import AuthenticationServices
import UIKit
#endif

// Small pieces of the connectors tab (Android connectors/ConnectorUi.kt, GoogleReview.kt).

/// A service's tile: its own app icon from the server when there is one (ConnectorIcons.swift; the services' trademarks, shown only
/// to say which service a row is), else its initials on a colour made from its id.
struct ConnectorTile: View {
    let x: ConnectorInfo
    let size: CGFloat

    var body: some View {
        ServiceIcon(icon: x.icon, small: x.iconSmall, color: x.iconColor, size: size) { InitialsTile(x: x, size: size) }
    }
}

/// The initials tile: the fallback when a service has no icon or it cannot be loaded.
struct InitialsTile: View {
    let x: ConnectorInfo
    let size: CGFloat
    @Environment(\.kelo) private var c

    var body: some View {
        let letters = ConnectorList.initials(x.name.isEmpty ? x.id : x.name)
        let (h, s, l) = ConnectorList.tileHSL(hue: ConnectorList.hue(x.id), dark: c.dark)
        RoundedRectangle(cornerRadius: size * 0.28)
            .fill(Color.hsl(h, s, l))
            .frame(width: size, height: size)
            .overlay(
                Text(letters).font(.system(size: size * (letters.count > 1 ? 0.36 : 0.44), weight: .bold))
                    .foregroundStyle(KeloColors.tileInk).lineLimit(1).minimumScaleFactor(0.6)
            )
            .accessibilityHidden(true)
    }
}

extension Color {
    /// HSL (hue 0..<360, saturation and lightness 0...1) to sRGB.
    static func hsl(_ h: Double, _ s: Double, _ l: Double) -> Color {
        let c = (1 - abs(2 * l - 1)) * s
        let hp = h / 60
        let x = c * (1 - abs(hp.truncatingRemainder(dividingBy: 2) - 1))
        let (r1, g1, b1): (Double, Double, Double) = switch hp {
        case ..<1: (c, x, 0)
        case ..<2: (x, c, 0)
        case ..<3: (0, c, x)
        case ..<4: (0, x, c)
        case ..<5: (x, 0, c)
        default: (c, 0, x)
        }
        let m = l - c / 2
        return Color(.sRGB, red: r1 + m, green: g1 + m, blue: b1 + m)
    }
}

/// A row's or the detail's badge.
struct RowBadgeView: View {
    let badge: ConnectorLogic.RowBadge?
    @Environment(\.kelo) private var c

    var body: some View {
        switch badge {
        case nil: EmptyView()
        case .reconnect: pill(L(R.string.connector_status_reconnect), bg: c.danger.opacity(0.12), fg: c.danger)
        case .review: pill(L(R.string.google_review_pill), bg: c.neutralSoft, fg: c.neutralInk)
        case .status(let b):
            let text = LS(ConnectorLogic.badgeKey(b))
            switch b {
            case .connected: pill(text, bg: c.successSoft, fg: c.successInk)
            case .available: pill(text, bg: c.infoSoft, fg: c.infoInk)
            case .browser, .autofill: pill(text, bg: c.neutralSoft, fg: c.neutralInk)
            case .unsupported: pill(text, bg: .clear, fg: c.muted, border: true)
            case .disabled, .comingSoon: pill(text, bg: c.bubble, fg: c.muted)
            }
        }
    }

    private func pill(_ text: String, bg: Color, fg: Color, border: Bool = false) -> some View {
        Text(text).font(KeloFont.caption).foregroundStyle(fg).lineLimit(2).multilineTextAlignment(.center)
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background(Capsule().fill(bg))
            .overlay(Capsule().stroke(border ? c.line : .clear, lineWidth: 1))
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// Numbered help lines from the server.
struct StepsView: View {
    let lines: [String]
    @Environment(\.kelo) private var c

    var body: some View {
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(lines.enumerated()), id: \.offset) { i, line in
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(L(R.string.step_number, i + 1)).font(KeloFont.small).foregroundStyle(c.ink).frame(minWidth: 20, alignment: .leading)
                        Text(line).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }
}

/// A bold line with the shield in front.
struct ShieldTitle: View {
    let text: String
    var small = false
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            KeloIconView(.shield, size: small ? 16 : 18, tint: c.ink)
            Text(TokenText.fit(text)).font(small ? KeloFont.small : KeloFont.button).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct BoldLine: View {
    let text: String
    @Environment(\.kelo) private var c
    var body: some View {
        Text(text).font(KeloFont.button).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
    }
}

struct SmallText: View {
    let text: String
    @Environment(\.kelo) private var c
    var body: some View {
        Text(text).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
    }
}

/// Why there is no Google sign-in button yet (the server's note, else the app's own) and the operator's support link; below it the
/// way to ask to be let in early (BetaRequestView). `onOpened` reloads the screen once that request is approved.
struct ReviewNote: View {
    let c: ConnectorInfo
    let api: KeloApi
    var onOpened: () -> Void = {}
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            BoldLine(text: L(R.string.google_review_title))
            MutedText(c.note.isEmpty ? L(R.string.google_review_note) : c.note, small: true)
            if ConnectorLogic.showsSupportLink(c.supportUrl), let url = URL(string: c.supportUrl) {
                LinkButton(L(R.string.google_review_support)) { openURL(url) }
            }
            BetaRequestView(c: c, api: api, onOpened: onOpened)
        }
    }
}

/// Runs `load` when `key` changes and every time the app comes back to the foreground (a sign-in finished in the browser).
struct ReloadOnReturn<K: Equatable>: ViewModifier {
    let key: K
    let load: () async -> Void
    @Environment(\.scenePhase) private var phase
    @State private var returns = 0

    func body(content: Content) -> some View {
        content
            .onChange(of: phase) { old, new in if new == .active && old != .active { returns += 1 } }
            .task(id: ReturnKey(key: key, returns: returns)) { await load() }
    }

    private struct ReturnKey: Equatable { let key: K; let returns: Int }
}

extension View {
    func reloadOnReturn<K: Equatable>(_ key: K, _ load: @escaping () async -> Void) -> some View {
        modifier(ReloadOnReturn(key: key, load: load))
    }
}

/// Opens a service's own page (sign-in, verification, settings) in the system browser, never inside the app. On iOS a sign-in page
/// opens in an ASWebAuthenticationSession sheet (shares Safari's cookies); it returns when the user closes it, and the panel reloads.
@MainActor
enum SystemBrowser {
    #if os(iOS)
    private static var session: ASWebAuthenticationSession?   // held while the sheet is open (a session that is let go closes)
    private static let anchor = Anchor()

    private final class Anchor: NSObject, ASWebAuthenticationPresentationContextProviding {
        func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
            let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            return scenes.flatMap(\.windows).first { $0.isKeyWindow } ?? ASPresentationAnchor()
        }
    }
    #endif

    /// A sign-in page: false when nothing could open it.
    static func signIn(_ urlText: String, openURL: OpenURLAction) async -> Bool {
        guard let url = URL(string: urlText), url.scheme == "https" || url.scheme == "http" else { return false }
        #if os(iOS)
        return await withCheckedContinuation { cont in
            // the service sends the user back to the server's own page, not to the app: the sheet is closed by the user
            let s = ASWebAuthenticationSession(url: url, callbackURLScheme: "chat.kelo.app") { _, _ in
                session = nil
                cont.resume(returning: true)
            }
            s.presentationContextProvider = anchor
            s.prefersEphemeralWebBrowserSession = false
            session = s
            if !s.start() { session = nil; cont.resume(returning: false) }
        }
        #else
        openURL(url)
        return true
        #endif
    }

    /// Any other page (a site, a verification page, settings) in the default browser.
    static func open(_ urlText: String, openURL: OpenURLAction) -> Bool {
        guard let url = URL(string: urlText), url.scheme == "https" || url.scheme == "http" else { return false }
        openURL(url)
        return true
    }
}

extension View {
    /// "Disconnect this account?": asks about the chosen item, and `remove` runs for it when the answer is yes.
    func removeAlert<T>(_ item: Binding<T?>, title: @escaping (T) -> String, remove: @escaping (T) -> Void) -> some View {
        alert(item.wrappedValue.map(title) ?? "", isPresented: Binding(get: { item.wrappedValue != nil }, set: { if !$0 { item.wrappedValue = nil } })) {
            Button(L(R.string.connector_disconnect), role: .destructive) {
                guard let a = item.wrappedValue else { return }
                remove(a)
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }
}
