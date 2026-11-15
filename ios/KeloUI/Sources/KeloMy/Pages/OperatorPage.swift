import SwiftUI
import UniformTypeIdentifiers
import KeloCore
import KeloShared

/// 마이 → 운영자: only for the person whose server other copies of Kelo lean on (sign-in apps, push key, free addresses, received
/// reports), and only when this app is signed in with that server's own access key. Secrets go in, never come back out.
struct OperatorPage: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        SubPage(L(R.string.operator_page_title), onBack: { dismiss() }) {
            MutedText(L(R.string.operator_page_intro), small: true)
            SectionLabel(L(R.string.operator_section_signin)).padding(.horizontal, -16)
            OAuthAppCard(provider: "github")
            OAuthAppCard(provider: "google")
            SectionLabel(L(R.string.operator_section_push)).padding(.horizontal, -16)
            FirebaseCard()
            SectionLabel(L(R.string.operator_section_addresses)).padding(.horizontal, -16)
            CloudflareCard()
            SectionLabel(L(R.string.operator_section_beta)).padding(.horizontal, -16)
            BetaRequestsCard()
            SectionLabel(L(R.string.operator_section_reports)).padding(.horizontal, -16)
            ReportsCard()
        }
        .keloSensitive()
    }
}

/// A sign-in app (GitHub / Google OAuth client) the operator's server uses for every copy.
struct OAuthAppCard: View {
    let provider: String
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var oauth: OAuthApp?
    @State private var cid = ""
    @State private var secret = ""
    @State private var msg = ""

