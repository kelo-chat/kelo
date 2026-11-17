import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private let idle = #"{"confirm_word":"reset","export":{"state":"idle"},"import":{"state":"idle"}}"#

private func served(_ t: RoutedTransport) {
    t.on("GET /api/data", idle)
    t.on("GET /api/data/import/prompt", #"{"prompt":"List everything you remember about me"}"#)
}

@MainActor @Suite struct DataModelTests {
    @Test func rulesDecideWhatTheScreenOffers() {
        #expect(DataRules.exportReady(DataExport(state: "done", download: "/api/files/library/a.zip/file")))
        #expect(!DataRules.exportReady(DataExport(state: "running")))
        #expect(!DataRules.exportReady(DataExport(state: "done")))
        #expect(DataRules.typedWord("  RESET ", "reset"))
        #expect(!DataRules.typedWord("rese", "reset") && !DataRules.typedWord("", ""))
        #expect(DataRules.percent(DataImport(total: 0)) == 0 && DataRules.percent(DataImport(total: 4, done: 2)) == 50)
        #expect(DataRules.importFinished(DataImport(state: "done")) && !DataRules.importFinished(DataImport(state: "running")))
        #expect(DataRules.saveName(DataExport()) == "kelo-data.zip")
        #expect(DataRules.zipFits(DataRules.maxZip) && !DataRules.zipFits(DataRules.maxZip + 1))
    }

    @Test func theOverviewParsesBothJobsAndTheWord() throws {
        let o = DataOverview.parse(try JSON.parse(Data(#"{"confirm_word":"reset","export":{"state":"done","file":"a.zip","download":"/x","bytes":2048},"import":{"state":"done","assistant":"Claude","total":2,"done":2,"added":[{"id":"c1","text":"Lives in Busan","kind":"fact"}]}}"#.utf8)))
        #expect(o.export.bytes == 2048 && o.export.file == "a.zip")
        #expect(o.importStatus.assistant == "Claude" && o.importStatus.added.map(\.text) == ["Lives in Busan"])
        #expect(DataOverview.parse(.null) == DataOverview())
    }

    @Test func loadReadsThePageAndThePrompt() async {
        let t = RoutedTransport(); served(t)
        let m = DataModel(api: t.api(), pollNanos: 1_000_000)
        await m.load()
        #expect(m.overview != nil && !m.failed && m.word == "reset" && m.prompt == "List everything you remember about me")
    }

    @Test func aFailedLoadIsMarked() async {
        let t = RoutedTransport(); t.on("GET /api/data", 500, "{}")
        let m = DataModel(api: t.api(), pollNanos: 1_000_000)
        await m.load()
        #expect(m.overview == nil && m.failed)
    }

    @Test func theExportRunsThenItsFileIsDownloaded() async {
        let t = RoutedTransport(); served(t)
        t.on("POST /api/data/export", #"{"state":"running"}"#)
        t.on("GET /api/data/import", #"{"state":"idle"}"#)
        t.on("GET /api/data/export") { _, n in
            (200, n < 1 ? #"{"state":"running"}"# : #"{"state":"done","file":"kelo-data-export-1.zip","download":"/api/files/library/kelo-data-export-1.zip/file","bytes":2048}"#)
        }
        t.on("GET /api/files/library/kelo-data-export-1.zip/file", "ZIPBYTES")
        let m = DataModel(api: t.api(), pollNanos: 1_000_000)
        await m.load()
        m.startExport()
        await m.jobs.settled()
        #expect(DataRules.exportReady(m.export) && m.export.bytes == 2048)
        m.saveExport()
        await m.jobs.settled()
        #expect(m.saved?.lastPathComponent == "kelo-data-export-1.zip")
        #expect((m.saved.flatMap { try? String(contentsOf: $0, encoding: .utf8) }) == "ZIPBYTES")
        #expect(t.count("GET /api/files/library/kelo-data-export-1.zip/file") == 1)
    }

    @Test func aPastedAnswerIsSentAndTheAddedFactsAreShown() async {
        let t = RoutedTransport(); served(t)
        t.on("POST /api/data/import/text", #"{"state":"running","total":2,"done":0}"#)
        t.on("GET /api/data/export", #"{"state":"idle"}"#)
        t.on("GET /api/data/import") { _, n in
            (200, n < 1 ? #"{"state":"running","total":2,"done":1}"# : #"{"state":"done","assistant":"ChatGPT","total":2,"done":2,"added":[{"id":"c1","text":"Lives in Busan","kind":"fact"}]}"#)
        }
        let m = DataModel(api: t.api(), pollNanos: 1_000_000)
        await m.load()
        m.pasted = "- Lives in Busan"
        m.addPasted()
        await m.jobs.settled()
        #expect(t.calls.first { $0.path == "/api/data/import/text" }?.body.contains("Lives in Busan") == true)
        #expect(DataRules.importFinished(m.importStatus) && m.importStatus.added.map(\.text) == ["Lives in Busan"] && m.pasted == "")
    }

    @Test func aZipGoesUpAsTheRawBodyAndARefusalIsShown() async throws {
        let t = RoutedTransport(); served(t)
        t.on("PUT /api/data/import/file") { c, _ in
            #expect(c.body == "PK-fake-zip")
            return (422, #"{"detail":"This ZIP holds no ChatGPT, Claude or Gemini chats that Kelo can read."}"#)
        }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("export-\(UUID().uuidString).zip")
        try Data("PK-fake-zip".utf8).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        let m = DataModel(api: t.api(), pollNanos: 1_000_000)
        await m.load()
        m.importZip(url: url, scoped: false)
        await m.jobs.settled()
        #expect(m.importStatus.state == "failed" && m.importStatus.error.contains("ChatGPT, Claude or Gemini"))
        #expect(t.count("PUT /api/data/import/file") == 1)
    }

    @Test func startOverNeedsTheWordSendsItAndReportsTheCopy() async {
        let t = RoutedTransport(); served(t)
        t.on("POST /api/data/reset", #"{"ok":true,"backup":{"name":"kelo-backup-before-reset-1.zip","path":"files/kelo-backup-before-reset-1.zip","bytes":99},"signed_out":1}"#)
        var cleared = 0
        let m = DataModel(api: t.api(), pollNanos: 1_000_000, onHistoryCleared: { cleared += 1 })
        await m.load()
        m.typed = "rese"
        m.reset()
        await m.jobs.settled()
        #expect(t.count("POST /api/data/reset") == 0)                                   // the word is not right: nothing is sent
        m.typed = "Reset"; m.signOut = true
        m.reset()
        await m.jobs.settled()
        let body = t.calls.first { $0.path == "/api/data/reset" }?.body ?? ""
        #expect(body.contains("\"confirm\":\"Reset\"") && body.contains("\"sign_out_connectors\":true"))
        #expect(m.resetDone?.name == "kelo-backup-before-reset-1.zip" && m.resetDone?.signedOut == 1 && cleared == 1)
        #expect(m.typed == "" && !m.signOut)
    }

    @Test func aWrongWordAnswerKeepsThePageAndSaysSo() async {
        let t = RoutedTransport(); served(t)
        t.on("POST /api/data/reset", 400, #"{"detail":"type the confirmation word to start over"}"#)
        let m = DataModel(api: t.api(), pollNanos: 1_000_000)
        await m.load()
        m.typed = "reset"
        m.reset()
        await m.jobs.settled()
        #expect(m.wrongWord && m.resetDone == nil && m.typed == "reset")
    }
}
