import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Kelo 서버 자원 → 정리하기 (Android my/CleanupPage.kt): what can safely be removed, grouped with sizes and dates. Nothing is
/// chosen at first; the user ticks items (or a whole group), presses 지우기 and confirms. Only then does the server delete, and only items
/// on its own current list (notes, memory and the chat history are never on it). The result line says how much room was freed.
struct CleanupPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var list: CleanupList?
    @State private var failed: StringKey?
    @State private var attempt = 0
    @State private var selected: Set<String> = []
    @State private var busy = false
    @State private var confirm = false
    @State private var result: CleanupResult?
    @State private var error: StringKey?

    var body: some View {
        SubPage(L(R.string.storage_cleanup_title), onBack: { dismiss() }) {
            if let l = list {
                content(l)
            } else if let why = failed {
                ErrorText(L(why))
                if why != R.string.storage_owner_only { SecondaryButton(L(R.string.retry)) { attempt += 1 } }
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task(id: attempt) {
            failed = nil
            do {
                let l = try await app.requireApi().cleanupList()
                list = l
                selected = StorageRules.keep(selected, l)
            } catch is CancellationError {
                return
            } catch {
                failed = (error as? ApiError)?.status == 403 ? R.string.storage_owner_only : R.string.storage_load_failed
            }
        }
        .alert(L(R.string.storage_confirm_title), isPresented: $confirm) {
            Button(L(R.string.storage_delete_button), role: .destructive) { delete() }
            Button(L(R.string.cancel), role: .cancel) {}
        } message: {
            Text(L(R.string.storage_confirm_body, MyFormat.storageSize(StorageRules.selectedBytes(list, selected))))
        }
    }

    @ViewBuilder private func content(_ l: CleanupList) -> some View {
        if let r = result {
            VStack(alignment: .leading, spacing: 4) {
                Text(L(R.string.storage_deleted, MyFormat.storageSize(r.freed))).font(KeloFont.body).foregroundStyle(c.successInk)
                    .fixedSize(horizontal: false, vertical: true)
                if !r.skipped.isEmpty {
                    Text(L(R.string.storage_skipped)).font(KeloFont.small).foregroundStyle(c.successInk).fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(c.successSoft, in: RoundedRectangle(cornerRadius: 12))
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("kelo.cleanup.result")
        }
        MutedText(L(R.string.storage_cleanup_intro), small: true)
        let groups = StorageRules.shown(l)
        if groups.isEmpty {
            MyCard { MutedText(L(R.string.storage_nothing)) }
        } else {
            ForEach(groups, id: \.id) { g in
                MyCard {
                    checkRow(checked: StorageRules.groupChecked(selected, g), id: "kelo.cleanup.group.\(g.id)") {
                        selected = StorageRules.toggleGroup(selected, g)
                    } label: {
                        Text(groupTitle(g)).font(KeloFont.titleSmall).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                        Text(MyFormat.storageSize(g.bytes)).font(KeloFont.small).foregroundStyle(c.muted)
                    }
                    if let note = groupNote(g, days: l.olderThanDays) { MutedText(note, small: true) }
                    ForEach(g.items, id: \.id) { i in
                        KeyBox(first: false) {
                            checkRow(checked: selected.contains(i.id), id: "kelo.cleanup.item") {
                                selected = StorageRules.toggle(selected, i.id)
                            } label: {
                                // a file name never breaks mid-word: one line, shortened in the middle (a backup with no name: its kind)
                                let note = itemNote(i)
                                Text(TokenText.fit(i.name.isEmpty ? (note ?? i.id) : i.name)).font(KeloFont.body).foregroundStyle(c.ink)
                                    .lineLimit(1).truncationMode(.middle)
                                let line = L(R.string.storage_item_line, MyFormat.storageSize(i.bytes), MyFormat.date(i.date))
                                Text(note.flatMap { i.name.isEmpty ? nil : L(R.string.storage_item_line, line, $0) } ?? line)
                                    .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
            MutedText(L(R.string.storage_selected, selected.count, MyFormat.storageSize(StorageRules.selectedBytes(l, selected))), small: true)
            if let e = error { ErrorText(L(e), small: true) }
            PrimaryButton(L(R.string.storage_delete_button), enabled: !selected.isEmpty && !busy, busy: busy) { confirm = true }
                .accessibilityIdentifier("kelo.cleanup.delete")
        }
    }

    /// A checkbox and its words; the whole row toggles (44pt high at least).
    private func checkRow<Label: View>(checked: Bool, id: String, action: @escaping () -> Void, @ViewBuilder label: () -> Label) -> some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: checked ? "checkmark.square.fill" : "square").font(.system(size: 22))
                    .foregroundStyle(checked ? c.accent : c.muted)
                VStack(alignment: .leading, spacing: 2) { label() }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(busy)
        .accessibilityAddTraits(checked ? .isSelected : [])
        .accessibilityIdentifier(id)
    }

    private func groupTitle(_ g: CleanupGroup) -> String {
        switch g.id {
        case "uploads": L(R.string.storage_group_uploads)
        case "images": L(R.string.storage_group_images)
        case "browser_history": L(R.string.storage_group_browser)
        case "results": L(R.string.storage_group_results)
        case "backups": L(R.string.storage_group_backups)
        default: g.name.isEmpty ? g.id : g.name
        }
    }

    private func groupNote(_ g: CleanupGroup, days: Int) -> String? {
        switch g.id {
        case "uploads": L(R.string.storage_group_uploads_desc, days)
        case "images": L(R.string.storage_group_images_desc)
        case "browser_history": L(R.string.storage_group_browser_desc)
        case "results": L(R.string.storage_group_results_desc, days)
        case "backups": L(R.string.storage_group_backups_desc)
        default: nil
        }
    }

    private func itemNote(_ i: CleanupItem) -> String? {
        if i.keepsText { return L(R.string.storage_item_text_kept) }
        if i.trashed { return L(R.string.storage_item_trashed) }
        switch i.kind {
        case "update": return L(R.string.storage_backup_update)
        case "memory": return L(R.string.storage_backup_memory)
        case "migration": return L(R.string.storage_backup_migration)
        default: return nil
        }
    }

    private func delete() {
        let ids = Array(selected)
        busy = true
        error = nil
        Task {
            defer { busy = false }
            do {
                let r = try await app.requireApi().cleanup(ids)
                result = r
                let fresh = CleanupList(groups: r.groups, total: r.total, olderThanDays: list?.olderThanDays ?? 14)
                list = fresh
                selected = StorageRules.keep(selected.subtracting(r.removed), fresh)
            } catch {
                self.error = R.string.storage_delete_failed
            }
        }
    }
}

/// The warning box on the resources page: red for 95% and a nearly full server disk, quieter for 80%; the biggest part; 정리하기 for
/// the owner.
struct StorageWarningBox: View {
    let state: StorageState
    let res: Resources
    let partName: (String) -> String
    let onCleanup: (() -> Void)?
    @Environment(\.kelo) private var c

    var body: some View {
        let urgent = StorageRules.urgent(state)
        let percent = res.alert.percent ?? 0
        VStack(alignment: .leading, spacing: 8) {
            Text(state == .hostLow ? L(R.string.storage_host_low, MyFormat.storageSize(res.host.diskFree ?? 0))
                 : state == .critical ? L(R.string.storage_critical, percent) : L(R.string.storage_warn, percent))
                .font(KeloFont.body).foregroundStyle(urgent ? c.dangerInk : c.warn).fixedSize(horizontal: false, vertical: true)
            if let k = res.alert.top, let n = res.disk.parts[k] {
                Text(L(R.string.storage_top, partName(k), MyFormat.storageSize(n)))
                    .font(KeloFont.small).foregroundStyle(urgent ? c.dangerInk : c.neutralInk).fixedSize(horizontal: false, vertical: true)
            }
            if let onCleanup { PrimaryButton(L(R.string.storage_clean_up), action: onCleanup).accessibilityIdentifier("kelo.resources.cleanup") }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(urgent ? c.dangerSoft : c.neutralSoft, in: RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.resources.warning")
    }
}
