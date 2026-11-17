import SwiftUI
import UniformTypeIdentifiers
import KeloCore
import KeloShared

/// 마이 → Your data: download a copy, bring memories from another assistant, start over.
struct DataPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: DataModel
    @State private var pickZip = false
    @State private var confirm = false

    init(api: KeloApi, onHistoryCleared: @escaping () -> Void = {}) {
        _model = State(initialValue: DataModel(api: api, onHistoryCleared: onHistoryCleared))
    }

    var body: some View {
        SubPage(L(R.string.data_title), onBack: { dismiss() }) {
            MutedText(L(R.string.data_intro), small: true)
            if model.overview != nil {
                exportSection
                importSection
                resetSection
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
        .fileImporter(isPresented: $pickZip, allowedContentTypes: [.zip, .data]) { result in
            if case .success(let url) = result { model.importZip(url: url, scoped: true) }
        }
        .keloConfirm(L(R.string.data_reset_confirm), isPresented: $confirm, yes: L(R.string.data_reset_button)) { model.reset() }
        .toast(model.notice, clear: model.clearNotice)
    }

    private func title(_ text: String) -> some View {
        Text(text).font(KeloFont.bodyLarge.weight(.semibold)).foregroundStyle(c.ink).padding(.top, 8)
    }

    private var exportSection: some View {
        let e = model.export
        return VStack(alignment: .leading, spacing: 8) {
            title(L(R.string.data_export_title))
            MyCard {
                MutedText(L(R.string.data_export_body), small: true)
                if DataRules.running(e.state) {
                    ProgressView().frame(maxWidth: .infinity)
                    MutedText(L(R.string.data_export_making), small: true)
                } else if DataRules.exportReady(e) {
                    Text(L(R.string.data_export_ready, MyFormat.fileSize(e.bytes))).font(KeloFont.body).foregroundStyle(c.ink)
                    if let saved = model.saved {
                        ShareLink(item: saved) { Text(L(R.string.data_export_save)).font(KeloFont.small).foregroundStyle(c.accent) }
                    } else {
                        PrimaryButton(L(R.string.data_export_save), enabled: !model.saving, busy: model.saving) { model.saveExport() }
                    }
                    SecondaryButton(L(R.string.data_export_again), enabled: !model.starting, fill: true) { model.startExport() }
                } else {
                    if e.state == "failed" { ErrorText(L(R.string.data_export_failed), small: true) }
                    PrimaryButton(L(R.string.data_export_make), enabled: !model.starting) { model.startExport() }
                }
            }
        }
    }

    private var importSection: some View {
        let i = model.importStatus
        let running = DataRules.running(i.state)
        return VStack(alignment: .leading, spacing: 8) {
            title(L(R.string.data_import_title))
            MyCard {
                MutedText(L(R.string.data_import_private), small: true)
                Text(L(R.string.data_import_step1)).font(KeloFont.body).foregroundStyle(c.ink)
                SecondaryButton(L(R.string.data_import_copy), enabled: !model.prompt.isEmpty, fill: true) {
                    #if canImport(UIKit)
                    UIPasteboard.general.string = model.prompt
                    #endif
                }
                Text(L(R.string.data_import_step2)).font(KeloFont.body).foregroundStyle(c.ink)
                // the pasted answer of the old assistant: a long text, not one value
                KeloTextField(L(R.string.data_import_paste_hint), text: $model.pasted, axis: .vertical)
                PrimaryButton(L(R.string.data_import_add), enabled: !model.pasted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !running) { model.addPasted() }
                MutedText(L(R.string.data_import_zip_body), small: true)
                SecondaryButton(L(R.string.data_import_zip), enabled: !running, fill: true) { pickZip = true }
                progress(i)
            }
        }
    }

    @ViewBuilder private func progress(_ i: DataImport) -> some View {
        if DataRules.running(i.state) {
            if i.total > 0 { ProgressView(value: Double(DataRules.percent(i)), total: 100) } else { ProgressView().frame(maxWidth: .infinity) }
            MutedText(i.total > 0 ? L(R.string.data_import_running, i.done, i.total) : L(R.string.data_import_reading), small: true)
            SecondaryButton(L(R.string.data_import_cancel), fill: true) { model.cancelImport() }
        } else if i.state == "failed" {
            ErrorText(L(R.string.data_import_failed, i.error.isEmpty ? L(R.string.connect_failed) : i.error), small: true)
        } else if DataRules.importFinished(i) {
            if i.added.isEmpty {
                MutedText(L(R.string.data_import_none), small: true)
            } else {
                Text(L(R.string.data_import_added, i.added.count)).font(KeloFont.body.weight(.medium)).foregroundStyle(c.ink)
                ForEach(i.added) { f in Text("• \(f.text)").font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true) }
            }
        }
    }

    private var resetSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            title(L(R.string.data_reset_title))
            MyCard {
                MutedText(L(R.string.data_reset_body), small: true)
                Toggle(isOn: $model.signOut) { Text(L(R.string.data_reset_signout)).font(KeloFont.body).foregroundStyle(c.ink) }
                    .tint(c.accent)
                Text(L(R.string.data_reset_type, model.word)).font(KeloFont.body).foregroundStyle(c.ink)
                KeloTextField(model.word, text: $model.typed)
                if model.wrongWord { ErrorText(L(R.string.data_reset_wrong), small: true) }
                PrimaryButton(L(R.string.data_reset_button), enabled: DataRules.typedWord(model.typed, model.word) && !model.resetting, busy: model.resetting) { confirm = true }
                if let done = model.resetDone { MutedText(L(R.string.data_reset_done, done.name), small: true) }
            }
        }
    }
}
