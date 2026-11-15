import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 권한: what this iPhone shares with the user's own server (each only when turned on here), the sites where the agent may act
/// without asking (each can be taken back), and every question it asked before with the answer it got. Questions still waiting are
/// answered on the work page.
struct PermissionsPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var pending: [PendingApproval]?
    @State private var rules: [ApprovalRule] = []
    @State private var history: [ApprovalRecord] = []
    @State private var next: Double?
    @State private var failed = false
    @State private var revoking: ApprovalRule?
    @State private var toast: String?

    var body: some View {
        SubPage(L(R.string.agent_tab_permissions), onBack: { dismiss() }) {
            MutedText(L(R.string.my_perm_intro), small: true)
            DeviceSection()
            if let pending {
                if !pending.isEmpty {
                    MyCard(action: { app.router.openWork(.approvals) }) {
                        Text(P(R.plurals.my_perm_waiting, pending.count, pending.count)).font(KeloFont.body).foregroundStyle(c.accent)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                SectionLabel(L(R.string.agent_perm_rules)).padding(.horizontal, -16)
                if rules.isEmpty {
                    MutedText(L(R.string.agent_perm_rules_empty), small: true)
                } else {
                    MenuCard {
                        ForEach(Array(rules.enumerated()), id: \.element.id) { i, r in
                            if i > 0 { Hairline() }
                            ruleRow(r)
                        }
                    }
                }
                SectionLabel(L(R.string.agent_perm_history)).padding(.horizontal, -16)
                if history.isEmpty {
                    EmptyNote(L(R.string.agent_perm_history_empty))
                } else {
                    MenuCard {
                        ForEach(Array(history.enumerated()), id: \.offset) { i, a in
                            if i > 0 { Hairline() }
                            recordRow(a)
                        }
                    }
                }
                if next != nil { LinkButton(L(R.string.agent_more)) { Task { await more() } } }
            } else if failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task { await load() }
        .alert(revoking.map { L(R.string.agent_rule_revoke_confirm, $0.site) } ?? "",
               isPresented: Binding(get: { revoking != nil }, set: { if !$0 { revoking = nil } })) {
            Button(L(R.string.agent_rule_revoke_yes), role: .destructive) { if let r = revoking { revoke(r) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .toast($toast)
    }

    private func ruleRow(_ r: ApprovalRule) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(TokenText.fit(r.site)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(1)
                Text(r.kind == "browser_action" ? L(R.string.agent_rule_kind_browser) : (r.kind == "login" ? L(R.string.agent_rule_kind_login)
                     : (r.kindLabel.isEmpty ? r.kind : r.kindLabel))).font(KeloFont.small).foregroundStyle(c.muted)
                Text(P(R.plurals.agent_rule_uses, r.uses, r.uses)).font(KeloFont.label).foregroundStyle(c.muted)
            }
            Spacer(minLength: 8)
            Button { revoking = r } label: { KeloIconView(.trash, size: 20, tint: c.muted).frame(width: 40, height: 40) }
                .buttonStyle(.plain)
                .accessibilityLabel(L(R.string.agent_rule_revoke, r.site))
        }
        .padding(.vertical, 10)
    }

    private func recordRow(_ a: ApprovalRecord) -> some View {
        let word: StringKey = switch a.decision {
        case "allowed_once": R.string.agent_decision_once
        case "allowed_always": R.string.agent_decision_always
        case "allowed_by_rule": R.string.agent_decision_rule
        case "denied": R.string.agent_decision_denied
        case "expired": R.string.agent_decision_expired
        case "cancelled": R.string.agent_decision_cancelled
        default: R.string.agent_decision_pending
        }
        return HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(a.label.isEmpty ? a.tool : a.label).font(KeloFont.body).foregroundStyle(c.ink).lineLimit(3)
                let sub = [a.site, a.detail].filter { !$0.isEmpty }.joined(separator: " · ")
                if !sub.isEmpty { Text(sub).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3) }
                if a.askedAt > 0 { Text(MyFormat.dayTime(a.askedAt)).font(KeloFont.label).foregroundStyle(c.muted) }
            }
            Spacer(minLength: 8)
            KeloBadge(L(word), denied: a.decision == "denied")
        }
        .padding(.vertical, 10)
    }

    private func load() async {
        do {
            let api = try app.requireApi()
            let p = try await api.pendingApprovals()
            rules = try await api.approvalRules().items
            let h = try await api.approvalHistory()
            history = h.items; next = h.nextBefore
            pending = p
        } catch {
            failed = true
        }
    }

    private func more() async {
        guard let before = next, let h = try? await app.requireApi().approvalHistory(before: before) else { return }
        history += h.items; next = h.nextBefore
    }

    private func revoke(_ r: ApprovalRule) {
        Task {
            do {
                try await app.requireApi().deleteApprovalRule(r.id)
                rules.removeAll { $0.id == r.id }
            } catch { toast = app.message(for: error) }
        }
    }
}

