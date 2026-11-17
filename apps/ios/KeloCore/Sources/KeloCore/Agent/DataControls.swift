import Foundation

/// GET /api/data/export: the background export (Android my/DataModels.kt). `state`: idle, running, done, failed. `download` is the
/// library path of the ZIP once it is done.
public struct DataExport: Equatable, Sendable {
    public var state: String
    public var bytes: Int64
    public var error: String
    public var file: String
    public var download: String

    public init(state: String = "idle", bytes: Int64 = 0, error: String = "", file: String = "", download: String = "") {
        self.state = state; self.bytes = bytes; self.error = error; self.file = file; self.download = download
    }

    public static func parse(_ e: JSON) -> DataExport {
        let o = Lenient(e)
        return DataExport(state: o.str("state", "idle"), bytes: o.int64("bytes"), error: o.str("error"), file: o.str("file"), download: o.str("download"))
    }
}

/// One thing the import added to memory.
public struct ImportedFact: Equatable, Sendable, Identifiable {
    public var id: String
    public var text: String
    public var kind: String
    public init(id: String = "", text: String = "", kind: String = "") { self.id = id; self.text = text; self.kind = kind }
}

/// GET /api/data/import: the background import. `state`: idle, running, done, failed, cancelled. `total` counts the pieces of text read.
public struct DataImport: Equatable, Sendable {
    public var state: String
    public var assistant: String
    public var total: Int
    public var done: Int
    public var failed: Int
    public var skipped: Int
    public var added: [ImportedFact]
    public var error: String

    public init(state: String = "idle", assistant: String = "", total: Int = 0, done: Int = 0, failed: Int = 0, skipped: Int = 0,
                added: [ImportedFact] = [], error: String = "") {
        self.state = state; self.assistant = assistant; self.total = total; self.done = done; self.failed = failed
        self.skipped = skipped; self.added = added; self.error = error
    }

    public static func parse(_ e: JSON) -> DataImport {
        let o = Lenient(e)
        let added = (o["added"]?.arrayValue ?? []).compactMap { f -> ImportedFact? in
            guard f.objectValue != nil else { return nil }
            let x = Lenient(f)
            return ImportedFact(id: x.str("id"), text: x.str("text"), kind: x.str("kind"))
        }
        return DataImport(state: o.str("state", "idle"), assistant: o.str("assistant"), total: o.int("total"), done: o.int("done"),
                          failed: o.int("failed"), skipped: o.int("skipped"), added: added, error: o.str("error"))
    }
}

/// GET /api/data: both statuses and the word the owner types before a reset.
public struct DataOverview: Equatable, Sendable {
    public var confirmWord: String
    public var export: DataExport
    public var importStatus: DataImport

    public init(confirmWord: String = "reset", export: DataExport = DataExport(), importStatus: DataImport = DataImport()) {
        self.confirmWord = confirmWord; self.export = export; self.importStatus = importStatus
    }

    public static func parse(_ e: JSON) -> DataOverview {
        let o = Lenient(e)
        return DataOverview(confirmWord: o.str("confirm_word", "reset"), export: DataExport.parse(e["export"] ?? .null),
                            importStatus: DataImport.parse(e["import"] ?? .null))
    }
}

/// POST /api/data/reset: where the copy was saved first.
public struct DataResetResult: Equatable, Sendable {
    public var name: String
    public var path: String
    public var bytes: Int64
    public var signedOut: Int

    public static func parse(_ e: JSON) -> DataResetResult {
        let o = Lenient(e)
        let b = o.obj("backup")
        return DataResetResult(name: b.str("name"), path: b.str("path"), bytes: b.int64("bytes"), signedOut: o.int("signed_out"))
    }
}

/// What the data page decides, away from the screen so it can be tested.
public enum DataRules {
    public static let zipType = "application/zip"
    /// The biggest ZIP the server reads (100 MB); a bigger one is not sent.
    public static let maxZip: Int64 = 100 * 1024 * 1024

    public static func running(_ state: String) -> Bool { state == "running" }

    /// The export file can be saved: done, and the server named the download.
    public static func exportReady(_ e: DataExport) -> Bool { e.state == "done" && !e.download.isEmpty }

    /// The word was typed (any case, spaces around it ignored), as the server compares it.
    public static func typedWord(_ typed: String, _ word: String) -> Bool {
        let w = word.trimmingCharacters(in: .whitespacesAndNewlines)
        return !w.isEmpty && typed.trimmingCharacters(in: .whitespacesAndNewlines).caseInsensitiveCompare(w) == .orderedSame
    }

    /// 0...100 for the import's progress bar; 0 until the first piece is counted.
    public static func percent(_ i: DataImport) -> Int { i.total <= 0 ? 0 : max(0, min(100, i.done * 100 / i.total)) }

    /// The import finished with something to show (done, even when nothing new was found).
    public static func importFinished(_ i: DataImport) -> Bool { i.state == "done" || i.state == "cancelled" }

    /// A name for the saved file, from the library name.
    public static func saveName(_ e: DataExport) -> String { e.file.isEmpty ? "kelo-data.zip" : e.file }

    public static func zipFits(_ size: Int64) -> Bool { size <= maxZip }
}

public extension KeloApi {
    func dataOverview() async throws -> DataOverview { DataOverview.parse(try await request("GET", "/api/data")) }
    func startDataExport() async throws -> DataExport { DataExport.parse(try await request("POST", "/api/data/export")) }
    func dataExport() async throws -> DataExport { DataExport.parse(try await request("GET", "/api/data/export")) }
    func dataImportPrompt() async throws -> String { try await request("GET", "/api/data/import/prompt")["prompt"]?.text ?? "" }

    func importDataText(_ text: String, assistant: String = "") async throws -> DataImport {
        DataImport.parse(try await request("POST", "/api/data/import/text", body: ["text": .string(text), "assistant": .string(assistant)]))
    }

    /// The ZIP goes up as one request, straight from disk.
    func importDataFile(_ url: URL) async throws -> DataImport {
        var r = try makeRequest("/api/data/import/file", method: "PUT")
        r.setValue(DataRules.zipType, forHTTPHeaderField: "Content-Type")
        return DataImport.parse(try KeloApi.readJson(await upload(r, body: .file(url))))
    }

    func dataImport() async throws -> DataImport { DataImport.parse(try await request("GET", "/api/data/import")) }
    func cancelDataImport() async throws -> DataImport { DataImport.parse(try await request("DELETE", "/api/data/import")) }

    func resetData(word: String, signOut: Bool) async throws -> DataResetResult {
        DataResetResult.parse(try await request("POST", "/api/data/reset", body: ["confirm": .string(word), "sign_out_connectors": .bool(signOut)]))
    }
}
