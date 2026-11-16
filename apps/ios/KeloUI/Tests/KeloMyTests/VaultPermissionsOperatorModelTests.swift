import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private func vaultJSON(_ names: [String]) -> String {
    #"{"locked":false,"items":["# + names.map { #"{"name":"\#($0)"}"# }.joined(separator: ",") + "]}"
}

@MainActor @Suite struct VaultModelTests {
    @Test func loadsAndLocksThenReads() async {
        let t = RoutedTransport()
        t.on("GET /api/vault", vaultJSON(["a"]))
        t.on("PUT /api/vault/lock", "{}")
        let m = VaultModel(api: t.api())
        await m.load()
        #expect(m.item("a") != nil)
        m.setLocked(true)
        await m.jobs.settled()
        #expect(t.count("PUT /api/vault/lock") == 1)
        #expect(t.count("GET /api/vault") == 2)
    }

    @Test func aFailedPatchShowsAMessageAndRemoveRunsDoneOnlyOnSuccess() async {
        let t = RoutedTransport()
        t.on("GET /api/vault", vaultJSON(["a"]))
        t.on("PATCH /api/vault/a", 400, #"{"detail":"bad"}"#)
        t.on("DELETE /api/credentials/a", "{}")
        let m = VaultModel(api: t.api())
        m.patch("a", ["x": .string("y")])
        await m.jobs.settled()
        #expect(!m.message.isEmpty)
        m.clearMessage()
        var gone = 0
        m.remove("a", done: { gone += 1 })
        await m.jobs.settled()
        #expect(gone == 1)
        t.on("DELETE /api/credentials/a", 500, #"{"detail":"x"}"#)
        m.remove("a", done: { gone += 1 })
        await m.jobs.settled()
        #expect(gone == 1)
    }

    @Test func anOlderReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/vault") { _, n in
            if n == 0 { await gate.wait(); return (200, vaultJSON(["old"])) }
            return (200, vaultJSON(["new"]))
        }
        let m = VaultModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/vault") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.item("new") != nil)
        #expect(m.item("old") == nil)
    }