/// 이 기기에서: one row per thing this iPhone can share with or do for the user's own server: the calendar (read, and if allowed
/// add / change after an approval), the contacts and the phone dialer. Android's text messages, call log, notifications and Health
/// rows have no iOS equivalent and are not shown.
/// A row opens a sheet with what Kelo can do, what is sent, the switch and the last send.
struct DeviceSection: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @Environment(\.scenePhase) private var phase
    @State private var open: PhoneFeature?
    @State private var version = 0
    private var sync: PhoneDataSync { PhoneDataSync.shared }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L(R.string.device_section)).font(KeloFont.titleSmall).foregroundStyle(c.muted).padding(.leading, 4).padding(.top, 6)
                .accessibilityAddTraits(.isHeader)
            VStack(spacing: 0) {
                ForEach(Array(PhoneFeature.allCases.enumerated()), id: \.element) { i, f in
                    if i > 0 { Hairline().padding(.leading, 68).padding(.trailing, 16) }
                    let _ = version
                    let on = sync.on(f), allowed = sync.permitted(f)
                    DeviceRow(feature: f, connected: on && allowed, needsPermission: (on || sync.denied(f)) && !allowed) { open = f }
                }
            }
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        }
        .onChange(of: phase) { _, p in if p == .active { version += 1 } }   // back from iPhone Settings
        .keloSheet(item: $open, detents: [.medium, .large]) { f in
            DeviceSheet(feature: f) { open = nil; version += 1 }
        }
    }
}

extension PhoneFeature: Identifiable {
    public var id: String { rawValue }
    var icon: KeloIcon {
        switch self { case .calendar: .calendar; case .contacts: .contacts; case .dialer: .phone }
    }
    var title: StringKey {
        switch self { case .calendar: R.string.device_calendar; case .contacts: R.string.device_contacts; case .dialer: R.string.device_dialer }
    }
    var does: StringKey {
        switch self { case .calendar: R.string.device_calendar_does; case .contacts: R.string.device_contacts_does; case .dialer: R.string.device_dialer_does }
    }
    var sends: StringKey {
        switch self { case .calendar: R.string.device_calendar_sends; case .contacts: R.string.device_contacts_sends; case .dialer: R.string.device_dialer_sends }
    }
}

struct DeviceTile: View {
    let feature: PhoneFeature
    let size: CGFloat
    @Environment(\.kelo) private var c
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28).fill(c.infoSoft).frame(width: size, height: size)
            .overlay(KeloIconView(feature.icon, size: size * 0.55, tint: c.infoInk))
    }
}

struct DeviceRow: View {
    let feature: PhoneFeature
    let connected: Bool
    let needsPermission: Bool
    let onOpen: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: 12) {
                DeviceTile(feature: feature, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L(feature.title)).font(KeloFont.bodyLarge.weight(.medium)).foregroundStyle(c.ink).lineLimit(2)
                    Text(L(R.string.device_row_sub)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2)
                }
                Spacer(minLength: 8)
                let label = needsPermission ? L(R.string.device_needs_permission) : (connected ? L(R.string.device_connected) : L(R.string.device_row_connect))
                let fg = needsPermission ? c.dangerInk : (connected ? c.successInk : c.accent)
                let bg = needsPermission ? c.dangerSoft : (connected ? c.successSoft : Color.clear)
                Text(label).font(.system(.footnote, weight: .semibold)).foregroundStyle(fg).lineLimit(1)
                    .padding(.horizontal, 12).padding(.vertical, 5)
                    .background(Capsule().fill(bg))
                    .overlay(Capsule().stroke(!connected && !needsPermission ? c.accent : .clear, lineWidth: 1))
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L(feature.title))
    }
}

