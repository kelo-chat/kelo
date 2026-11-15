import Testing
import Foundation
import CryptoKit
import KeloCore
@testable import KeloShared

/// tests/fixtures/model_labels.json at the repo root: the cases the server (tests/test_model_labels.py), Android and desktop check.
/// Found by walking up from this file, so it works wherever the repository is checked out.
func modelLabelCases(file: String = #filePath) throws -> [(String, String)] {
    var dir = URL(fileURLWithPath: file).deletingLastPathComponent()
    var found: URL?
    while dir.path != "/" {
        let f = dir.appendingPathComponent("tests/fixtures/model_labels.json")
        if FileManager.default.fileExists(atPath: f.path) { found = f; break }
        dir = dir.deletingLastPathComponent()
    }
    let url = try #require(found, "tests/fixtures/model_labels.json not found above \(file)")
    let root = try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let cases = try #require(root["cases"] as? [[String]])
    return try cases.map { c in
        try #require(c.count == 2, "a case is [id, name]: \(c)")
        return (c[0], c[1])
    }
}

/// The one way of writing a model's name the server also uses (ModelNames), and the kept provider and model lists
/// (ModelCatalog, Android chat/ModelCatalogTest.kt).
@Suite struct ModelCatalogTests {
    private let agy = ModelList(provider: "antigravity-oauth", models: ["gemini-3.8-flash-medium", "claude-sonnet-4-6"],
                                labels: ["gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)", "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)"],
                                fetchedAt: 1_700_000_000)

    private func raw(_ l: ModelList) throws -> JSON { try JSON.parse(JSONEncoder().encode(l)) }

    // ---- the shared label rule

    @Test func namesAreWrittenExactlyAsTheServerWritesThem() throws {
        let cases = try modelLabelCases()
        #expect(cases.count >= 20)
        for (id, want) in cases {
            #expect(ModelNames.humanize(id) == want, "for \(id)")
        }
    }

    @Test func aDerivedNameAndTheServersLabelNeverDifferInPunctuation() {
        // the CLIs' own labels (agy models, the claude CLI's names) for ids whose words say it all
        let labels = [
            "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)", "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
            "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low)", "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
            "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)", "sonnet": "Sonnet (latest)", "claude-opus-5-5": "Claude Opus 5.5",
        ]
        for (id, label) in labels {
            #expect(ModelNames.display(id, label: nil) == label, "for \(id)")
            #expect(ModelNames.short(ModelNames.display(id, label: nil)) == ModelNames.short(label))   // the chip's form too
        }
    }

    @Test func theGptFormsAndDatedSnapshots() {
        #expect(ModelNames.humanize("gpt-4o") == "GPT-4o")
        #expect(ModelNames.humanize("gpt-oss-120b") == "GPT-OSS 120B")
        #expect(ModelNames.humanize("gpt-5.1-mini") == "GPT-5.1 Mini")
        #expect(ModelNames.humanize("GPT") == "GPT")                       // nothing after it: just the acronym
        #expect(ModelNames.humanize("SONNET") == "Sonnet (latest)")
        #expect(ModelNames.humanize("claude-sonnet-thinking") == "Claude Sonnet (Thinking)")
        #expect(ModelNames.humanize("  ") == "")
        let all = ModelNames.displayAll(["gpt-4o", "gpt-4o-2024-08-06"], labels: [:])
        #expect(all["gpt-4o"] == "GPT-4o")
        #expect(all["gpt-4o-2024-08-06"] == "GPT-4o (2024-08-06)")
        // full-width digits are not ASCII digits: no version merge
        #expect(ModelNames.humanize("m-３-５") == "M ３ ５")
    }

    // ---- what a screen shows

    @Test func neverBlank() {
        let failed = ModelList(provider: agy.provider, error: "could not")
        #expect(ModelCatalog.shown(nil, agy) == agy)                       // not answered yet: the kept list
        var stale = agy
        stale.stale = true
        #expect(ModelCatalog.shown(failed, agy) == stale)                  // failed: the kept list, marked stale
        #expect(ModelCatalog.shown(failed, nil) == failed)                 // nothing kept: the reason
        var fresh = agy
        fresh.models.append("gemini-3.8-flash-high")
        #expect(ModelCatalog.shown(fresh, agy) == fresh)                   // a good answer replaces it in place
        #expect(ModelCatalog.shown(nil as ModelList?, nil) == nil)
        // nothing known at all: the chosen model by name (a select row), never the raw id in a text field
        #expect(ModelCatalog.placeholder(provider: "claude-cli", model: "sonnet", as: ModelList.self)?.models == ["sonnet"])
        #expect(ModelCatalog.name("sonnet", fresh: nil as ModelList?, kept: nil) == "Sonnet (latest)")
        #expect(ModelCatalog.placeholder(provider: "claude-cli", model: "", as: ModelList.self) == nil)
        #expect(ModelCatalog.placeholder(provider: "", model: "sonnet", as: ModelList.self) == nil)
        #expect(ModelCatalog.slowMs == 1000)
    }

