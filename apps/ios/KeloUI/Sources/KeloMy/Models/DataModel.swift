import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Your data (Android my/DataViewModel.kt): the export, the import of another assistant's memories, and start over.
/// `overview` is nil until the first load; `failed`: it could not be read (a loaded page stays shown).
@MainActor @Observable final class DataModel {
    private(set) var overview: DataOverview?
    private(set) var failed = false
    private(set) var prompt = ""
    var pasted = ""
    private(set) var starting = false
    private(set) var saving = false
    /// The downloaded export, handed to the share sheet (Save to Files, AirDrop, …).
    private(set) var saved: URL?
    var typed = "" { didSet { if typed != oldValue { wrongWord = false } } }
    var signOut = false
    private(set) var resetting = false
    private(set) var wrongWord = false
    private(set) var resetDone: DataResetResult?
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let onHistoryCleared: () -> Void
    @ObservationIgnored private let pollNanos: UInt64
    @ObservationIgnored private var following = false
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, pollNanos: UInt64 = 1_500_000_000, onHistoryCleared: @escaping () -> Void = {}) {
        self.api = api; self.pollNanos = pollNanos; self.onHistoryCleared = onHistoryCleared
    }

    var export: DataExport { overview?.export ?? DataExport() }
    var importStatus: DataImport { overview?.importStatus ?? DataImport() }
    var word: String { overview?.confirmWord ?? "reset" }
    func clearNotice() { notice = nil }

    /// The page's `.task`: reads the page, then looks again every few seconds while a job runs (ends with the page).
    func load() async {
        do {
            let o = try await api.dataOverview()
            if prompt.isEmpty { prompt = (try? await api.dataImportPrompt()) ?? "" }
            overview = o; failed = false
        } catch { failed = true; return }
        await follow()
    }

    /// While the export or the import runs, ask again every few seconds; stops by itself when both are over.
    func follow() async {
        guard !following else { return }
        following = true
        defer { following = false }
        while DataRules.running(export.state) || DataRules.running(importStatus.state), !Task.isCancelled {
            try? await Task.sleep(nanoseconds: pollNanos)
            guard !Task.isCancelled else { return }
            do {
                async let e = api.dataExport()
                async let i = api.dataImport()
                let (exp, imp) = try await (e, i)
                overview = DataOverview(confirmWord: word, export: exp, importStatus: imp)
            } catch { return }   // a page that cannot reach the server stops asking; coming back loads again
        }
    }

    private func set(export: DataExport? = nil, importStatus: DataImport? = nil) {
        let o = overview ?? DataOverview()
        overview = DataOverview(confirmWord: o.confirmWord, export: export ?? o.export, importStatus: importStatus ?? o.importStatus)
    }

    func startExport() {
        guard !starting, !DataRules.running(export.state) else { return }
        starting = true
        saved = nil
        jobs.run { [self] in
            defer { starting = false }
            do { set(export: try await api.startDataExport()); await follow() }
            catch { notice = L(R.string.connect_failed) }
        }
    }

    /// The ZIP is downloaded with the token to a temporary file and offered to the share sheet.
    func saveExport() {
        let e = export
        guard DataRules.exportReady(e), !saving else { return }
        saving = true
        jobs.run { [self] in
            defer { saving = false }
            do {
                let data = try await api.download(e.download)
                let url = FileManager.default.temporaryDirectory.appendingPathComponent(DataRules.saveName(e))
                try data.write(to: url, options: .atomic)
                saved = url
            } catch { notice = L(R.string.connect_failed) }
        }
    }

    /// The old assistant's answer goes to the server to be read in the background.
    func addPasted() {
        let text = pasted.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !DataRules.running(importStatus.state) else { return }
        jobs.run { [self] in
            do { set(importStatus: try await api.importDataText(text)); pasted = ""; await follow() }
            catch let e as ApiError where e.status != 0 { set(importStatus: DataImport(state: "failed", error: e.detail)) }
            catch { notice = L(R.string.connect_failed) }
        }
    }

    /// The picked ZIP goes up straight from disk (`scoped`: the document picker's URL needs its security scope while it is read).
    func importZip(url: URL, scoped: Bool) {
        guard !DataRules.running(importStatus.state) else { return }
        let opened = scoped && url.startAccessingSecurityScopedResource()
        let size = ((try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize).map(Int64.init) ?? 0
        guard DataRules.zipFits(size) else {
            if opened { url.stopAccessingSecurityScopedResource() }
            notice = L(R.string.data_zip_too_big)
            return
        }
        jobs.run { [self] in
            defer { if opened { url.stopAccessingSecurityScopedResource() } }
            do { set(importStatus: try await api.importDataFile(url)); await follow() }
            catch let e as ApiError where e.status != 0 { set(importStatus: DataImport(state: "failed", error: e.detail)) }
            catch { notice = L(R.string.connect_failed) }
        }
    }

    func cancelImport() {
        jobs.run { [self] in
            do { set(importStatus: try await api.cancelDataImport()) } catch { notice = L(R.string.connect_failed) }
        }
    }

    /// Start over: the server saves a copy first, then clears; a wrong word (400) keeps the page as it is.
    func reset() {
        guard !resetting, DataRules.typedWord(typed, word) else { return }
        resetting = true; wrongWord = false
        let sentWord = typed.trimmingCharacters(in: .whitespacesAndNewlines), out = signOut
        jobs.run { [self] in
            defer { resetting = false }
            do {
                resetDone = try await api.resetData(word: sentWord, signOut: out)
                onHistoryCleared()   // the kept chat is emptied and the screens drop what they hold
                typed = ""; signOut = false
                await load()
            } catch let e as ApiError where e.status == 400 { wrongWord = true }
            catch { notice = L(R.string.connect_failed) }
        }
    }
}
