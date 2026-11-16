import SwiftUI
import UniformTypeIdentifiers
import KeloCore
import KeloShared

/// 마이 → 운영자: only for the person whose server other copies of Kelo lean on (sign-in apps, push key, free addresses, received
/// reports), and only when this app is signed in with that server's own access key. Secrets go in, never come back out.
struct OperatorPage: View {
    let api: KeloApi
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        SubPage(L(R.string.operator_page_title), onBack: { dismiss() }) {
            MutedText(L(R.string.operator_page_intro), small: true)
            SectionLabel(L(R.string.operator_section_signin)).padding(.horizontal, -16)
            OAuthAppCard(api: api, provider: "github")
            OAuthAppCard(api: api, provider: "google")
            SectionLabel(L(R.string.operator_section_push)).padding(.horizontal, -16)
            FirebaseCard(api: api)
            SectionLabel(L(R.string.operator_section_addresses)).padding(.horizontal, -16)
            CloudflareCard(api: api)
            SectionLabel(L(R.string.operator_section_beta)).padding(.horizontal, -16)
            BetaRequestsCard(api: api)
            SectionLabel(L(R.string.operator_section_reports)).padding(.horizontal, -16)
            ReportsCard(api: api)
        }
        .keloSensitive()
    }
}

/// A sign-in app (GitHub / Google OAuth client) the operator's server uses for every copy.
struct OAuthAppCard: View {
    let provider: String
    @Environment(\.kelo) private var c
    @State private var model: OAuthAppModel
    @State private var cid = ""
    @State private var secret = ""

    init(api: KeloApi, provider: String) {
        self.provider = provider
        _model = State(initialValue: OAuthAppModel(api: api, provider: provider))
    }

    var body: some View {
        let github = provider == "github"
        KeloCard {
            Text(L(github ? R.string.operator_title_github : R.string.operator_title_google)).font(KeloFont.button).foregroundStyle(c.ink)
            MutedText(L(R.string.operator_note), small: true)
            if let a = model.oauth {
                if !a.callback.isEmpty {
                    Text(emphasized(L(github ? R.string.operator_callback_github : R.string.operator_callback_google), a.callback, code: true))
                        .font(KeloFont.small).foregroundStyle(c.ink).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                KeloTextField(L(R.string.client_id), text: $cid, keyboard: .ascii, maxLength: 100)
                let hint = L(github ? R.string.operator_secret_github : R.string.operator_secret_google)
                KeloTextField(a.hasSecret ? L(R.string.operator_secret_saved, hint) : hint, text: $secret, secure: true, keyboard: .ascii, maxLength: 200)
                PrimaryButton(L(R.string.save), enabled: !cid.trimmingCharacters(in: .whitespaces).isEmpty) { model.save(clientId: cid, secret: secret) { secret = ""; cid = $0 } }
                if !model.message.isEmpty { MutedText(model.message, small: true) }
            } else {
                SkeletonRows(rows: 2)
            }
        }
        .task { if let id = await model.load() { cid = id } }
    }
}

/// The operator's push key (Firebase service account): uploaded as a file, never shown.
struct FirebaseCard: View {
    @Environment(\.kelo) private var c
    @State private var model: FirebaseModel
    @State private var confirmOff = false
    @State private var pick = false

    init(api: KeloApi) { _model = State(initialValue: FirebaseModel(api: api)) }