    var body: some View {
        let github = provider == "github"
        KeloCard {
            Text(L(github ? R.string.operator_title_github : R.string.operator_title_google)).font(KeloFont.button).foregroundStyle(c.ink)
            MutedText(L(R.string.operator_note), small: true)
            if let a = oauth {
                if !a.callback.isEmpty {
                    Text(emphasized(L(github ? R.string.operator_callback_github : R.string.operator_callback_google), a.callback, code: true))
                        .font(KeloFont.small).foregroundStyle(c.ink).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                KeloTextField(L(R.string.client_id), text: $cid, keyboard: .ascii, maxLength: 100)
                let hint = L(github ? R.string.operator_secret_github : R.string.operator_secret_google)
                KeloTextField(a.hasSecret ? L(R.string.operator_secret_saved, hint) : hint, text: $secret, secure: true, keyboard: .ascii, maxLength: 200)
                PrimaryButton(L(R.string.save), enabled: !cid.trimmingCharacters(in: .whitespaces).isEmpty) { save() }
                if !msg.isEmpty { MutedText(msg, small: true) }
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .task {
            if let a = try? await app.requireApi().get("/api/oauth-apps/\(KeloApi.segment(provider))", as: OAuthApp.self) { oauth = a; cid = a.clientId }
        }
    }

    private func save() {
        msg = ""
        Task {
            do {
                var b: [String: JSON] = ["client_id": .string(cid.trimmingCharacters(in: .whitespaces))]
                let s = secret.trimmingCharacters(in: .whitespaces)
                if !s.isEmpty { b["client_secret"] = .string(s) }
                let saved: OAuthApp = try await app.requireApi().put("/api/oauth-apps/\(KeloApi.segment(provider))", body: .object(b))
                secret = ""; oauth = saved; cid = saved.clientId
                msg = L(R.string.operator_saved)
            } catch { msg = app.message(for: error) }
        }
    }
}

/// The operator's push key (Firebase service account): uploaded as a file, never shown.
struct FirebaseCard: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var cfg: FirebaseConfig?
    @State private var failed = false
    @State private var busy = false
    @State private var msg = ""
    @State private var err = ""
    @State private var confirmOff = false
    @State private var pick = false

    var body: some View {
        KeloCard {
            Text(L(R.string.operator_fb_title)).font(KeloFont.button).foregroundStyle(c.ink)
            MutedText(L(R.string.operator_fb_note), small: true)
            if let c0 = cfg {
                if c0.enabled {
                    KeyLine(label: L(R.string.operator_fb_project), value: c0.projectId)
                    KeyLine(label: L(R.string.operator_fb_account), value: c0.clientEmail)
                } else {
                    MutedText(L(R.string.operator_fb_off), small: true)
                }
                PrimaryButton(busy ? L(R.string.operator_fb_checking) : (c0.enabled ? L(R.string.operator_fb_replace) : L(R.string.operator_fb_upload)),
                              enabled: !busy) { pick = true }
                if c0.enabled { LinkButton(L(R.string.operator_fb_remove), danger: true) { confirmOff = true } }
            } else if failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
            if !msg.isEmpty { MutedText(msg, small: true) }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .task {
            do { cfg = try await app.requireApi().get("/api/operator/firebase") } catch { failed = true }
        }
        .fileImporter(isPresented: $pick, allowedContentTypes: [.json, .plainText, .data]) { result in
            if case .success(let url) = result { upload(url) }
        }
        .keloConfirm(L(R.string.operator_fb_remove_confirm), isPresented: $confirmOff, yes: L(R.string.operator_fb_remove)) {
            Task {
                do { cfg = try await app.requireApi().delete("/api/operator/firebase"); msg = ""; err = "" }
                catch { err = app.message(for: error) }
            }
        }
    }

    private func upload(_ url: URL) {
        busy = true; err = ""; msg = ""
        Task {
            defer { busy = false }
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            guard var bytes = try? Data(contentsOf: url), OperatorRules.keyFileOk(bytes) else { err = L(R.string.operator_fb_not_key); return }
            defer { bytes.resetBytes(in: 0..<bytes.count) }
            do {
                let out = try await app.requireApi().requestBytes("PUT", "/api/operator/firebase", body: bytes)
                cfg = try KeloApi.decode(FirebaseConfig.self, out)
                msg = L(R.string.operator_fb_saved)
            } catch {
                err = app.message(for: error)   // the server's words never contain the key
            }
        }
    }
}

/// The free-address service's settings (Cloudflare): the API token goes in, never comes back.
struct CloudflareCard: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var cfg: CloudflareConfig?
    @State private var failed = false
    @State private var form = CloudflareForm()
    @State private var busy = false
    @State private var msg = ""
    @State private var err = ""
    @State private var confirmOff = false

    var body: some View {
        KeloCard {
            Text(L(R.string.operator_cf_title)).font(KeloFont.button).foregroundStyle(c.ink)
            MutedText(L(R.string.operator_cf_note), small: true)
            if let c0 = cfg {
                KeyLine(label: L(R.string.operator_cf_state), value: L(c0.enabled ? R.string.operator_cf_on : R.string.operator_cf_off))
                KeyLine(label: L(R.string.operator_cf_used), value: L(R.string.operator_cf_used_value, c0.used, c0.maxNames))
                field(L(R.string.operator_cf_token)) {
                    KeloTextField(c0.hasToken ? L(R.string.operator_cf_token_saved) : L(R.string.operator_cf_token), text: $form.token, secure: true, keyboard: .ascii, maxLength: 200)
                }
                field(L(R.string.operator_cf_account)) { KeloTextField(L(R.string.operator_cf_account), text: $form.accountId, keyboard: .ascii, maxLength: 32) }
                field(L(R.string.operator_cf_zone_id)) { KeloTextField(L(R.string.operator_cf_zone_id), text: $form.zoneId, keyboard: .ascii, maxLength: 32) }
                field(L(R.string.operator_cf_zone)) { KeloTextField(L(R.string.operator_cf_zone), text: $form.zoneName, keyboard: .url, maxLength: 253) }
                field(L(R.string.operator_cf_max)) { KeloTextField(L(R.string.operator_cf_max), text: $form.maxNames, keyboard: .number, maxLength: 6) }
                PrimaryButton(busy ? L(R.string.operator_cf_checking) : L(R.string.save), enabled: !busy) { save() }
                SecondaryButton(L(R.string.operator_cf_reclaim), enabled: c0.enabled && !busy, fill: true) { reclaim() }
                MutedText(P(R.plurals.operator_cf_idle, c0.idleDays, c0.idleDays), small: true)
                if c0.hasToken { LinkButton(L(R.string.operator_cf_turn_off), danger: true) { confirmOff = true } }
            } else if failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }
            if !msg.isEmpty { MutedText(msg, small: true) }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .onChange(of: form.maxNames) { _, v in let d = v.filter(\.isNumber); if d != v { form.maxNames = d } }
        .task {
            do { let x: CloudflareConfig = try await app.requireApi().get("/api/operator/cloudflare"); cfg = x; form = OperatorRules.formOf(x) }
            catch { failed = true }
        }
        .keloConfirm(L(R.string.operator_cf_off_confirm), isPresented: $confirmOff, yes: L(R.string.operator_cf_turn_off)) {
            Task {
                do { let x: CloudflareConfig = try await app.requireApi().put("/api/operator/cloudflare", body: OperatorRules.cloudflareOff()); cfg = x; form = OperatorRules.formOf(x) }
                catch { err = app.message(for: error) }
            }
        }
    }

    private func field<F: View>(_ label: String, @ViewBuilder _ f: () -> F) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(KeloFont.small).foregroundStyle(c.muted)
            f()
        }
    }

