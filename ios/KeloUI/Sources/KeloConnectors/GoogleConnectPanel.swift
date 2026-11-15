import SwiftUI
import KeloCore
import KeloShared

/// Google Calendar (Android connectors/GoogleConnect.kt): the Google sign-in first (only calendar permissions are asked), each
/// connected account with its own disconnect, and the service account kept as the other way (`fallback`). While new Google sign-ins
/// wait for Google's review there is no sign-in button: the note, and this iPhone's own calendar as the other way (마이 → 권한).
struct GoogleConnectPanel: View {
    let c: ConnectorInfo
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var state: GoogleCalendarState?
    @State private var failed = false
    @State private var err = ""
    @State private var busy = false
    @State private var removing: GoogleCalendarAccount?
    @State private var other = false

    var body: some View {
        Group {
            if let s = state { content(s) }
            else if failed { ErrorText(L(R.string.connect_failed), small: true) }
            else { SkeletonRows(rows: 2) }
        }
        .reloadOnReturn(c.id) { await load(); busy = false }
        .alert(removing.map { L(R.string.gcal_disconnect_confirm, $0.address) } ?? "", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button(L(R.string.connector_disconnect), role: .destructive) {
                guard let a = removing else { return }
                Task { try? await app.requireApi().disconnect(item: a.name); await load(); onChanged() }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func load() async {
        do {
            state = try await app.requireApi().googleCalendar(); failed = false
        } catch is CancellationError {
        } catch {
            failed = true
        }
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
                ReviewNote(c: c) { Task { await load() }; onChanged() }
                MutedText(L(R.string.google_review_phone_calendar_help), small: true)
                LinkButton(L(R.string.google_review_phone_calendar)) { app.router.openMyPage(.permissions) }
            } else if !panel.pending && s.accounts.isEmpty {
                BoldLine(text: L(R.string.gcal_title))
            }
            if let button = panel.button {
                let label = busy ? L(R.string.connector_opening) : buttonLabel(button)
                if panel.primary {
                    PrimaryButton(label, enabled: !busy) { start() }
                } else {
                    SecondaryButton(label, enabled: !busy) { start() }
                }
                if s.accounts.isEmpty { MutedText(L(R.string.gcal_help), small: true) }
            } else if panel.showsNotReady {
                MutedText(s.reason.isEmpty ? L(R.string.gcal_not_ready) : s.reason, small: true)
            }
            if !err.isEmpty { ErrorText(err, small: true) }
            if panel.showsFallback, let fb = c.fallback {
                if other || fb.connected {
                    BoldLine(text: L(R.string.gcal_other_way))
                    KeyConnectPanel(c: c.with(connect: "key", via: fb.via, item: fb.item, description: fb.blurb), onChanged: onChanged)
                } else {
                    LinkButton(L(R.string.gcal_other_way_open)) { other = true }
                }
            }
            OperatorSettings(provider: "google", onSaved: { Task { await load() } })
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
        err = ""; busy = true
        Task {
            do {
                let url = try await app.requireApi().googleWebStart(product: "calendar")
                if !(await SystemBrowser.signIn(url, openURL: openURL)) { err = L(R.string.connector_browser_failed) }
                await load(); onChanged()
            } catch { err = app.message(for: error) }
            busy = false
        }
    }
}
