import SwiftUI
import KeloCore
import KeloShared

// 마이 → 서버 업데이트 (Android my/ServerUpdatePage.kt): the owner updates the Kelo server with one button. The rules (what shows,
// following a run through the server's restart) are KeloCore's `ServerUpdateRules`; this file only draws them. Another device, an
// older server and state "unavailable" see nothing: the page closes itself and 마이 has no row or card.

/// The page's state: the last answer, and what is under way. Kept across the page's own redraws; the last answer is cached so the
/// page opens with it at once. A run is followed by `follow()`, which the visible page runs in its `.task`: it ends with the page and
/// starts again when the page opens.
@MainActor @Observable final class ServerUpdateModel {
    static let cacheSlot = "server-update"

    private(set) var status: ServerUpdateStatus?
    /// 403 / 404 / unavailable: nothing to show
    private(set) var hidden = false
    /// the last ask failed while following a run (the server restarting)
    private(set) var reconnecting = false
    /// 15 minutes without the run ending
    private(set) var gaveUp = false
    private(set) var checking = false
    private(set) var starting = false
    private(set) var savingAuto = false
    /// a refusal or failure in the server's own words
    private(set) var problem: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private var following = false
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, cache: ScreenCache) { self.api = api; self.cache = cache }

    var running: Bool { status?.isRunning == true }

    func load() async {
        if status == nil, let kept: ServerUpdateStatus = cache.get(Self.cacheSlot), ServerUpdateRules.visible(kept) { status = kept }
        switch await ServerUpdateRules.load(api) {
        case .shown(let st): show(st)
        case .hidden: hide()
        case .failed(let f): if status == nil { problem = f.isNetwork ? L(R.string.connect_failed) : f.detail }
        }
    }

    private func show(_ st: ServerUpdateStatus) {
        guard ServerUpdateRules.visible(st) else { hide(); return }
        status = st
        cache.put(Self.cacheSlot, st)
    }

    private func hide() {
        hidden = true
        status = nil
        cache.put(Self.cacheSlot, ServerUpdateStatus())
    }

    /// Follows the run the status shows until it ends (through the server's restart).
    func follow() async {
        guard !following, let st = status, st.isRunning else { return }
        following = true
        defer { following = false }
        gaveUp = false
        let out = await ServerUpdateRules.follow(api, from: st, onProgress: { p in await self.progress(p) })
        reconnecting = false
        if Task.isCancelled { return }   // the page went away: it picks the run up again when it opens
        switch out {
        case .finished(let end): show(end)
        case .gaveUp(let last): status = last; gaveUp = true
        case .refused(let f): if f.hides { hide() } else { problem = f.detail }
        }
    }

    private func progress(_ p: ServerUpdateRules.Progress) {
        if ServerUpdateRules.visible(p.status) { status = p.status }
        reconnecting = p.reconnecting
    }

    func check() {
        guard !checking else { return }
        checking = true; problem = nil
        jobs.run { [self] in
            defer { checking = false }
            do { show(try await ServerUpdateRules.check(api)) } catch { fail(error) }
        }
    }

    /// Starts the update (`rollback` false) or the way back; the page's `follow()` then picks the run up.
    func start(rollback: Bool) {
        guard !starting else { return }
        starting = true; problem = nil; gaveUp = false
        jobs.run { [self] in
            defer { starting = false }
            do {
                show(rollback ? try await ServerUpdateRules.startRollback(api) : try await ServerUpdateRules.startUpdate(api))
            } catch {
                fail(error)
                if case .shown(let st) = await ServerUpdateRules.load(api) { show(st) }   // what the refusal was about
            }
        }
    }

    func setAuto(_ on: Bool) {
        savingAuto = true; problem = nil
        jobs.run { [self] in
            defer { savingAuto = false }
            do { show(try await ServerUpdateRules.setAuto(api, on)) } catch { fail(error) }
        }
    }

    private func fail(_ error: Error) {
        let f = ServerUpdateRules.failure(error)
        if f.hides { hide(); return }
        problem = f.isNetwork || f.detail.isEmpty ? L(R.string.connect_failed) : f.detail
    }
}

