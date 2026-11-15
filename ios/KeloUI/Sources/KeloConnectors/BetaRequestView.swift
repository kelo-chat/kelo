import SwiftUI
import KeloCore
import KeloShared

/// Under the "Google sign-in is getting ready" note of Gmail / Google Calendar (Android connectors/GoogleBeta.kt): ask to be let in
/// early. The address goes to this server, which forwards it to the Kelo developer and keeps its state, so "Requested" survives
/// reopening; once approved, `onOpened` reloads the screen and the normal Google sign-in shows.
struct BetaRequestView: View {
    let c: ConnectorInfo
    let onOpened: () -> Void
    @Environment(AppModel.self) private var app
    @State private var state: GoogleBetaState?
    @State private var sheet = false

    var body: some View {
        Group {
            if let product = BetaRules.product(c.id) {
                switch BetaRules.shown(state) {
                case .request:
                    PrimaryButton(L(R.string.beta_request)) { sheet = true }
                        .sheet(isPresented: $sheet) {
                            BetaSheet(product: product) { got in state = got; sheet = false }
                                .presentationDetents([.medium, .large])
                        }
                case .requested:
                    VStack(alignment: .leading, spacing: 6) {
                        SecondaryButton(L(R.string.beta_requested), enabled: false, fill: true) {}
                        MutedText(L(R.string.beta_sent, state?.email ?? ""), small: true)
                    }
                case .declined:
                    MutedText(L(R.string.beta_declined), small: true)
                case .opened:
                    Color.clear.frame(height: 0).task { onOpened() }
                case .nothing:
                    EmptyView()
                }
            }
        }
        .task(id: c.id) {
            guard BetaRules.product(c.id) != nil else { return }
            state = try? await app.requireApi().googleBeta()
        }
    }
}

private struct BetaSheet: View {
    let product: String
    let onSent: (GoogleBetaState) -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var email = ""
    @State private var tried = false
    @State private var busy = false
    @State private var err = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(L(R.string.beta_request)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                MutedText(L(R.string.beta_sheet_note), small: true)
                KeloTextField(L(R.string.beta_email_hint), text: $email, keyboard: .email, maxLength: 254) { send() }
                if tried && BetaRules.email(email) == nil { ErrorText(L(R.string.beta_email_invalid), small: true) }
                if !err.isEmpty { ErrorText(err, small: true) }
                PrimaryButton(L(busy ? R.string.beta_sending : R.string.beta_send), enabled: !busy && !email.isEmpty, busy: busy) { send() }
            }
            .padding(16)
        }
        .background(c.bg)
    }

    private func send() {
        tried = true
        guard let valid = BetaRules.email(email), !busy else { return }
        busy = true; err = ""
        let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? ""
        Task {
            do {
                onSent(try await app.requireApi().requestGoogleBeta(email: valid, product: product, appVersion: version))
            } catch let e as ApiError {
                err = e.detail
            } catch {
                err = L(R.string.connect_failed)
            }
            busy = false
        }
    }
}