    var body: some View {
        let busy = model.busy
        KeloCard {
            Text(L(R.string.operator_fb_title)).font(KeloFont.button).foregroundStyle(c.ink)
            MutedText(L(R.string.operator_fb_note), small: true)
            if let c0 = model.cfg {
                if c0.enabled {
                    KeyLine(label: L(R.string.operator_fb_project), value: c0.projectId)
                    KeyLine(label: L(R.string.operator_fb_account), value: c0.clientEmail)
                } else {
                    MutedText(L(R.string.operator_fb_off), small: true)
                }
                PrimaryButton(busy ? L(R.string.operator_fb_checking) : (c0.enabled ? L(R.string.operator_fb_replace) : L(R.string.operator_fb_upload)),
                              enabled: !busy) { pick = true }
                if c0.enabled { LinkButton(L(R.string.operator_fb_remove), danger: true) { confirmOff = true } }
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
            if !model.message.isEmpty { MutedText(model.message, small: true) }
            if !model.error.isEmpty { ErrorText(model.error, small: true) }
        }
        .task { await model.load() }
        .fileImporter(isPresented: $pick, allowedContentTypes: [.json, .plainText, .data]) { result in
            if case .success(let url) = result { model.upload(url) }
        }
        .keloConfirm(L(R.string.operator_fb_remove_confirm), isPresented: $confirmOff, yes: L(R.string.operator_fb_remove)) { model.remove() }
    }
}

/// The free-address service's settings (Cloudflare): the API token goes in, never comes back.
struct CloudflareCard: View {
    @Environment(\.kelo) private var c
    @State private var model: CloudflareModel
    @State private var confirmOff = false

    init(api: KeloApi) { _model = State(initialValue: CloudflareModel(api: api)) }

    /// The form's fields edit the model's form.
    private var form: Binding<CloudflareForm> { Binding(get: { model.form }, set: { model.setForm($0) }) }

    var body: some View {
        let busy = model.busy
        KeloCard {
            Text(L(R.string.operator_cf_title)).font(KeloFont.button).foregroundStyle(c.ink)
            MutedText(L(R.string.operator_cf_note), small: true)
            if let c0 = model.cfg {
                KeyLine(label: L(R.string.operator_cf_state), value: L(c0.enabled ? R.string.operator_cf_on : R.string.operator_cf_off))
                KeyLine(label: L(R.string.operator_cf_used), value: L(R.string.operator_cf_used_value, c0.used, c0.maxNames))
                field(L(R.string.operator_cf_token)) {
                    KeloTextField(c0.hasToken ? L(R.string.operator_cf_token_saved) : L(R.string.operator_cf_token), text: form.token, secure: true, keyboard: .ascii, maxLength: 200)
                }
                field(L(R.string.operator_cf_account)) { KeloTextField(L(R.string.operator_cf_account), text: form.accountId, keyboard: .ascii, maxLength: 32) }
                field(L(R.string.operator_cf_zone_id)) { KeloTextField(L(R.string.operator_cf_zone_id), text: form.zoneId, keyboard: .ascii, maxLength: 32) }
                field(L(R.string.operator_cf_zone)) { KeloTextField(L(R.string.operator_cf_zone), text: form.zoneName, keyboard: .url, maxLength: 253) }
                field(L(R.string.operator_cf_max)) { KeloTextField(L(R.string.operator_cf_max), text: form.maxNames, keyboard: .number, maxLength: 6) }
                PrimaryButton(busy ? L(R.string.operator_cf_checking) : L(R.string.save), enabled: !busy) { model.save() }
                SecondaryButton(L(R.string.operator_cf_reclaim), enabled: c0.enabled && !busy, fill: true) { model.reclaim() }
                MutedText(P(R.plurals.operator_cf_idle, c0.idleDays, c0.idleDays), small: true)
                if c0.hasToken { LinkButton(L(R.string.operator_cf_turn_off), danger: true) { confirmOff = true } }
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }
            if !model.message.isEmpty { MutedText(model.message, small: true) }
            if !model.error.isEmpty { ErrorText(model.error, small: true) }
        }
        .onChange(of: model.form.maxNames) { _, v in let d = v.filter(\.isNumber); if d != v { var f = model.form; f.maxNames = d; model.setForm(f) } }
        .task { await model.load() }
        .keloConfirm(L(R.string.operator_cf_off_confirm), isPresented: $confirmOff, yes: L(R.string.operator_cf_turn_off)) { model.turnOff() }
    }

    private func field<F: View>(_ label: String, @ViewBuilder _ f: () -> F) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(KeloFont.small).foregroundStyle(c.muted)
            f()
        }
    }
}

/// 운영자 → 받은 신고: what users reported from inside the app, newest first; mark handled or delete.
struct ReportsCard: View {
    @Environment(\.kelo) private var c
    @State private var model: ReportsModel
    @State private var deleting: Int64?

    init(api: KeloApi) { _model = State(initialValue: ReportsModel(api: api)) }

