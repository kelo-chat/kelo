import Foundation
import Testing
import KeloCore
import KeloTestSupport
@testable import KeloShared

@MainActor @Suite struct SecretAnswerModelTests {
    @Test func aKeyIsSavedAndAFailureShowsItsReason() async {
        let t = RoutedTransport()
        t.on("PUT /api/credentials/k/secret", "{}")
        let m = SecretAnswerModel()
        var saved = 0
        m.saveKey(name: "k", secret: "s", api: t.api(), saved: { _ in saved += 1 }, onGone: {})
        await m.settled()
        #expect(saved == 1 && !m.busy)
        t.on("PUT /api/credentials/k/secret", 400, #"{"detail":"bad"}"#)
        m.saveKey(name: "k", secret: "s", api: t.api(), saved: { _ in saved += 1 }, onGone: {})
        await m.settled()
        #expect(saved == 1 && m.problem == "bad")
    }

    @Test func anEntryTheServerNoLongerHasFoldsInsteadOfFailing() async {
        let t = RoutedTransport()
        t.on("PUT /api/vault/n/login", 404, #"{"detail":"gone"}"#)
        let m = SecretAnswerModel()
        var gone = 0
        m.saveLogin(name: "n", user: "u", pass: "p", mode: .afterSuccess, api: t.api(), saved: { _ in }, onGone: { gone += 1 })
        await m.settled()
        #expect(gone == 1 && m.problem.isEmpty)
    }

    @Test func aSignedOutPhoneSaysItCouldNot() async {
        let m = SecretAnswerModel()
        m.cardSaved(id: "r", cardId: "c", api: nil, saved: { _ in }, onGone: {})
        await m.settled()
        #expect(!m.problem.isEmpty)
    }
}

@MainActor @Suite struct SkillModelsTests {
    @Test func proposalsLoadAndAFailureKeepsWhatWasShown() async {
        let t = RoutedTransport()
        t.on("GET /api/skills/proposals", #"{"items":[],"drafting":[]}"#)
        let m = SkillProposalsModel(api: { t.api() }, say: { _ in })
        await m.load()
        #expect(m.data?.available == true)
        let kept = m.data
        t.down = true
        await m.load()
        #expect(m.data == kept)
    }

    @Test func anOlderProposalsReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/skills/proposals") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"items":[],"drafting":[],"available":false}"#) }
            return (200, #"{"items":[],"drafting":[]}"#)
        }
        let m = SkillProposalsModel(api: { t.api() }, say: { _ in })
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/skills/proposals") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.data?.available == true)
    }

    @Test func savingAsASkillSaysWhyItCouldNot() async {
        let t = RoutedTransport()
        t.on("POST /api/skills/draft", 409, #"{"detail":"busy"}"#)
        t.on("GET /api/skills/proposals", #"{"items":[],"drafting":[]}"#)
        var said: [String] = []
        let m = SaveAsSkillModel(taskId: 5, workerId: nil, api: { t.api() }, say: { said.append($0) })
        m.save()
        await m.settled()
        #expect(said.count == 1 && !m.posting)
    }
}