    private func save() {
        msg = ""; err = ""
        if let p = OperatorRules.problem(form) {
            err = switch p {
            case .token: L(R.string.operator_cf_bad_token)
            case .account: L(R.string.operator_cf_bad_account)
            case .zoneId: L(R.string.operator_cf_bad_zone_id)
            case .zone: L(R.string.operator_cf_bad_zone)
            case .max: L(R.string.operator_cf_bad_max)
            }
            return
        }
        busy = true
        Task {
            defer { busy = false }
            do {
                let out: CloudflareConfig = try await app.requireApi().put("/api/operator/cloudflare", body: OperatorRules.cloudflareBody(form))
                cfg = out; form = OperatorRules.formOf(out)   // the token field empties: it is never shown again
                msg = L(R.string.operator_saved)
            } catch { err = app.message(for: error) }
        }
    }

    private func reclaim() {
        msg = ""; err = ""; busy = true
        Task {
            defer { busy = false }
            do {
                let r: Reclaimed = try await app.requireApi().post("/api/operator/cloudflare/reclaim")
                msg = r.released.isEmpty ? L(R.string.operator_cf_reclaim_none) : P(R.plurals.operator_cf_reclaimed, r.released.count, r.released.count)
                if let x: CloudflareConfig = try? await app.requireApi().get("/api/operator/cloudflare") { cfg = x }
            } catch { err = app.message(for: error) }
        }
    }
}

