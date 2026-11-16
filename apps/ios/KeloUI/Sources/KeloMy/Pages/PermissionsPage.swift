import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 권한: what this iPhone shares with the user's own server (each only when turned on here), the sites where the agent may act
/// without asking (each can be taken back), and every question it asked before with the answer it got. Questions still waiting are
/// answered on the work page.
struct PermissionsPage: View {
    let phoneData: PhoneDataSync
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: PermissionsModel
    @State private var revoking: ApprovalRule?

    init(api: KeloApi, phoneData: PhoneDataSync) {
        self.phoneData = phoneData
        _model = State(initialValue: PermissionsModel(api: api))
    }

    var body: some View {
        let rules = model.rules, history = model.history
        SubPage(L(R.string.agent_tab_permissions), onBack: { dismiss() }) {
            MutedText(L(R.string.my_perm_intro), small: true)
            DeviceSection(sync: phoneData, source: app)
            if let pending = model.pending {
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
                            ApprovalRecordRow(a: a, when: a.askedAt > 0 ? MyFormat.dayTime(a.askedAt) : "")
                        }
                    }
                }
                if model.next != nil { LinkButton(L(R.string.agent_more)) { model.more() } }
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task { await model.load() }
        .alert(revoking.map { L(R.string.agent_rule_revoke_confirm, $0.site) } ?? "",
               isPresented: Binding(get: { revoking != nil }, set: { if !$0 { revoking = nil } })) {
            Button(L(R.string.agent_rule_revoke_yes), role: .destructive) { if let r = revoking { model.revoke(r) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .toast(model.notice, clear: model.clearNotice)
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
}

/// 이 기기에서: one row per thing this iPhone can share with or do for the user's own server: the calendar (read, and if allowed
/// add / change after an approval), the contacts and the phone dialer. Android's text messages, call log, notifications and Health
/// rows have no iOS equivalent and are not shown.
/// A row opens a sheet with what Kelo can do, what is sent, the switch and the last send.
struct DeviceSection: View {
    let sync: PhoneDataSync
    let source: any PhoneSource
    @Environment(\.kelo) private var c
    @Environment(\.scenePhase) private var phase
    @State private var open: PhoneFeature?
    @State private var version = 0

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
            DeviceSheet(model: DeviceFeatureModel(feature: f, sync: sync, source: source)) { open = nil; version += 1 }
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
    let onClose: () -> Void
    @Environment(\.kelo) private var c
    @State private var model: DeviceFeatureModel

    init(model: DeviceFeatureModel, onClose: @escaping () -> Void) {
        _model = State(initialValue: model)
        self.onClose = onClose
    }

    var body: some View {
        let feature = model.feature, sync = model.sync
        let on = model.on, isDenied = model.isDenied
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
                ToggleRow(L(R.string.device_switch), isOn: Binding(get: { on }, set: { v in Haptics.tick(); model.toggle(v) }))
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
                            model.sendNow()
                        }
                        .disabled(st.busy)
                    }
                    if st.failed && !st.busy { ErrorText(L(R.string.device_send_failed), small: true) }
                    if feature == .calendar { CalendarWriteOptions(model: CalendarWriteModel(sync: sync, source: model.source)) }
                }
                if model.offFailed { ErrorText(L(R.string.device_off_failed), small: true) }
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .onAppear { model.refresh() }
    }
}

/// The calendar row's own options once it is on (Android DeviceUi Extras CALENDAR): "also allow adding and editing events" (each one
/// asks first on an approval card) and which calendar Kelo adds to.
struct CalendarWriteOptions: View {
    @Environment(\.kelo) private var c
    @State private var model: CalendarWriteModel

    init(model: CalendarWriteModel) { _model = State(initialValue: model) }

    var body: some View {
        let write = model.write, sync = model.sync
        VStack(alignment: .leading, spacing: 10) {
            Hairline()
            ToggleRow(L(R.string.device_cal_write), detail: L(R.string.device_cal_write_line), isOn: Binding(get: { write }, set: { v in Haptics.tick(); model.setWrite(v) }))
            if write {
                let cals = sync.writableCalendars()
                Text(L(R.string.device_cal_write_to)).font(KeloFont.titleSmall).foregroundStyle(c.muted)
                if cals.isEmpty { MutedText(L(R.string.device_cal_none), small: true) }
                let chosen = cals.first { $0.calendarIdentifier == model.pick }?.calendarIdentifier ?? cals.first?.calendarIdentifier
                ForEach(cals, id: \.calendarIdentifier) { k in
                    let account = k.source?.title ?? ""
                    ChoiceRow(k.title + (account.isEmpty || account == k.title ? "" : " · \(account)"), selected: k.calendarIdentifier == chosen) {
                        model.choose(k.calendarIdentifier)
                    }
                }
            }
        }
        .onAppear { model.refresh() }
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
