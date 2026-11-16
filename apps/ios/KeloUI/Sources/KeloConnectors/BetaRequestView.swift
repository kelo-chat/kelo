import SwiftUI
import KeloCore
import KeloShared

/// Under the "Google sign-in is getting ready" note of Gmail / Google Calendar (Android connectors/GoogleBeta.kt): ask to be let in
/// early. The address goes to this server, which forwards it to the Kelo developer and keeps its state, so "Requested" survives
/// reopening; once approved, `onOpened` reloads the screen and the normal Google sign-in shows.
struct BetaRequestView: View {
    let c: ConnectorInfo
    let onOpened: () -> Void
    @State private var model: BetaRequestModel
    @State private var sheet = false

    init(c: ConnectorInfo, api: KeloApi, onOpened: @escaping () -> Void) {
        self.c = c; self.onOpened = onOpened
        _model = State(initialValue: BetaRequestModel(api: api))
    }

    var body: some View {
        Group {
            if let product = BetaRules.product(c.id) {
                switch BetaRules.shown(model.state) {
                case .request:
                    PrimaryButton(L(R.string.beta_request)) { sheet = true }
                        .sheet(isPresented: $sheet) {
                            BetaSheet(product: product, model: model) { sheet = false }
                                .presentationDetents([.medium, .large])
                        }
                case .requested:
                    VStack(alignment: .leading, spacing: 6) {
                        SecondaryButton(L(R.string.beta_requested), enabled: false, fill: true) {}
                        MutedText(L(R.string.beta_sent, model.state?.email ?? ""), small: true)
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
            await model.load()
        }
    }
}

private struct BetaSheet: View {
    let product: String
    let model: BetaRequestModel
    let onSent: () -> Void
    @Environment(\.kelo) private var c
    @State private var email = ""
    @State private var tried = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(L(R.string.beta_request)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                MutedText(L(R.string.beta_sheet_note), small: true)
                KeloTextField(L(R.string.beta_email_hint), text: $email, keyboard: .email, maxLength: 254) { send() }
                if tried && BetaRules.email(email) == nil { ErrorText(L(R.string.beta_email_invalid), small: true) }
                if !model.err.isEmpty { ErrorText(model.err, small: true) }
                PrimaryButton(L(model.busy ? R.string.beta_sending : R.string.beta_send), enabled: !model.busy && !email.isEmpty, busy: model.busy) { send() }
            }
            .padding(16)
        }
        .background(c.bg)
    }

    private func send() {
        tried = true
        guard let valid = BetaRules.email(email), !model.busy else { return }
        let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? ""
        model.send(email: valid, product: product, appVersion: version, sent: onSent)
    }
}