    @Test func theLabelComesFromTheFreshListThenTheKeptOneThenTheRule() {
        var unlabeled = agy
        unlabeled.labels = [:]
        #expect(ModelCatalog.name("claude-sonnet-4-6", fresh: nil, kept: agy) == "Claude Sonnet 4.6 (Thinking)")
        #expect(ModelCatalog.name("claude-sonnet-4-6", fresh: unlabeled, kept: agy) == "Claude Sonnet 4.6 (Thinking)")
        #expect(ModelCatalog.name("claude-sonnet-4-6", fresh: nil as ModelList?, kept: nil) == "Claude Sonnet 4.6")
    }

    @Test func onlyGoodListsAreKeptNewestFirstAndCapped() throws {
        var kept: [JSON] = []
        kept = ModelCatalog.merge(kept, try raw(agy))
        kept = ModelCatalog.merge(kept, try raw(ModelList(provider: agy.provider, error: "down")))   // a failure changes nothing
        #expect(kept.count == 1)
        #expect(try JSON.decode(ModelList.self, from: kept[0]) == agy)
        kept = ModelCatalog.merge(kept, try raw(ModelList(provider: "openai")))                     // an empty list is not kept
        #expect(kept.count == 1)
        for i in 0..<(ModelCatalog.maxProviders + 4) { kept = ModelCatalog.merge(kept, try raw(ModelList(provider: "p\(i)", models: ["m"]))) }
        #expect(kept.count == ModelCatalog.maxProviders)
        #expect(kept[0]["provider"]?.text == "p\(ModelCatalog.maxProviders + 3)")
        // a newer list of a kept provider replaces it and goes first
        kept = ModelCatalog.merge(kept, try raw(ModelList(provider: "p10", models: ["m2"])))
        #expect(kept.count == ModelCatalog.maxProviders)
        #expect(kept[0]["provider"]?.text == "p10")
        #expect(kept.filter { $0["provider"]?.text == "p10" }.count == 1)
    }

    // ---- kept on the phone

    @Test func listsAndProvidersSurviveARestartPerServer() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-catalog-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let key = SymmetricKey(size: .bits256)
        let a = "https://a.example.com"
        let first = ScreenCache(directory: dir, key: { key })
        first.use(server: a)
        var stale = agy
        stale.stale = true
        ModelCatalog.keepList(first, try raw(stale))
        ModelCatalog.keepList(first, try raw(ModelList(provider: "openai", error: "no key")))
        // a failed list never replaces a good one
        ModelCatalog.keepList(first, try raw(ModelList(provider: agy.provider, error: "agy models timed out")))
        ModelCatalog.keepList(first, try raw(ModelList(provider: agy.provider)))
        let providers: JSON = [["name": "antigravity-oauth", "display_name": "Antigravity", "configured": true,
                                "cli": ["installed": true, "logged_in": true, "checked_at": 1700000000.5]]]
        ModelCatalog.keepProviders(first, providers)
        ModelCatalog.keepProviders(first, .object(["detail": "oops"]))   // not a list: ignored

        let again = ScreenCache(directory: dir, key: { key })   // the next launch reads it back from the disk
        again.use(server: a)
        #expect(ModelCatalog.keptList(again, agy.provider, as: ModelList.self) == agy)   // with its labels, without the stale mark
        #expect(ModelCatalog.keptList(again, "openai", as: ModelList.self) == nil)
        #expect(ModelCatalog.keptList(again, "", as: ModelList.self) == nil)
        #expect(ModelCatalog.keptLists(again, as: ModelList.self).keys.sorted() == [agy.provider])
        #expect(ModelCatalog.keptProviders(again) == providers)

        let other = ScreenCache(directory: dir, key: { key })   // another server never sees them
        other.use(server: "https://b.example.com")
        #expect(ModelCatalog.keptList(other, agy.provider, as: ModelList.self) == nil)
        #expect(ModelCatalog.keptProviders(other) == nil)

        // signing out drops them; a write still on its way afterwards never shows again
        again.clear()
        ModelCatalog.keepList(again, try raw(agy))
        let later = ScreenCache(directory: dir, key: { key })
        later.use(server: a)
        #expect(ModelCatalog.keptList(later, agy.provider, as: ModelList.self) == nil)
        #expect(ModelCatalog.keptProviders(later) == nil)
    }

    @MainActor @Test func signingOutClearsTheKeptLists() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-catalog-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let key = SymmetricKey(size: .bits256)
        let defaults = UserDefaults(suiteName: "kelo-catalog-test-\(UUID().uuidString)")!
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")), prefs: Prefs(defaults: defaults),
                           strings: Strings(code: "en"), transport: NoNetwork(), cache: ScreenCache(directory: dir, key: { key }))
        ModelCatalog.keepList(app.cache, try raw(agy))
        #expect(ModelCatalog.keptList(app.cache, agy.provider, as: ModelList.self) == agy)
        app.signOut()
        #expect(ModelCatalog.keptList(app.cache, agy.provider, as: ModelList.self) == nil)
        let next = ScreenCache(directory: dir, key: { key })
        next.use(server: "https://kelo.test")
        #expect(ModelCatalog.keptList(next, agy.provider, as: ModelList.self) == nil)
    }
}

/// A transport that never reaches anything (the profile load at start fails quietly).
private final class NoNetwork: HTTPTransport, @unchecked Sendable {
    func send(_ request: URLRequest) async throws -> (Int, Data) { throw URLError(.notConnectedToInternet) }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { throw URLError(.notConnectedToInternet) }
}