    var body: some View {
        KeloCard {
            MutedText(L(R.string.operator_reports_note), small: true)
            if let d = model.data {
                if !d.receiving { MutedText(L(R.string.operator_reports_not_receiving), small: true) }
                Text(L(R.string.operator_reports_open, d.open)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                if d.reports.isEmpty { MutedText(L(R.string.operator_reports_empty), small: true) }
                ForEach(d.reports) { r in row(r) }
                LinkButton(model.showHandled ? L(R.string.operator_reports_hide_handled) : L(R.string.operator_reports_show_handled)) { model.toggleHandled() }
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
            if !model.error.isEmpty { ErrorText(model.error, small: true) }
        }
        .task(id: model.showHandled) { await model.load() }
        .alert(L(R.string.operator_reports_delete_confirm), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button(L(R.string.operator_reports_delete), role: .destructive) {
                if let id = deleting { model.delete(id) }
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
                    model.mark(r)
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
}

/// 운영자 → 베타 신청: people asking to try Google sign-in while the Google app is in Testing mode; Approved or Decline (Android
/// my/OperatorBeta.kt BetaRequestsCard).
struct BetaRequestsCard: View {
    @Environment(\.kelo) private var c
    @State private var model: BetaRequestsModel
    @State private var declining: BetaRequestRow?
    @State private var declineMail = false
    @State private var deleting: Int64?

    init(api: KeloApi) { _model = State(initialValue: BetaRequestsModel(api: api)) }

    var body: some View {
        KeloCard {
            MutedText(L(R.string.operator_beta_note), small: true)
            if let d = model.data {
                if !d.receiving { MutedText(L(R.string.operator_beta_not_receiving), small: true) }
                if !d.mailFrom.isEmpty { MutedText(L(R.string.operator_beta_mail_from, d.mailFrom), small: true) }
                else { Text(L(R.string.operator_beta_no_mail)).font(KeloFont.small).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true) }
                Text(L(R.string.operator_beta_open, d.open)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                if d.requests.isEmpty { MutedText(L(R.string.operator_beta_empty), small: true) }
                ForEach(d.requests) { r in row(r) }
                if !model.message.isEmpty { MutedText(model.message, small: true) }
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 2)
            }
            if !model.error.isEmpty { ErrorText(model.error, small: true) }
        }
        .task { await model.load() }
        .sheet(item: $declining) { r in declineSheet(r) }
        .alert(L(R.string.operator_reports_delete_confirm), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button(L(R.string.operator_reports_delete), role: .destructive) {
                if let id = deleting { model.delete(id) }
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
        let tester = r.kind == "play_tester"
        let line = tester ? [OperatorBetaRules.products(r.products), r.appVersion, MyFormat.dayTime(r.createdAt)].filter { !$0.isEmpty }.joined(separator: " · ")
            : OperatorBetaRules.products(r.products) + " · " + MyFormat.dayTime(r.createdAt)
        return VStack(alignment: .leading, spacing: 6) {
            Text(r.email).font(KeloFont.titleSmall).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            Text(line).font(KeloFont.small).foregroundStyle(c.muted)
                .fixedSize(horizontal: false, vertical: true)
            Text(stateText).font(KeloFont.small).foregroundStyle(stateColor)
            switch tester ? "" : r.notice {
            case "no_mailbox": MutedText(L(R.string.operator_beta_notice_no_mail), small: true)
            case "failed": Text(L(R.string.operator_beta_notice_failed)).font(KeloFont.small).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true)
            default: EmptyView()
            }
            if OperatorBetaRules.canApprove(r) {
                Text(L(tester ? R.string.operator_tester_steps : R.string.operator_beta_steps, r.email)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                PrimaryButton(L(tester ? R.string.operator_tester_added : R.string.operator_beta_approve)) { model.answer(r, action: "approve", mail: true) }
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
            PrimaryButton(L(R.string.operator_beta_decline)) { let m = declineMail; declining = nil; model.answer(r, action: "decline", mail: m) }
            SecondaryButton(L(R.string.cancel), fill: true) { declining = nil }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(c.bg.ignoresSafeArea())
        .presentationDetents([.medium, .large])
    }
}
