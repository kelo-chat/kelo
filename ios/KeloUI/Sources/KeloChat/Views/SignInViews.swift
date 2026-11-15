import SwiftUI
import KeloCore
import KeloShared

/// 로그인 for a provider reached by signing in (Claude, Antigravity, ChatGPT, Qwen, MiniMax), all on the phone (Android SignIn.kt).
/// Shown only when the server says the app can sign this provider in (`signin`) and it is not signed in now: the consent notice
/// first (when not given yet), then the provider's page in the browser and either a code to type there (the app notices by itself)
/// or a field to paste the code the page shows.
struct SignInBox: View {
    let provider: ChatProvider
    let gate: ConsentGate
    let onSignedIn: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @Environment(\.kelo) private var c
    @Environment(\.scenePhase) private var scenePhase
    @State private var s: SignInSession?
    @State private var busy = false
    @State private var err = ""
    @State private var paste = ""
    @State private var copied = false
    @State private var deadline = Date()

    private var path: String { "/api/providers/\(KeloApi.segment(provider.name))/login" }

    var body: some View {
        let name = provider.displayName.trimmingCharacters(in: .whitespaces).isEmpty ? L(R.string.signin_this_service) : provider.displayName
        VStack(alignment: .leading, spacing: 8) {
            if let cur = s {
                if SignInRules.finished(cur) {
                    MutedText(cur.account?.isEmpty == false ? L(R.string.signin_done_account, cur.account!) : L(R.string.signin_done), small: true)
                } else if SignInRules.ended(cur) {
                    ErrorText(cur.message?.isEmpty == false ? cur.message! : L(R.string.signin_ended), small: true)
                    PrimaryButton(L(R.string.signin_again), enabled: !busy, action: start)
                } else {
                    pending(cur, name: name)
                }
            } else {
                MutedText(L(R.string.signin_intro, name), small: true)
                PrimaryButton(L(busy ? R.string.signin_starting : R.string.signin_button), enabled: !busy) { gate.request(provider) { start() } }
            }
            if !err.isEmpty { ErrorText(err, small: true) }
        }
        // a code typed on the provider's page: ask every third second while it waits, and at once when the user comes back
        .task(id: s?.session) {
            guard let cur = s, cur.state == "pending", SignInRules.isDeviceCode(cur) else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                await refresh()
                if s?.state != "pending" { return }
            }
        }
        .onChange(of: scenePhase) { _, p in if p == .active { Task { await refresh() } } }
    }

    @ViewBuilder
    private func pending(_ cur: SignInSession, name: String) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { tl in
            let secs = Int64(max(0, deadline.timeIntervalSince(tl.date)).rounded())
            VStack(alignment: .leading, spacing: 8) {
                Text(L(R.string.signin_step_open, name)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                PrimaryButton(L(R.string.signin_open_browser)) {
                    // the system browser, never a page inside the app
                    if let u = URL(string: cur.url), Markdown.isWebUrl(cur.url) { openURL(u) } else { err = L(R.string.connector_browser_failed) }
                }
                if SignInRules.isDeviceCode(cur) {
                    if let code = cur.userCode, !code.isEmpty {
                        Text(L(R.string.signin_step_code)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        Text(code).font(.system(size: 26, weight: .bold, design: .monospaced)).tracking(3).foregroundStyle(c.ink)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity).padding(.vertical, 14)
                            .background(RoundedRectangle(cornerRadius: 14).fill(c.bubble))
                            .accessibilityLabel(L(R.string.device_code_label))
                            .accessibilityValue(code)
                        SecondaryButton(L(copied ? R.string.device_copied : R.string.device_copy), fill: true) { Clipboard.copy(code); copied = true }
                    }
                    MutedText(L(R.string.signin_device_wait, SignInRules.mmss(secs)), small: true)
                    LinkButton(L(R.string.signin_check_now), action: finish)
                } else {
                    Text(L(R.string.signin_step_paste)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    KeloTextField(L(R.string.signin_paste_placeholder), text: $paste, keyboard: .url, maxLength: 4096)
                    HStack(spacing: 8) {
                        SecondaryButton(L(R.string.signin_paste), fill: true) {
                            if let t = Clipboard.text()?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { paste = t; err = "" }
                        }
                        PrimaryButton(L(busy ? R.string.signin_checking : R.string.signin_finish), enabled: !busy && SignInRules.pasteReady(paste),
                                      action: finish)
                    }
                    MutedText(L(R.string.signin_paste_help, SignInRules.mmss(secs)), small: true)
                }
                LinkButton(L(R.string.cancel), action: cancel)
            }
        }
    }

    private func take(_ next: SignInSession) {
        s = next
        if next.state == "pending" { deadline = Date().addingTimeInterval(TimeInterval(next.expiresIn)) }
        if SignInRules.finished(next) { onSignedIn() }
    }

    private func fail(_ e: ApiError) {
        err = SignInRules.problem(e) == .already ? L(R.string.signin_already)
            : (SignInRules.readable(e).isEmpty ? L(R.string.signin_failed) : SignInRules.readable(e))
    }

    private func start() {
        guard !busy, let api = app.api else { return }
        busy = true; err = ""; paste = ""; copied = false
        Task {
            defer { busy = false }
            do {
                take(try await api.post("\(path)/start", body: .object([:]), as: SignInSession.self))
            } catch let e as ApiError {
                if SignInRules.problem(e) == .consent {
                    var p = provider
                    p.consent = nil
                    busy = false
                    gate.request(p) { start() }   // recorded elsewhere meanwhile, or asked again
                    return
                }
                if SignInRules.problem(e) == .already { onSignedIn() }
                fail(e)
            } catch {
                err = L(R.string.signin_failed)
            }
        }
    }

    private func finish() {
        guard let cur = s, let api = app.api else { return }
        busy = true; err = ""
        Task {
            defer { busy = false }
            do {
                take(try await api.post("\(path)/finish", body: ["session": .string(cur.session), "code": .string(paste.trimmingCharacters(in: .whitespacesAndNewlines))],
                                        as: SignInSession.self))
                paste = ""
            } catch let e as ApiError {
                if e.status == 404 || e.status == 410 { var x = cur; x.state = "expired"; s = x }
                fail(e)
            } catch {
                err = L(R.string.signin_failed)
            }
        }
    }

    private func cancel() {
        guard let cur = s else { return }
        s = nil; err = ""; paste = ""
        guard let api = app.api else { return }
        Task { _ = try? await api.request("POST", "\(path)/cancel", body: ["session": .string(cur.session)]) }
    }

    private func refresh() async {
        guard let cur = s, cur.state == "pending", SignInRules.isDeviceCode(cur), let api = app.api else { return }
        do {
            take(try await api.get("\(path)/status?session=\(KeloApi.query(cur.session))", as: SignInSession.self))
        } catch let e as ApiError {
            if e.status == 404 { var x = cur; x.state = "expired"; s = x }
        } catch {}
    }
}