/// One row's sheet: what it does, what goes to the server, the switch, the last send (it keeps sending by itself), 지금 보내기.
struct DeviceSheet: View {
    let feature: PhoneFeature
    let onClose: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var on = false
    @State private var offFailed = false
    @State private var isDenied = false
    private var sync: PhoneDataSync { PhoneDataSync.shared }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(feature.title), onClose: onClose)
                HStack(alignment: .top, spacing: 12) {
                    DeviceTile(feature: feature, size: 44)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L(feature.does)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        MutedText(L(feature.sends), small: true)
                    }
                }
                ToggleRow(L(R.string.device_switch), isOn: Binding(get: { on }, set: { v in toggle(v) }))
                if isDenied {
                    ErrorText(L(R.string.device_denied), small: true)
                    SecondaryButton(L(R.string.device_open_settings), fill: true) { DeviceInfo.openAppSettings() }
                } else if on {
                    let st = sync.status[feature] ?? FeatureStatus()
                    let last = st.lastAt > 0 ? st.lastAt : sync.lastAt(feature)
                    let count = st.count > 0 ? st.count : sync.lastCount(feature)
                    HStack(alignment: .firstTextBaseline) {
                        MutedText(st.busy && last == 0 ? L(R.string.device_sending)
                                  : (last == 0 ? L(R.string.device_auto_never) : L(R.string.device_auto, MyFormat.agoText(last)) + (count > 0 ? " · \(count)" : "")),
                                  small: true)
                        Spacer(minLength: 8)
                        LinkButton(st.busy ? L(R.string.device_sending) : L(R.string.device_send_now)) {
                            Task { await sync.sync(feature, app: app, force: true) }
                        }
                        .disabled(st.busy)
                    }
                    if st.failed && !st.busy { ErrorText(L(R.string.device_send_failed), small: true) }
                    if feature == .calendar { CalendarWriteOptions() }
                }
                if offFailed { ErrorText(L(R.string.device_off_failed), small: true) }
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .onAppear {
            on = sync.on(feature) && sync.permitted(feature)
            isDenied = sync.on(feature) && sync.denied(feature)
        }
    }

    private func toggle(_ v: Bool) {
        Haptics.tick()
        offFailed = false
        Task {
            if v {
                let ok = await sync.turnOn(feature, app: app)
                on = ok
                isDenied = !ok && sync.denied(feature)
            } else {
                on = false
                isDenied = false
                offFailed = !(await sync.turnOff(feature, app: app))
            }
        }
    }
}

/// The calendar row's own options once it is on (Android DeviceUi Extras CALENDAR): "also allow adding and editing events" (each one
/// asks first on an approval card) and which calendar Kelo adds to.
struct CalendarWriteOptions: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var write = false
    @State private var pick: String?
    private var sync: PhoneDataSync { PhoneDataSync.shared }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Hairline()
            ToggleRow(L(R.string.device_cal_write), detail: L(R.string.device_cal_write_line), isOn: Binding(get: { write }, set: { v in
                Haptics.tick()
                write = v
                sync.writeAllowed = v
                Task { await sync.sync(.calendar, app: app, force: true) }
            }))
            if write {
                let cals = sync.writableCalendars()
                Text(L(R.string.device_cal_write_to)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
                if cals.isEmpty { MutedText(L(R.string.device_cal_none), small: true) }
                let chosen = cals.first { $0.calendarIdentifier == pick }?.calendarIdentifier ?? cals.first?.calendarIdentifier
                ForEach(cals, id: \.calendarIdentifier) { k in
                    let account = k.source?.title ?? ""
                    ChoiceRow(k.title + (account.isEmpty || account == k.title ? "" : " · \(account)"), selected: k.calendarIdentifier == chosen) {
                        pick = k.calendarIdentifier
                        sync.writeCalendarId = k.calendarIdentifier
                        Task { await sync.sync(.calendar, app: app, force: true) }
                    }
                }
            }
        }
        .onAppear { write = sync.writeAllowed; pick = sync.writeCalendarId }
    }
}

/// One choice of a list (a radio mark and its words).
struct ChoiceRow: View {
    let text: String
    let selected: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c
    init(_ text: String, selected: Bool, action: @escaping () -> Void) { self.text = text; self.selected = selected; self.action = action }
    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle").foregroundStyle(selected ? c.accent : c.muted)
                Text(text).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 6).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}