/// 운영자 → 받은 신고: what users reported from inside the app, newest first; mark handled or delete.
struct ReportsCard: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var data: ReceivedReports?
    @State private var failed = false
    @State private var showHandled = false
    @State private var err = ""
    @State private var deleting: Int64?

    var body: some View {
        KeloCard {
            MutedText(L(R.string.operator_reports_note), small: true)
            if let d = data {
                if !d.receiving { MutedText(L(R.string.operator_reports_not_receiving), small: true) }
                Text(L(R.string.operator_reports_open, d.open)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                if d.reports.isEmpty { MutedText(L(R.string.operator_reports_empty), small: true) }
                ForEach(d.reports) { r in row(r) }
                LinkButton(showHandled ? L(R.string.operator_reports_hide_handled) : L(R.string.operator_reports_show_handled)) { showHandled.toggle() }
            } else if failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .task(id: showHandled) { await load() }
        .alert(L(R.string.operator_reports_delete_confirm), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button(L(R.string.operator_reports_delete), role: .destructive) {
                if let id = deleting { Task { await act { try await app.requireApi().request("DELETE", "/api/operator/reports/\(id)") } } }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func reasonWord(_ r: String) -> String {
        switch r {
        case "harmful": L(R.string.report_reason_harmful)
        case "inaccurate": L(R.string.report_reason_inaccurate)
        case "privacy": L(R.string.report_reason_privacy)
        case "other": L(R.string.report_reason_other)
        default: r
        }
    }

    private func row(_ r: ReceivedReport) -> some View {
        let unknown = L(R.string.operator_reports_unknown)
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(reasonWord(r.reason)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                Spacer(minLength: 8)
                if r.handled { Text(L(R.string.operator_reports_handled)).font(KeloFont.small).foregroundStyle(c.success) }
            }
            Text(L(R.string.operator_reports_meta, MyFormat.dayTime(r.createdAt), OperatorRules.modelName(r) ?? unknown, r.appVersion.isEmpty ? unknown : r.appVersion))
                .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            labeled(L(R.string.operator_reports_reply), r.reply)
            if !r.asked.isEmpty { labeled(L(R.string.operator_reports_asked), r.asked) }
            if !r.comment.isEmpty { labeled(L(R.string.operator_reports_comment), r.comment) }
            HStack(spacing: 10) {
                SecondaryButton(r.handled ? L(R.string.operator_reports_unmark) : L(R.string.operator_reports_mark), fill: true) {
                    Task { await act { try await app.requireApi().request("POST", "/api/operator/reports/\(r.id)", body: ["handled": .bool(!r.handled)]) } }
                }
                LinkButton(L(R.string.operator_reports_delete), danger: true) { deleting = r.id }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
    }

    private func labeled(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(KeloFont.small).foregroundStyle(c.muted)
            Text(text).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func load() async {
        do { data = try await app.requireApi().get(OperatorRules.reportsPath(showHandled: showHandled)); failed = false } catch { failed = true }
    }

    private func act(_ f: () async throws -> JSON) async {
        do { _ = try await f(); await load() } catch { err = app.message(for: error) }
    }
}

/// 운영자 → 베타 신청: people asking to try Google sign-in while the Google app is in Testing mode; Approved or Decline (Android
/// my/OperatorBeta.kt BetaRequestsCard).
struct BetaRequestsCard: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var data: BetaRequests?
    @State private var failed = false
    @State private var err = ""
    @State private var msg = ""
    @State private var declining: BetaRequestRow?
    @State private var declineMail = false
    @State private var deleting: Int64?

    var body: some View {
        KeloCard {
            MutedText(L(R.string.operator_beta_note), small: true)
            if let d = data {
                if !d.receiving { MutedText(L(R.string.operator_beta_not_receiving), small: true) }
                if !d.mailFrom.isEmpty { MutedText(L(R.string.operator_beta_mail_from, d.mailFrom), small: true) }
                else { Text(L(R.string.operator_beta_no_mail)).font(KeloFont.small).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true) }
                Text(L(R.string.operator_beta_open, d.open)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                if d.requests.isEmpty { MutedText(L(R.string.operator_beta_empty), small: true) }
                ForEach(d.requests) { r in row(r) }
                if !msg.isEmpty { MutedText(msg, small: true) }
            } else if failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        .task { await load() }
        .sheet(item: $declining) { r in declineSheet(r) }
        .alert(L(R.string.operator_reports_delete_confirm), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button(L(R.string.operator_reports_delete), role: .destructive) {
                if let id = deleting {
                    Task {
                        do { _ = try await app.requireApi().request("DELETE", "/api/operator/beta/\(id)"); await load() } catch { err = app.message(for: error) }
                    }
                }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func row(_ r: BetaRequestRow) -> some View {
        let (stateText, stateColor): (String, Color) = switch r.status {
        case "approved": (L(R.string.operator_beta_state_approved), c.success)
        case "declined": (L(R.string.operator_beta_declined), c.muted)
        default: (L(R.string.operator_beta_waiting), c.accent)
        }
        return VStack(alignment: .leading, spacing: 6) {
            Text(r.email).font(KeloFont.titleSmall).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            Text(OperatorBetaRules.products(r.products) + " · " + MyFormat.dayTime(r.createdAt)).font(KeloFont.small).foregroundStyle(c.muted)
                .fixedSize(horizontal: false, vertical: true)
            Text(stateText).font(KeloFont.small).foregroundStyle(stateColor)
            switch r.notice {
            case "no_mailbox": MutedText(L(R.string.operator_beta_notice_no_mail), small: true)
            case "failed": Text(L(R.string.operator_beta_notice_failed)).font(KeloFont.small).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true)
            default: EmptyView()
            }
            if OperatorBetaRules.canApprove(r) {
                Text(L(R.string.operator_beta_steps, r.email)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                PrimaryButton(L(R.string.operator_beta_approve)) { answer(r, action: "approve", mail: true) }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { betaActions(r) }
                VStack(alignment: .leading, spacing: 6) { betaActions(r) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
    }

    @ViewBuilder private func betaActions(_ r: BetaRequestRow) -> some View {
        if OperatorBetaRules.canDecline(r) {
            SecondaryButton(L(R.string.operator_beta_decline)) { declineMail = false; declining = r }
        }
        LinkButton(L(R.string.operator_reports_delete), danger: true) { deleting = r.id }
    }

    private func declineSheet(_ r: BetaRequestRow) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L(R.string.operator_beta_decline_confirm)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            Text(r.email).font(KeloFont.small).foregroundStyle(c.muted)
            Toggle(isOn: $declineMail) {
                Text(L(R.string.operator_beta_decline_mail)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            .tint(c.accent)
            PrimaryButton(L(R.string.operator_beta_decline)) { let m = declineMail; declining = nil; answer(r, action: "decline", mail: m) }
            SecondaryButton(L(R.string.cancel), fill: true) { declining = nil }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(c.bg.ignoresSafeArea())
        .presentationDetents([.medium, .large])
    }

    private func answer(_ r: BetaRequestRow, action: String, mail: Bool) {
        err = ""; msg = ""
        Task {
            do {
                let a: BetaAnswer = try await app.requireApi().post("/api/operator/beta/\(r.id)", body: ["action": .string(action), "email": .bool(mail)])
                msg = a.mailed ? L(R.string.operator_beta_mailed, r.email) : a.mailError
                await load()
            } catch { err = app.message(for: error) }
        }
    }

    private func load() async {
        do { data = try await app.requireApi().get("/api/operator/beta"); failed = false } catch { failed = true }
    }
}