/// The signed-in account of a provider and 로그아웃 with a confirm step (Android SignOut.kt).
struct SignOutBox: View {
    let name: String
    let displayName: String
    let account: String?
    let mainProvider: String
    var quietWhenMain = false
    let onResult: (SignOutRules.Outcome, SignOutResult?, String) -> Void
    @Environment(AppModel.self) private var app
    @State private var confirm = false
    @State private var busy = false

    var body: some View {
        let main = SignOutRules.isMain(name, mainProvider: mainProvider)
        VStack(alignment: .leading, spacing: 4) {
            MutedText(account?.isEmpty == false ? L(R.string.model_signin_account, account!) : L(R.string.model_signin_connected), small: true)
            if busy { MutedText(L(R.string.model_signout_busy), small: true) }
            else if main && quietWhenMain { EmptyView() }
            else if main { MutedText(L(R.string.model_signout_main), small: true) }
            else { LinkButton(L(R.string.model_signout), danger: true) { confirm = true } }
        }
        .keloConfirm(L(R.string.model_signout_confirm, displayName), isPresented: $confirm, yes: L(R.string.model_signout), onYes: signOut)
    }

    private func signOut() {
        guard let api = app.api else { return }
        busy = true
        Task {
            var result: SignOutResult?
            var failure: ApiError?
            do { result = try await api.delete("/api/providers/\(KeloApi.segment(name))/login", as: SignOutResult.self) }
            catch let e as ApiError { failure = e }
            catch { failure = ApiError(status: ApiError.noResponse, detail: "") }
            busy = false
            onResult(SignOutRules.outcome(result, failure), result, failure.map { $0.isNetwork ? "" : $0.detail } ?? "")
        }
    }
}

