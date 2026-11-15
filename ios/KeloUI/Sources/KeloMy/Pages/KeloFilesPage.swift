import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Kelo의 파일 (Android my/KeloFilesPage.kt): the files Kelo keeps for itself. Folders and files push onto the page's own stack
/// ("dir:<path>", "file:<path>", "trash"); back pops it and leaves the page when it is empty. Writable files can be edited; core files go
/// back to the start rather than being deleted; deleted files wait 30 days in the trash.
struct KeloFilesPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: KeloFilesModel
    @State private var at: [String] = []
    @State private var leaveAsk = false
    @State private var ask: Ask?
    @State private var purgeId: String?
    @State private var newFolder = false
    @State private var folderText = ""

    private enum Ask: Identifiable { case reset, delete; var id: Int { self == .reset ? 0 : 1 } }

    init(api: KeloApi) { _model = State(initialValue: KeloFilesModel(api: api)) }

    private var top: String { at.last ?? "" }
    private var isFile: Bool { top.hasPrefix("file:") }
    private var filePath: String { String(top.dropFirst(5)) }
    private var folder: String { top.hasPrefix("dir:") ? String(top.dropFirst(4)) : "" }

    private func pop() { if at.isEmpty { dismiss() } else { at.removeLast() } }
    private func back() { if isFile && model.dirty { leaveAsk = true } else { pop() } }

    private var title: String {
        if top == "trash" { return L(R.string.files_trash_title) }
        if isFile || top.hasPrefix("dir:") { return String(top.split(separator: ":", maxSplits: 1).last ?? "").split(separator: "/").last.map(String.init) ?? "" }
        return L(R.string.files_title)
    }

    var body: some View {
        SubPage(title, onBack: back) {
            if top == "trash" { trashView }
            else if isFile { fileView }
            else { folderView }
        }
        .task { await model.load() }
        .task(id: top) {
            if isFile { await model.openFile(filePath) } else if top == "trash" { await model.loadTrash() }
        }
        .task(id: model.note) {
            guard model.note != nil else { return }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !Task.isCancelled { model.clearNote() }
        }
        .toast(model.note.map { L($0) }, clear: { model.clearNote() })
        .alert(L(R.string.files_discard_q), isPresented: $leaveAsk) {
            Button(L(R.string.files_discard), role: .destructive) { model.cancelEdit(); pop() }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .alert(L(R.string.files_new_folder), isPresented: $newFolder) {
            TextField(L(R.string.files_folder_name), text: $folderText)
            Button(L(R.string.files_make)) { let n = folderText; Task { await model.makeFolder(parent: folder, rawName: n) } }
            Button(L(R.string.files_cancel), role: .cancel) {}
        }
        .alert(ask == .reset ? L(R.string.files_reset_q) : L(R.string.files_delete_q), isPresented: Binding(get: { ask != nil }, set: { if !$0 { ask = nil } })) {
            if ask == .reset {
                Button(L(R.string.files_reset)) { let p = filePath; Task { await model.reset(p) } }
            } else {
                Button(L(R.string.files_delete), role: .destructive) { let p = filePath; Task { await model.delete(p) { pop() } } }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .alert(L(R.string.files_purge_q), isPresented: Binding(get: { purgeId != nil }, set: { if !$0 { purgeId = nil } })) {
            Button(L(R.string.files_purge), role: .destructive) { if let id = purgeId { Task { await model.purge(id) } } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func meaning(_ path: String) -> String? {
        switch path {
        case "assistant.md": L(R.string.files_core_assistant)
        case "user.md": L(R.string.files_core_user)
        case "MEMORY.md": L(R.string.files_core_memory)
        case "AGENTS.md": L(R.string.files_core_agents)
        case "CHECKLIST.md": L(R.string.files_core_checklist)
        case "NOTIFY_PREFS.md": L(R.string.files_core_notify)
        default: nil
        }
    }

    // ---- a folder

    @ViewBuilder private var folderView: some View {
        if folder.isEmpty { MutedText(L(R.string.files_about)) }
        if let all = model.entries {
            ForEach(KeloFileRules.children(all, folder: folder), id: \.path) { e in
                MenuRow(.file, e.name, value: e.isDir ? nil : (meaning(e.path) ?? e.bytes.map { MyFormat.fileSize($0) })) {
                    at.append(e.isDir ? "dir:\(e.path.hasSuffix("/") ? String(e.path.dropLast()) : e.path)" : "file:\(e.path)")
                }
            }
            if KeloFileRules.canMakeFolder(folder) {
                SecondaryButton(L(R.string.files_new_folder)) { folderText = ""; newFolder = true }
            }
            if folder.isEmpty { MenuRow(.trash, L(R.string.files_trash_title)) { at.append("trash") } }
        } else if model.failed {
            ErrorText(L(R.string.files_load_failed))
            SecondaryButton(L(R.string.retry)) { Task { await model.load() } }
        } else {
            SkeletonRows(rows: 4)
        }
    }

    // ---- a file

    private let mono = Font.system(.footnote, design: .monospaced)

    @ViewBuilder private var fileView: some View {
        if let file = model.open, file.path == filePath {
            let entry = model.entries?.first { $0.path == filePath }
            if let draft = model.draft {
                TextEditor(text: Binding(get: { draft }, set: { model.draft = $0 }))
                    .font(mono).foregroundStyle(c.ink).scrollContentBackground(.hidden)
                    .padding(8).frame(minHeight: 360)
                    .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                PrimaryButton(L(R.string.files_save), enabled: !model.busy && draft != file.content) { Task { await model.save() } }
                SecondaryButton(L(R.string.files_cancel)) { model.cancelEdit() }
            } else {
                Text(file.content).font(mono).foregroundStyle(c.ink).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if entry?.writable == true { PrimaryButton(L(R.string.files_edit)) { model.edit() } }
                else { MutedText(L(R.string.files_read_only), small: true) }
                if KeloFileRules.isCore(filePath) { SecondaryButton(L(R.string.files_reset), enabled: !model.busy) { ask = .reset } }
                if KeloFileRules.canDelete(filePath) { SecondaryButton(L(R.string.files_delete), enabled: !model.busy) { ask = .delete } }
            }
        } else {
            SkeletonRows(rows: 3)
        }
    }

    // ---- the trash

    @ViewBuilder private var trashView: some View {
        MutedText(L(R.string.files_trash_about))
        if let items = model.trash {
            if items.isEmpty { MutedText(L(R.string.files_trash_empty)) }
            ForEach(items) { t in
                VStack(alignment: .leading, spacing: 2) {
                    Text(t.path).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    MutedText(L(R.string.files_trash_left, t.daysLeft()), small: true)
                    HStack {
                        Button(L(R.string.files_restore)) { Task { await model.restore(t.id) } }.foregroundStyle(c.accent)
                        Button(L(R.string.files_purge)) { purgeId = t.id }.foregroundStyle(c.danger)
                    }
                    .font(KeloFont.body)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            SkeletonRows(rows: 3)
        }
    }
}
