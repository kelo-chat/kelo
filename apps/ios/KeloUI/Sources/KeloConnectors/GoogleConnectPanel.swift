import SwiftUI
import KeloCore
import KeloShared

/// Google Calendar (Android connectors/GoogleConnect.kt): the Google sign-in first (only calendar permissions are asked), each
/// connected account with its own disconnect, and the service account kept as the other way (`fallback`). While new Google sign-ins
/// wait for Google's review there is no sign-in button: the note, and this iPhone's own calendar as the other way (마이 → 권한).
struct GoogleConnectPanel: View {
    let c: ConnectorInfo
    let api: KeloApi
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @Environment(\.forgetReporter) private var forgetReporter
    @State private var model: GoogleConnectModel
    @State private var removing: GoogleCalendarAccount?
    @State private var other = false

    init(c: ConnectorInfo, api: KeloApi, onChanged: @escaping () -> Void) {
        self.c = c; self.api = api; self.onChanged = onChanged
        _model = State(initialValue: GoogleConnectModel(api: api))
    }

    var body: some View {
        Group {
            if let s = model.state { content(s) }
            else if model.failed { ErrorText(L(R.string.connect_failed), small: true) }
            else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.id) { await model.returned() }
        .removeAlert($removing, title: { L(R.string.gcal_disconnect_confirm, $0.address) }) { model.remove($0, changed: onChanged, report: forgetReporter) }
    }

    @ViewBuilder private func content(_ s: GoogleCalendarState) -> some View {
        let panel = GooglePanel(c, state: s)
        VStack(alignment: .leading, spacing: 10) {
            ForEach(s.accounts) { a in
                VStack(alignment: .leading, spacing: 4) {
                    ShieldTitle(text: a.address)
                    MutedText(L(a.problem ? R.string.gcal_account_problem : (a.canWrite ? R.string.gcal_account_note : R.string.gcal_account_read_only)),
                              small: true)
                    LinkButton(L(R.string.connector_disconnect), danger: true) { removing = a }
                }
            }
            if panel.showsReviewNote {
                ReviewNote(c: c, api: api) { Task { await model.load() }; onChanged() }
                MutedText(L(R.string.google_review_phone_calendar_help), small: true)
                LinkButton(L(R.string.google_review_phone_calendar)) { app.router.openMyPage(.permissions) }
            } else if !panel.pending && s.accounts.isEmpty {
                BoldLine(text: L(R.string.gcal_title))
            }
            if let button = panel.button {
                let label = model.busy ? L(R.string.connector_opening) : buttonLabel(button)
                if panel.primary {
                    PrimaryButton(label, enabled: !model.busy) { start() }
                } else {
                    SecondaryButton(label, enabled: !model.busy) { start() }
                }
                if s.accounts.isEmpty { MutedText(L(R.string.gcal_help), small: true) }
            } else if panel.showsNotReady {
                MutedText(s.reason.isEmpty ? L(R.string.gcal_not_ready) : s.reason, small: true)
            }
            if !model.err.isEmpty { ErrorText(model.err, small: true) }
            if panel.showsFallback, let fb = c.fallback {
                if other || fb.connected {
                    BoldLine(text: L(R.string.gcal_other_way))
                    KeyConnectPanel(c: c.with(connect: "key", via: fb.via, item: fb.item, description: fb.blurb), api: api, onChanged: onChanged)
                } else {
                    LinkButton(L(R.string.gcal_other_way_open)) { other = true }
                }
            }
            OperatorSettings(provider: "google", api: api, onSaved: { Task { await model.load() } })
        }
    }

    private func buttonLabel(_ b: GooglePanel.Button) -> String {
        switch b {
        case .signIn: L(R.string.gcal_sign_in)
        case .signInAgain: L(R.string.gcal_sign_in_again)
        case .addAccount: L(R.string.gcal_add_account)
        }
    }

    private func start() {
        model.start(browser: { await SystemBrowser.signIn($0, openURL: openURL) }, changed: onChanged)
    }
}