/// The line after a sign-out answer; the caller's page keeps it, since the box itself goes away once signed out.
struct SignOutMessage: View {
    let outcome: SignOutRules.Outcome?
    var detail = ""

    var body: some View {
        switch outcome {
        case nil: EmptyView()
        case .done?: MutedText(L(R.string.model_signout_done), small: true)
        case .doneFallbackCleared?: MutedText(L(R.string.model_signout_fallback_cleared), small: true)
        case .doneEnvLeft?: ErrorText(L(R.string.model_signout_env_left), small: true)
        case .mainModel?: ErrorText(L(R.string.model_signout_main), small: true)
        case .failed?: ErrorText(L(R.string.model_signout_failed, detail).trimmingCharacters(in: .whitespaces), small: true)
        }
    }
}

/// 신고하기: tell the Kelo developer about a reply that was offensive, harmful, wrong or exposed someone's private details. The
/// sheet says exactly what will be sent before anything is (Android ReportSheet.kt).
struct ReportSheet: View {
    let reply: String
    let asked: String?
    let draft: ReportDraft
    let onDraft: (ReportDraft) -> Void
    let onSent: () -> Void
    let onClose: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var d: ReportDraft?
    @State private var busy = false
    @State private var err = ""

    var body: some View {
        let cur = d ?? draft
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.report_title)) { onDraft(cur); onClose() }
                MutedText(L(R.string.report_intro), small: true)
                Text(L(R.string.report_reason_label)).font(KeloFont.button).foregroundStyle(c.ink)
                VStack(spacing: 0) {
                    ForEach(ReportReason.allCases, id: \.self) { r in
                        RadioRow(reasonText(r), selected: cur.reason == r) { var x = cur; x.reason = r; d = x }
                    }
                }
                KeloTextField(L(R.string.report_comment_hint), text: Binding(get: { cur.comment }, set: { var x = cur; x.comment = $0; d = x }),
                              axis: .vertical, maxLength: ReportRules.maxComment)
                if asked != nil {
                    Toggle(isOn: Binding(get: { cur.includeAsked }, set: { var x = cur; x.includeAsked = $0; d = x })) {
                        Text(L(R.string.report_include_asked)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    .tint(c.accent)
                }
                // exactly what leaves the phone, following the switch above
                VStack(alignment: .leading, spacing: 4) {
                    Text(L(R.string.report_sent_title)).font(.system(.footnote, weight: .semibold)).foregroundStyle(c.ink)
                    ForEach(sentLines(cur), id: \.self) { line in
                        Text(L(R.string.report_bullet, line)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    Text(L(R.string.report_sent_not)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
                if !err.isEmpty { ErrorText(err, small: true) }
                PrimaryButton(L(busy ? R.string.report_sending : R.string.report_send), enabled: !busy && ReportRules.canSend(cur)) { send(cur) }
            }
            .padding(.horizontal, 20).padding(.bottom, 16)
        }
        .interactiveDismissDisabled(busy)
        .onDisappear { if !busy { onDraft(d ?? draft) } }
    }

    private func reasonText(_ r: ReportReason) -> String {
        switch r {
        case .harmful: L(R.string.report_reason_harmful)
        case .inaccurate: L(R.string.report_reason_inaccurate)
        case .privacy: L(R.string.report_reason_privacy)
        case .other: L(R.string.report_reason_other)
        }
    }

    private func sentLines(_ cur: ReportDraft) -> [String] {
        var l = [L(R.string.report_sent_reply)]
        if asked != nil && cur.includeAsked { l.append(L(R.string.report_sent_asked)) }
        l += [L(R.string.report_sent_reason), L(R.string.report_sent_model), L(R.string.report_sent_version)]
        return l
    }

    private func send(_ cur: ReportDraft) {
        guard let api = app.api else { return }
        busy = true; err = ""
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        Task {
            defer { busy = false }
            do {
                _ = try await api.request("POST", "/api/reports", body: ReportRules.body(cur, reply: reply, asked: asked, appVersion: version))
                onSent()
            } catch let e as ApiError {
                err = e.isNetwork ? L(R.string.report_failed_offline) : e.detail
                onDraft(cur)   // kept: the next try starts from here
            } catch {
                err = L(R.string.report_failed_offline)
            }
        }
    }
}
