import SwiftUI
import KeloCore
import KeloShared
import KeloMy

/// The whole app (Android start/AppRoot.kt): connect → (loading / failed) → onboarding (no agent name yet) → the
/// signed-in shell. The Xcode app's @main puts this in its WindowGroup.
public struct KeloRootView: View {
    @State private var app: AppModel
    @State private var incomingLink: String?
    @Environment(\.scenePhase) private var phase

    public init(app: AppModel) { _app = State(initialValue: app) }

    public var body: some View {
        RootGate(incomingLink: $incomingLink)
            .environment(app)
            .keloTheme(app.strings)
            .id(app.strings.code)   // a language pick redraws every screen in it (and flips the layout for Arabic)
            .onOpenURL { url in
                let raw = url.absoluteString
                if PairingLinks.parse(raw) != nil {
                    if app.api == nil { incomingLink = raw }
                } else if let target = OpenTarget.decode(url.host.map { h in h + (url.path.isEmpty ? "" : ":" + url.path.dropFirst()) } ?? "") {
                    app.router.open(target)
                }
            }
            .overlay { if phase != .active { PrivacyCover() } }
    }
}

/// Covers the app in the app switcher (iOS has no FLAG_SECURE): chats and the keychain never show in the snapshot.
private struct PrivacyCover: View {
    @Environment(\.kelo) private var c
    var body: some View {
        ZStack {
            c.bg
            KeloCharacterView(.idle).frame(width: 96, height: 96)
        }
        .ignoresSafeArea()
    }
}

private struct RootGate: View {
    @Binding var incomingLink: String?
    @Environment(AppModel.self) private var app

    var body: some View {
        if app.api == nil {
            ConnectFlow(incoming: incomingLink).id(incomingLink ?? "")
        } else {
            switch app.profileState {
            case .signedOut, .loading:
                CenterMessage(L(R.string.loading))
            case .failed:
                CenterMessage(L(R.string.connect_failed)) {
                    PrimaryButton(L(R.string.retry)) { app.retryProfile() }
                    LinkButton(L(R.string.connect_other_server)) { app.signOut() }
                }
            case .ready(let p):
                if p.agentName.trimmingCharacters(in: .whitespaces).isEmpty {
                    OnboardingScreen(initial: p)
                } else {
                    MainShell()
                }
            }
        }
    }
}

/// What the app's @main calls once at launch.
public enum KeloLaunch {
    @MainActor public static func makeApp() -> AppModel {
        let app = AppModel()
        app.wallet = KeloWalletHook.make(app: app)   // the chat's and the Keychain's fill-in cards use the wallet's forms and key
        KeloBackground.register(app: app)
        return app
    }
}