    @Test func addingALoginHandsOverItsName() async {
        let t = RoutedTransport()
        t.on("POST /api/vault/logins", #"{"name":"shop"}"#)
        t.on("GET /api/vault", vaultJSON(["shop"]))
        let m = VaultModel(api: t.api())
        var made: String?
        m.addLogin(site: " shop.test ", label: "me", created: { made = $0 })
        await m.jobs.settled()
        #expect(made == "shop")
    }

    @Test func anAiKeySaveAndItsFailure() async {
        let t = RoutedTransport()
        t.on("PUT /api/providers/p/key", "{}")
        t.on("GET /api/providers", "[]")
        let m = VaultModel(api: t.api())
        m.saveAiKey("p", key: " k ")
        await m.jobs.settled()
        #expect(m.aiSaved)
        t.on("PUT /api/providers/p/key", 400, #"{"detail":"bad key"}"#)
        m.saveAiKey("p", key: "k")
        await m.jobs.settled()
        #expect(!m.aiSaved)
        #expect(!m.message.isEmpty)
    }
}

@MainActor @Suite struct SecureEntryModelTests {
    @Test func savingAKeyThenAFailure() async {
        let t = RoutedTransport()
        t.on("PUT /api/credentials/k/secret", "{}")
        let vault = VaultModel(api: t.api())
        let entry = try! JSONDecoder().decode(VaultEntry.self, from: Data(#"{"name":"k"}"#.utf8))
        let m = SecureEntryModel(vault: vault)
        var saved = 0
        m.save(entry, user: "", pass: " secret ", mode: .now, saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        #expect(t.calls.last?.body.contains("secret") == true)
        t.on("PUT /api/credentials/k/secret", 400, #"{"detail":"no"}"#)
        m.save(entry, user: "", pass: "x", mode: .now, saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        #expect(!m.message.isEmpty)
    }
}

@MainActor @Suite struct WaitingEntriesModelTests {
    @Test func aFailedReadKeepsTheList() async {
        let t = RoutedTransport()
        var changed = 0
        let m = WaitingEntriesModel(api: t.api(), onChanged: { changed += 1 })
        await m.load()
        #expect(m.list.isEmpty)
        m.fill("x")
        #expect(m.open == "x")
        m.closeForm()
        #expect(m.open == nil)
        #expect(changed == 0)
    }
}

@MainActor @Suite struct PermissionsModelTests {
    private let hist = #"{"items":[{"id":1}],"next_before":5}"#

    @Test func loadsRulesAndHistoryAndMore() async {
        let t = RoutedTransport()
        t.on("GET /api/approvals/pending", "[]")
        t.on("GET /api/approvals/rules", #"{"items":[{"id":7,"site":"s"}]}"#)
        t.on("GET /api/approvals/history") { c, _ in (200, c.query.contains("before") ? #"{"items":[{"id":2}]}"# : #"{"items":[{"id":1}],"next_before":5}"#) }
        let m = PermissionsModel(api: t.api())
        await m.load()
        #expect(m.rules.count == 1)
        #expect(m.history.count == 1)
        #expect(!m.failed)
        m.more()
        await m.jobs.settled()
        #expect(m.history.count == 2)
        #expect(m.next == nil)
    }

    @Test func aFailureMarksFailedAndRevokeFailureShowsNotice() async {
        let t = RoutedTransport()
        t.on("GET /api/approvals/pending", 500, "{}")
        let m = PermissionsModel(api: t.api())
        await m.load()
        #expect(m.failed)
        let r = try! JSONDecoder().decode(ApprovalRule.self, from: Data(#"{"id":7}"#.utf8))
        t.on("DELETE /api/approvals/rules/7", 500, #"{"detail":"x"}"#)
        m.revoke(r)
        await m.jobs.settled()
        #expect(m.notice != nil)
    }

    @Test func anOlderLoadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/approvals/pending", "[]")
        t.on("GET /api/approvals/rules") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"items":[{"id":1}]}"#) }
            return (200, #"{"items":[{"id":1},{"id":2}]}"#)
        }
        t.on("GET /api/approvals/history", #"{"items":[]}"#)
        let m = PermissionsModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/approvals/rules") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.rules.count == 2)
    }
}

@MainActor @Suite struct OperatorModelsTests {
    @Test func oauthAppSavesAndFails() async {
        let t = RoutedTransport()
        t.on("PUT /api/oauth-apps/github", #"{"client_id":"abc","has_secret":true}"#)
        let m = OAuthAppModel(api: t.api(), provider: "github")
        var kept: String?
        m.save(clientId: " abc ", secret: "s", saved: { kept = $0 })
        await m.jobs.settled()
        #expect(kept == "abc")
        t.on("PUT /api/oauth-apps/github", 500, #"{"detail":"x"}"#)
        kept = nil
        m.save(clientId: "abc", secret: "", saved: { kept = $0 })
        await m.jobs.settled()
        #expect(kept == nil)
    }

    @Test func firebaseLoadsAndFailureIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/operator/firebase", #"{"enabled":true,"project_id":"p"}"#)
        let m = FirebaseModel(api: t.api())
        await m.load()
        #expect(m.cfg?.projectId == "p")
        let t2 = RoutedTransport()
        let m2 = FirebaseModel(api: t2.api())
        await m2.load()
        #expect(m2.failed)
    }

    @Test func aBadKeyFileIsRefusedBeforeAnythingIsSent() async throws {
        let t = RoutedTransport()
        let m = FirebaseModel(api: t.api())
        let f = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-test-\(UUID().uuidString).json")
        try Data("not json".utf8).write(to: f)
        defer { try? FileManager.default.removeItem(at: f) }
        m.upload(f)
        await m.jobs.settled()
        #expect(!m.error.isEmpty)
        #expect(t.calls.isEmpty)
    }

    @Test func cloudflareLoadsAndTurnOffFailureShowsError() async {
        let t = RoutedTransport()
        t.on("GET /api/operator/cloudflare", "{}")
        let m = CloudflareModel(api: t.api())
        await m.load()
        #expect(m.cfg != nil)
        t.on("PUT /api/operator/cloudflare", 500, #"{"detail":"x"}"#)
        m.turnOff()
        await m.jobs.settled()
        #expect(!m.error.isEmpty)
    }

    @Test func reportsFilterAnswerComingBackLateIsDropped() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/operator/reports") { c, _ in
            if c.query.contains("handled=false") { await gate.wait(); return (200, #"{"open":1}"#) }
            return (200, #"{"open":9}"#)
        }
        let m = ReportsModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/operator/reports") == 1 }
        m.toggleHandled()
        await m.load()
        await gate.release()
        await first.value
        #expect(m.data?.open == 9)
    }

    @Test func reportsFailureIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/operator/reports", 500, "{}")
        let m = ReportsModel(api: t.api())
        await m.load()
        #expect(m.failed)
    }

    @Test func betaLoadsAndDeleteFailureShowsError() async {
        let t = RoutedTransport()
        t.on("GET /api/operator/beta", #"{"open":2}"#)
        let m = BetaRequestsModel(api: t.api())
        await m.load()
        #expect(m.data?.open == 2)
        t.on("DELETE /api/operator/beta/3", 500, #"{"detail":"x"}"#)
        m.delete(3)
        await m.jobs.settled()
        #expect(!m.error.isEmpty)
    }
}

@MainActor @Suite struct DocumentsModelTests {
    @Test func loadsTheListAndANewerReadWins() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/uploads") { _, n in
            if n == 0 { await gate.wait(); return (200, #"[{"path":"a.txt"}]"#) }
            return (200, #"[{"path":"a.txt"},{"path":"b.txt"}]"#)
        }
        let m = DocumentsModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/uploads") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.docs?.count == 2)
    }

    @Test func aFileOnTheServerAnswering413SaysTooLarge() async throws {
        let t = RoutedTransport()
        t.on("PUT /api/uploads/a.txt", 413, #"{"detail":"big"}"#)
        let f = FileManager.default.temporaryDirectory.appendingPathComponent("a.txt")
        try Data("hi".utf8).write(to: f)
        defer { try? FileManager.default.removeItem(at: f) }
        let m = DocumentsModel(api: t.api())
        m.start(url: f, scoped: false)
        await m.finished()
        #expect(!m.message.isEmpty)
        #expect(!m.uploading)
    }

    @Test func fileViewerReadsTextAndDeletes() async {
        let t = RoutedTransport()
        t.on("GET /api/uploads/a.txt/text", #"{"name":"a.txt","text":"hello"}"#)
        t.on("DELETE /api/uploads/a.txt", "{}")
        let m = FileViewerModel(api: t.api(), name: "a.txt")
        await m.load()
        #expect(m.text == "hello")
        m.delete()
        await m.jobs.settled()
        #expect(m.deleted)
    }

    @Test func fileViewerShowsAFailure() async {
        let t = RoutedTransport()
        t.on("GET /api/uploads/a.txt/text", 500, #"{"detail":"x"}"#)
        t.on("DELETE /api/uploads/a.txt", 500, #"{"detail":"x"}"#)
        let m = FileViewerModel(api: t.api(), name: "a.txt")
        await m.load()
        #expect(!m.error.isEmpty)
        m.delete()
        await m.jobs.settled()
        #expect(!m.deleted)
    }
}

@MainActor private final class FakeKeyguardHost: KeyguardHost {
    var api: KeloApi?
    var session: Session? = Session(serverUrl: "https://kelo.test", token: "t")
    var locked = false
    init(_ api: KeloApi?) { self.api = api }
    func setLocked(_ on: Bool) { locked = on }
    func refreshKeyguard() async -> Bool? { true }
}

@MainActor @Suite struct KeyguardModelTests {
    @Test func loadShowsTheViewAndLockedState() async {
        let t = RoutedTransport()
        t.on("GET /api/keyguard", #"{"mode":"phone","locked":true}"#)
        t.on("GET /api/devices", #"{"devices":[],"current":3}"#)
        let host = FakeKeyguardHost(t.api())
        let m = KeyguardModel(host: host)
        await m.load()
        #expect(m.view?.locked == true)
        #expect(host.locked)
        #expect(m.me == 3)
    }

    @Test func aPairedPhoneIsToldItIsNotTheOwner() async {
        let t = RoutedTransport()
        t.on("GET /api/keyguard", 403, #"{"detail":"owner"}"#)
        let host = FakeKeyguardHost(t.api())
        let m = KeyguardModel(host: host)
        await m.load()
        #expect(m.notOwner)
    }

    @Test func anOlderLoadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/keyguard") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"mode":"phone","locked":true}"#) }
            return (200, #"{"mode":"phone","locked":false}"#)
        }
        t.on("GET /api/devices", #"{"devices":[]}"#)
        let host = FakeKeyguardHost(t.api())
        let m = KeyguardModel(host: host)
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/keyguard") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.view?.locked == false)
    }
}
