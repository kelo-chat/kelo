import SwiftUI
import KeloCore
import KeloShared

/// What one connected connector lets Kelo do (GET/PUT/DELETE /api/connectors/{id}/permissions).
@MainActor @Observable
final class PermissionsModel {
    private(set) var permissions: ConnectorPermissions?
    private(set) var missing = false
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi) { self.api = api }

    private func path(_ id: String) -> String { "/api/connectors/\(KeloApi.segment(id))/permissions" }

    func load(_ id: String) async {
        do {
            let p: ConnectorPermissions = try await api.get(path(id))
            if !Task.isCancelled { permissions = p; missing = false }
        } catch let e as ApiError {
            if e.status == 404 { permissions = nil; missing = true }
        } catch {}
    }

    func setKind(_ id: String, _ kind: String, _ mode: String) { put(id, PermissionRules.kindBody(kind, mode)) }
    func setAction(_ id: String, _ a: PermissionAction, _ mode: String) { put(id, PermissionRules.actionBody(a, mode)) }

    private func put(_ id: String, _ body: [String: JSON]) {
        run { [api, path = path(id)] in try await api.put(path, body: .object(body)) }
    }

    func reset(_ id: String) {
        run { [api, path = path(id)] in try await api.delete(path) }
    }

    private func run(_ call: @escaping () async throws -> ConnectorPermissions) {
        busy = true
        Task { [self] in
            if let p = try? await call() { permissions = p }
            busy = false
        }
    }
}

@MainActor private func modeText(_ m: String) -> String {
    switch m {
    case "allow": return L(R.string.perm_allow)
    case "ask": return L(R.string.perm_ask)
    default: return L(R.string.perm_off)
    }
}

@MainActor private func kindText(_ k: String) -> String {
    switch k {
    case "read": return L(R.string.perm_kind_read)
    case "change": return L(R.string.perm_kind_change)
    case "send": return L(R.string.perm_kind_send)
    default: return L(R.string.perm_kind_delete)
    }
}

struct PermissionsSection: View {
    let id: String
    @State private var model: PermissionsModel
    @State private var confirmReset = false
    @Environment(\.kelo) private var c

    init(id: String, api: KeloApi) {
        self.id = id
        _model = State(initialValue: PermissionsModel(api: api))
    }

    var body: some View {
        Group {
            if let p = model.permissions, !model.missing, !p.kinds.isEmpty {
                DetailSection(title: L(R.string.perm_title)) {
                    VStack(alignment: .leading, spacing: 14) {
                        MutedText(L(R.string.perm_about), small: true)
                        ForEach(p.kinds, id: \.kind) { k in
                            PermissionKindRow(k: k, enabled: !model.busy,
                                              onKind: { model.setKind(id, k.kind, $0) }, onAction: { model.setAction(id, $0, $1) })
                        }
                        if PermissionRules.changed(p) { LinkButton(L(R.string.perm_reset)) { confirmReset = true } }
                    }
                }
                .keloConfirm(L(R.string.perm_reset_q), isPresented: $confirmReset, yes: L(R.string.perm_reset), danger: false) { model.reset(id) }
            }
        }
        .task(id: id) { await model.load(id) }
    }
}

private struct PermissionKindRow: View {
    let k: PermissionKind
    let enabled: Bool
    let onKind: (String) -> Void
    let onAction: (PermissionAction, String) -> Void
    @State private var open = false
    @State private var confirmOff = false
    @Environment(\.kelo) private var c

    var body: some View {
        let words = PermissionRules.modes.map(modeText)
        VStack(alignment: .leading, spacing: 6) {
            Text(kindText(k.kind)).font(KeloFont.bodyLarge.weight(.semibold)).foregroundStyle(c.ink)
            Segmented(options: words, selected: PermissionRules.modes.firstIndex(of: k.mode) ?? -1) { i in
                let m = PermissionRules.modes[i]
                guard enabled, m != k.mode else { return }
                if k.kind == "read" && m == "off" { confirmOff = true } else { onKind(m) }
            }
            let def = modeText(k.default)
            MutedText(k.mode == k.default ? L(R.string.perm_default_is, def) : L(R.string.perm_changed_from, def), small: true)
            if !k.actions.isEmpty {
                LinkButton(L(R.string.perm_actions_more, k.actions.count)) { open.toggle() }
                if open {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(k.actions, id: \.id) { a in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack(spacing: 6) {
                                    if a.mode != k.mode { Circle().fill(c.accent).frame(width: 7, height: 7) }
                                    Text(a.label.isEmpty ? a.id : a.label).font(KeloFont.body).foregroundStyle(c.ink)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                Segmented(options: words, selected: PermissionRules.modes.firstIndex(of: a.mode) ?? -1) { i in
                                    let m = PermissionRules.modes[i]
                                    if enabled && m != a.mode { onAction(a, m) }
                                }
                            }
                        }
                    }
                }
            }
        }
        .keloConfirm(L(R.string.perm_read_off_q), isPresented: $confirmOff, yes: L(R.string.perm_off), danger: false) { onKind("off") }
    }
}

/// A small muted chip in the connectors list: something differs from the defaults.
struct LimitedChip: View {
    @Environment(\.kelo) private var c
    var body: some View {
        Text(L(R.string.perm_limited)).font(KeloFont.caption).foregroundStyle(c.neutralInk).lineLimit(1)
            .padding(.horizontal, 9).padding(.vertical, 3).background(Capsule().fill(c.neutralSoft))
    }
}