// ---- words --------------------------------------------------------------------------------------------------------------------------

@MainActor enum ServerUpdateText {
    static func step(_ job: ServerUpdateJob) -> String {
        switch ServerUpdateRules.step(job) {
        case "preflight": L(R.string.server_update_step_preflight)
        case "download": L(R.string.server_update_step_download)
        case "verify": L(R.string.server_update_step_verify)
        case "backup": L(R.string.server_update_step_backup)
        case "restart": L(R.string.server_update_step_restart)
        case "health": L(R.string.server_update_step_health)
        case "rollback": L(R.string.server_update_step_rollback)
        case "self_update": L(R.string.server_update_step_self_update)
        case "done": L(R.string.server_update_step_done)
        default: L(R.string.server_update_step_working)
        }
    }

    /// The line on a running card: "Updating the server… 40%" / "Going back to the previous version… 40%".
    static func running(rollback: Bool, progress: Int) -> String {
        rollback ? L(R.string.server_update_card_going_back, progress) : L(R.string.server_update_card_running, progress)
    }

    static func card(_ card: ServerUpdateCard) -> String {
        switch card {
        case .running(let rollback, let progress, _): running(rollback: rollback, progress: progress)
        case .available(let v): L(R.string.server_update_card_available, v)
        case .failed(let rollback): rollback ? L(R.string.server_update_go_back_failed) : L(R.string.server_update_card_failed)
        case .rolledBack: L(R.string.server_update_card_rolled_back)
        case .rollbackFailed: L(R.string.server_update_card_rollback_failed)
        }
    }

    static func nonEmpty(_ s: String?) -> String? {
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        return t
    }
}

// ---- the page -------------------------------------------------------------------------------------------------------------------------

