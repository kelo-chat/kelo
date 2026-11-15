import SwiftUI
import KeloCore
import KeloShared

/// Every phone or computer that can open this assistant. Add one with a QR code (made here from the link the server returns);
/// remove one without touching the others.
struct DevicesPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: DevicesModel
    @State private var copied = false
    @State private var removing: Device?
    @State private var scanning = false
    @State private var verifyNote = ""

    init(api: KeloApi, onThisDeviceRemoved: @escaping () -> Void) {
        _model = State(initialValue: DevicesModel(api: api, onThisDeviceRemoved: onThisDeviceRemoved))
    }

    var body: some View {
        let devices = model.devices, current = model.current
        SubPage(L(R.string.devices_title), onBack: { dismiss() }) {
            encryptionCard
            MyCard {
                MutedText(L(R.string.devices_intro), small: true)
                if devices.isEmpty { MutedText(L(R.string.devices_empty), small: true) }
                ForEach(Array(devices.enumerated()), id: \.element.id) { i, d in
                    KeyBox(first: i == 0) {
                        Text(TokenText.fit(d.id == current ? L(R.string.devices_this_one, d.name) : d.name))
                            .font(KeloFont.button).foregroundStyle(c.ink).lineLimit(2)
                        if d.owner { KeloTag(L(R.string.devices_owner_badge)) }   // given on the server machine: may open the operator settings
                        MutedText(L(R.string.devices_meta, MyFormat.date(d.createdAt), MyFormat.agoText(d.lastSeen)), small: true)
                        LinkButton(L(R.string.my_disconnect_button), danger: true) { removing = d }
                    }
                }
                if let p = model.pair {
                    KeyBox(first: devices.isEmpty) { pairingBox(p.link) }
                    if !p.link.hasPrefix("https://") && !isLocal(p.link) { ErrorText(L(R.string.devices_insecure), small: true) }
                } else {
                    SecondaryButton(L(R.string.devices_add), fill: true) { copied = false; model.add() }
                }
                if !model.error.isEmpty { ErrorText(model.error, small: true) }
            }
        }
        .task { await model.load() }
        .task(id: model.pairLink) { await model.followPairing() }
        .keloFullScreen(isPresented: $scanning) {
            QrScanScreen(accept: { PairingLinks.parse($0) != nil }, onScanned: { raw in
                scanning = false
                verify(raw)
            }, onClose: { scanning = false })
            .keloTheme()
        }
        .alert(removing.map { $0.id == current ? L(R.string.my_disconnect_confirm) : L(R.string.devices_remove_confirm, $0.name) } ?? "",
               isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button(L(R.string.my_disconnect_button), role: .destructive) { if let d = removing { model.remove(d) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    /// KE2E for this phone (spec §1.3): the server key's fingerprint, whether it was verified (a scanned QR, or compared with
    /// `assistant phone fingerprint`), and verifying it by scanning a connection QR once.
    @ViewBuilder private var encryptionCard: some View {
        if let pin = app.e2ePin {
            MyCard {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    KeloIconView(.lock, size: 16, tint: c.accent)
                    Text(L(R.string.devices_e2e_title)).font(KeloFont.button).foregroundStyle(c.ink)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    KeloTag(L(pin.verified ? R.string.devices_e2e_verified : R.string.devices_e2e_not_verified))
                }
                MutedText(L(R.string.devices_e2e_on), small: true)
                MutedText(L(R.string.devices_e2e_fingerprint), small: true)
                Text(pin.key.fingerprint).font(.system(.callout, design: .monospaced)).foregroundStyle(c.ink)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("kelo.devices.fingerprint")
                if !pin.verified {
                    MutedText(L(R.string.devices_e2e_not_verified_body), small: true)
                    SecondaryButton(L(R.string.devices_e2e_verify_scan), fill: true) { verifyNote = ""; scanning = true }
                }
                if !verifyNote.isEmpty { MutedText(verifyNote, small: true) }
            }
        }
    }

    private func verify(_ raw: String) {
        guard let link = PairingLinks.parse(raw) else { return }
        switch app.verifyKey(scanned: link) {
        case nil: verifyNote = L(R.string.devices_e2e_verify_no_key)
        case true?: verifyNote = L(R.string.devices_e2e_verify_done); Haptics.success()
        case false?: verifyNote = L(R.string.devices_e2e_verify_mismatch); Haptics.warning()
        }
    }

    @ViewBuilder private func pairingBox(_ link: String) -> some View {
        if let qr = QRCode.image(link) {
            Image(decorative: qr, scale: 1).interpolation(.none).resizable().scaledToFit()
                .frame(width: 220, height: 220).padding(4).background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .frame(maxWidth: .infinity)
                .accessibilityLabel(L(R.string.devices_qr_label))
        }
        MutedText(L(R.string.devices_qr_help, model.left / 60, model.left % 60), small: true)
        Text(link).font(KeloFont.small).foregroundStyle(c.muted).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { pairButtons(link) }
            VStack(alignment: .leading, spacing: 8) { pairButtons(link) }
        }
    }

    @ViewBuilder private func pairButtons(_ link: String) -> some View {
        SecondaryButton(copied ? L(R.string.devices_copied) : L(R.string.devices_copy)) { Clipboard.copy(link); copied = true }
        ShareLink(item: link, subject: Text(L(R.string.devices_share_title))) {
            Text(L(R.string.devices_share)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                .padding(.horizontal, 14).padding(.vertical, 11)
                .background(Capsule().fill(c.card)).overlay(Capsule().stroke(c.line, lineWidth: 1))
        }
        SecondaryButton(L(R.string.close)) { model.closePairing() }
    }

    private func isLocal(_ link: String) -> Bool {
        let base = link.components(separatedBy: "#").first ?? link
        guard let host = URL(string: base)?.host else { return false }
        return host == "localhost" || host == "127.0.0.1"
    }
}

/// What this assistant may use on the server machine (RAM, CPUs, storage). Where nothing was set, the ceiling is the machine itself,
/// so that is shown as numbers (never just "no limit"). Reloaded every 10 seconds while the page is in view. Until the first numbers
/// arrive it shows placeholder rows, or, when reading them failed, why with "try again" (never a blank page). A storage warning (80% /
/// 95% of the cap, or the server's own disk almost full) shows on top with what takes the most room; the owner gets 정리하기 there and
/// under the storage numbers (the cleanup screen).
struct ResourcesPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: ResourcesModel

    init(api: KeloApi) { _model = State(initialValue: ResourcesModel(api: api)) }

    private static let parts: [String: StringKey] = [
        "database": R.string.resources_part_database, "uploads": R.string.resources_part_uploads, "images": R.string.resources_part_images,
        "notes": R.string.resources_part_notes, "history": R.string.resources_part_history, "other": R.string.resources_part_other,
    ]

    var body: some View {
        SubPage(L(R.string.resources_title), onBack: { dismiss() }) {
            if let res = model.load.data {
                content(res)
            } else if model.load.showError {
                ErrorText(L(R.string.resources_load_failed))
                SecondaryButton(L(R.string.retry)) { model.retry() }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task(id: model.attempt) { await model.poll() }
    }

    @ViewBuilder private func content(_ res: Resources) -> some View {
        let ram = ResourceRules.ramCeiling(res), cpu = ResourceRules.cpuCeiling(res), disk = ResourceRules.diskCeiling(res)
        let state = StorageRules.state(res.alert)
        let clean: (() -> Void)? = res.canClean ? { MyNav(app: app).push(.cleanup) } : nil
        if state != .ok {
            StorageWarningBox(state: state, res: res, partName: { k in Self.parts[k].map { L($0) } ?? k }, onCleanup: clean)
        }
        MyCard {
            Text(profileText(res.profile, long: true) ?? res.profile).font(KeloFont.titleSmall).foregroundStyle(c.ink)
            KeyBox {
                let used = res.memory.used.map { L(R.string.resources_used, MyFormat.storageSize($0)) } ?? ""
                let ceiling = ram.map { L(res.memory.limit != nil ? R.string.resources_limit : R.string.resources_whole_computer, MyFormat.storageSize($0)) } ?? ""
                Text(L(R.string.resources_ram, used, ceiling).trimmingCharacters(in: .whitespaces)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if let u = res.memory.used, let f = ResourceRules.fraction(u, of: ram) { KeloBar(f, hot: f > 0.9) }
                hint(MyFormat.storageSize(res.minimum.memory), MyFormat.storageSize(res.recommended.memory),
                     under: res.memory.limit.map { $0 < res.minimum.memory } ?? false)
            }
            KeyBox(first: false) {
                Text(cpu == nil ? L(R.string.resources_cpu_whole)
                     : (res.cpus != nil ? L(R.string.resources_cpu_max, MyFormat.cpuCount(cpu!)) : L(R.string.resources_cpu_all, MyFormat.cpuCount(cpu!))))
                    .font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                hint(L(R.string.resources_count, MyFormat.cpuCount(res.minimum.cpus)), L(R.string.resources_count, MyFormat.cpuCount(res.recommended.cpus)),
                     under: res.cpus.map { $0 < res.minimum.cpus } ?? false)
            }
            KeyBox(first: false) {
                let ceiling = disk.map { L(res.disk.limit != nil ? R.string.resources_limit : R.string.resources_disk_with_free, MyFormat.storageSize($0)) } ?? ""
                Text(L(R.string.resources_disk, MyFormat.storageSize(res.disk.used), ceiling).trimmingCharacters(in: .whitespaces))
                    .font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                if let f = ResourceRules.fraction(res.disk.used, of: disk) { KeloBar(f, hot: f > 0.9) }
                hint(MyFormat.storageSize(res.minimum.disk), MyFormat.storageSize(res.recommended.disk),
                     under: res.disk.limit.map { $0 < res.minimum.disk } ?? false)
                let parts = res.disk.parts.filter { $0.value > 0 }.sorted { $0.key < $1.key }.map { k, n in
                    L(R.string.resources_part, Self.parts[k].map { L($0) } ?? k, MyFormat.storageSize(n))
                }
                MutedText(parts.isEmpty ? L(R.string.resources_empty) : parts.joined(separator: L(R.string.usage_group_joiner)), small: true)
                // a server from before the storage warnings sends no alert: its old 90% line still shows
                if state == .ok && ResourceRules.diskFull(res) { ErrorText(L(R.string.resources_full), small: true) }
                if let clean { SecondaryButton(L(R.string.storage_clean_up), action: clean).accessibilityIdentifier("kelo.resources.cleanupButton") }
            }
            let q = L(R.string.resources_unknown)
            Text(emphasized(L(R.string.resources_host, res.host.memory.map { MyFormat.storageSize($0) } ?? q,
                              res.host.cpus.map { MyFormat.cpuCount($0) } ?? q, res.host.diskFree.map { MyFormat.storageSize($0) } ?? q),
                            "assistant limits --choose", code: true))
                .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func hint(_ min: String, _ rec: String, under: Bool) -> some View {
        let text = L(R.string.resources_hint, min, rec)
        if under { ErrorText(L(R.string.resources_under_min, text), small: true) } else { MutedText(text, small: true) }
    }
}