struct ServerUpdatePage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: ServerUpdateModel
    @State private var confirmUpdate = false
    @State private var confirmBack = false

    init(api: KeloApi, cache: ScreenCache) { _model = State(initialValue: ServerUpdateModel(api: api, cache: cache)) }

    var body: some View {
        SubPage(L(R.string.server_update_title), onBack: { dismiss() }) {
            if let st = model.status {
                content(st)
            } else if !model.hidden {
                if let p = model.problem { ErrorText(p, small: true) } else { SkeletonRows(rows: 3) }
            }
        }
        .task { await model.load() }
        .task(id: model.running) { await model.follow() }
        .onChange(of: model.hidden) { _, h in if h { dismiss() } }
    }

    @ViewBuilder private func content(_ st: ServerUpdateStatus) -> some View {
        let now = Date().timeIntervalSince1970
        let job = st.job
        let available = ServerUpdateRules.available(st)
        let result = ServerUpdateRules.result(st, now: now)

        // the version in use
        KeloCard {
            KeyLine(label: L(R.string.server_update_current), value: ServerUpdateRules.rowValue(st))
            if ServerUpdateRules.isLocal(st) {
                MutedText(ServerUpdateText.nonEmpty(st.message) ?? L(R.string.server_update_local_fallback), small: true)
            } else if available == nil && !st.isRunning {
                HStack(spacing: 6) {
                    KeloIconView(.check, size: 16, tint: c.success)
                    Text(L(R.string.server_update_up_to_date)).font(KeloFont.small).foregroundStyle(c.success)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }

        if !ServerUpdateRules.isLocal(st) {
            if let job, job.isRunning { progressCard(job) }
            if model.gaveUp { ErrorText(L(R.string.server_update_gave_up), small: true) }
            if let result, let job { resultCard(result, job: job) }
            if let available, !st.isRunning { availableCard(available, st: st) }
            if ServerUpdateRules.canCheck(st) { checkRow(st) }
            if let p = model.problem { ErrorText(p, small: true) }
            if ServerUpdateRules.showAutoToggle(st) { autoCard(st) }
            if let back = ServerUpdateRules.rollbackVersion(st) {
                LinkButton(L(R.string.server_update_go_back, back)) { confirmBack = true }
                    .accessibilityIdentifier("kelo.serverUpdate.goBack")
                    .alert(L(R.string.server_update_go_back_title, back), isPresented: $confirmBack) {
                        Button(L(R.string.server_update_go_back_yes), role: .destructive) { model.start(rollback: true) }
                        Button(L(R.string.cancel), role: .cancel) {}
                    } message: {
                        Text(L(R.string.server_update_go_back_body))
                    }
            }
        }
    }

    private func progressCard(_ job: ServerUpdateJob) -> some View {
        KeloCard {
            Text(ServerUpdateText.running(rollback: job.isRollback, progress: job.progress))
                .font(KeloFont.button).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            KeloBar(ServerUpdateRules.fraction(job))
                .accessibilityLabel(ServerUpdateText.step(job))
                .accessibilityValue("\(job.progress)%")
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(ServerUpdateText.step(job)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            if job.auto { KeloBadge(L(R.string.server_update_auto_tag)) }
            MutedText(L(R.string.server_update_running_note), small: true)
            if model.reconnecting {
                Text(L(R.string.server_update_waiting)).font(KeloFont.small).foregroundStyle(c.warn)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("kelo.serverUpdate.reconnecting")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.serverUpdate.progress")
    }

    @ViewBuilder private func resultCard(_ result: ServerUpdateResult, job: ServerUpdateJob) -> some View {
        let (title, lines, bad, error): (String, [String], Bool, String?) = {
            switch result {
            case .updated(let to):
                return (ServerUpdateText.nonEmpty(to).map { L(R.string.server_update_done, $0) } ?? L(R.string.server_update_done_plain), [], false, nil)
            case .wentBack(let to):
                return (ServerUpdateText.nonEmpty(to).map { L(R.string.server_update_went_back, $0) } ?? L(R.string.server_update_done_plain), [], false, nil)
            case .failed(let rollback, let e):
                return rollback ? (L(R.string.server_update_go_back_failed), [], true, e)
                                : (L(R.string.server_update_card_failed), [L(R.string.server_update_failed)], true, e)
            case .rolledBack(let from, _, let restored, let e):
                let line = ServerUpdateText.nonEmpty(from).map { L(R.string.server_update_rolled_back, $0) } ?? L(R.string.server_update_rolled_back_plain)
                return (L(R.string.server_update_card_rolled_back),
                        [line, restored ? L(R.string.server_update_data_restored) : L(R.string.server_update_data_kept)], true, e)
            case .rollbackFailed(let e):
                return (L(R.string.server_update_card_rollback_failed), [L(R.string.server_update_rollback_failed)], true, e)
            }
        }()
        KeloCard(spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                KeloIconView(bad ? .alert : .check, size: 18, tint: bad ? c.danger : c.success)
                Text(title).font(KeloFont.button).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(lines, id: \.self) { MutedText($0, small: true) }
            if let e = ServerUpdateText.nonEmpty(error) {
                Text(L(R.string.server_update_error_detail, e)).font(KeloFont.caption).foregroundStyle(c.muted)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            if job.auto { KeloBadge(L(R.string.server_update_auto_tag)) }
            if let f = job.finishedAt { Text(MyFormat.monthDayTime(f)).font(KeloFont.caption).foregroundStyle(c.muted) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.serverUpdate.result")
    }

    private func availableCard(_ r: ServerUpdateRelease, st: ServerUpdateStatus) -> some View {
        KeloCard {
            VStack(alignment: .leading, spacing: 2) {
                Text(L(R.string.server_update_new_version)).font(KeloFont.small).foregroundStyle(c.muted)
                Text(r.version).font(KeloFont.titleLarge).foregroundStyle(c.ink).lineLimit(1).minimumScaleFactor(0.7)
                if let d = r.dateSeconds {
                    Text(L(R.string.server_update_released, MyFormat.date(d))).font(KeloFont.small).foregroundStyle(c.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let notes = ServerUpdateText.nonEmpty(r.notes) {
                Text(L(R.string.server_update_whats_new)).font(KeloFont.titleSmall).foregroundStyle(c.ink).padding(.top, 4)
                Text(notes).font(KeloFont.body).foregroundStyle(c.ink).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            PrimaryButton(L(R.string.server_update_button), enabled: ServerUpdateRules.showUpdateButton(st), busy: model.starting) { confirmUpdate = true }
                .accessibilityIdentifier("kelo.serverUpdate.update")
                .padding(.top, 4)
                .alert(L(R.string.server_update_confirm_title, r.version), isPresented: $confirmUpdate) {
                    Button(L(R.string.server_update_button)) { model.start(rollback: false) }
                    Button(L(R.string.cancel), role: .cancel) {}
                } message: {
                    Text(L(R.string.server_update_confirm_body))
                }
        }
    }

    private func checkRow(_ st: ServerUpdateStatus) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                checkButton
                checkedLine(st)
                Spacer(minLength: 0)
            }
            VStack(alignment: .leading, spacing: 6) {
                checkButton
                checkedLine(st)
            }
        }
    }

    private var checkButton: some View {
        SecondaryButton(model.checking ? L(R.string.server_update_checking) : L(R.string.server_update_check_now), icon: .retry,
                        enabled: !model.checking) { model.check() }
    }

    private func checkedLine(_ st: ServerUpdateStatus) -> some View {
        Text(st.checkedAt.map { L(R.string.server_update_checked, MyFormat.agoText($0)) } ?? L(R.string.server_update_never_checked))
            .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
    }

    private func autoCard(_ st: ServerUpdateStatus) -> some View {
        KeloCard(spacing: 4) {
            ToggleRow(L(R.string.server_update_auto_title), detail: L(R.string.server_update_auto_body),
                      isOn: Binding(get: { model.status?.autoUpdate ?? false },
                                    set: { on in model.setAuto(on) }),
                      enabled: !model.savingAuto)
            if st.autoUpdate, let (h, m) = ServerUpdateRules.clock(st.autoTime) {
                Text(L(R.string.server_update_auto_tonight, MyFormat.clock(h, m))).font(KeloFont.small).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 6)
            }
        }
    }
}

// ---- on 마이 ----------------------------------------------------------------------------------------------------------------------------

/// The highlighted card at the top of 마이 (a newer version, a run under way, a recent run that went wrong); opens the page.
struct ServerUpdateCardView: View {
    let card: ServerUpdateCard
    let action: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let bad: Bool = { if case .rollbackFailed = card { return true }; if case .rolledBack = card { return true }
                          if case .failed = card { return true }; return false }()
        Button(action: action) {
            HStack(alignment: .top, spacing: 12) {
                KeloIconView(bad ? .alert : .update, size: 22, tint: bad ? c.dangerInk : c.infoInk)
                VStack(alignment: .leading, spacing: 6) {
                    Text(ServerUpdateText.card(card)).font(KeloFont.button).foregroundStyle(bad ? c.dangerInk : c.infoInk)
                        .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                    if case .running(_, let progress, _) = card { KeloBar(Double(progress) / 100) }
                    Text(L(R.string.server_update_card_open)).font(KeloFont.small).foregroundStyle(bad ? c.dangerInk : c.infoInk).opacity(0.85)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                KeloIconView(.chevronRight, size: 16, tint: bad ? c.dangerInk : c.infoInk)
            }
            .padding(16)
            .background(RoundedRectangle(cornerRadius: 16).fill(bad ? c.dangerSoft : c.infoSoft))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressShrinkStyle())
        .accessibilityIdentifier("kelo.serverUpdate.card")
    }
}
