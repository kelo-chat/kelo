import Testing
import Foundation
@testable import KeloCore

// Android agent/SkillModelsTest.kt
@Suite struct SkillRulesTests {
    private let body = """
    {"items": [
        {"id": "a1", "skill": "renew-library-book", "kind": "new", "description": "Renew a book.", "text": "## When to use\\nA loan runs out.\\n\\n## Steps\\n1. Sign in.",
         "diff": [], "reason": "Renew my book", "origin": {"type": "draft", "task_id": 7}, "outside_text": true, "created_at": 1700000100.0,
         "secrets_removed": 1, "something_newer": {"x": 1}},
        {"id": "b2", "skill": "tidy-inbox", "kind": "change", "description": "Tidy.", "text": "x",
         "diff": [{"op": "gap", "text": ""}, {"op": "del", "text": "2. Old."}, {"op": "add", "text": "2. New."}, {"op": "add", "text": "3. More."}, {"op": "same", "text": "4. Same."}],
         "origin": {"type": "chat", "task_id": 3}, "outside_text": false, "created_at": 1700000200.0},
        {"id": "edited-hand-made", "skill": "hand-made", "kind": "edited", "description": "By hand.", "text": "y", "diff": [{"op": "file", "text": "SKILL.md"}],
         "origin": {"type": "disk"}, "outside_text": true, "created_at": null, "content_hash": "abc", "never_approved": true}
      ], "drafting": [{"task_id": 9}, {"worker_id": "w1", "task_id": 4}]}
    """

    private func parsed() throws -> SkillProposals { try JSONDecoder().decode(SkillProposals.self, from: Data(body.utf8)) }

    @Test func theProposalsParseWithUnknownFieldsIgnoredAndMissingOnesDefaulted() throws {
        let p = try parsed()
        #expect(p.items.count == 3)
        let draft = p.items[0]
        #expect(draft.origin.type == "draft")
        #expect(draft.origin.taskId == 7)
        #expect(draft.outsideText)
        #expect(draft.secretsRemoved == 1)
        #expect(p.items[2].contentHash == "abc")
        #expect(p.items[2].neverApproved)
        #expect(p.items[2].createdAt == nil)
        #expect(p.available)
        let bare = try JSONDecoder().decode(SkillProposals.self, from: Data(#"{"items": [{"id": "z"}]}"#.utf8))
        #expect(bare.items[0].kind == SkillProposal.new)
        #expect(bare.items[0].origin == SkillOrigin())
        #expect(bare.drafting.isEmpty)
        // an entry without its id is left out; an id sent as a number still reads
        let odd = try JSONDecoder().decode(SkillProposals.self, from: Data(#"{"items": [{"skill": "x"}, {"id": 5, "skill": "y"}]}"#.utf8))
        #expect(odd.items.map(\.id) == ["5"])
    }

    @Test func changesMadeOutsideTheAppComeFirstThenTheNewestAndBlankOnesAreLeftOut() throws {
        let p = try parsed()
        #expect(SkillRules.ordered(p.items + [SkillProposal(id: "", skill: "x"), SkillProposal(id: "q", skill: " ")]).map(\.id)
                == ["edited-hand-made", "b2", "a1"])
        #expect(SkillRules.without(p, id: "b2").items.map(\.id) == ["a1", "edited-hand-made"])
        #expect(SkillRules.without(p, id: "b2").drafting == p.drafting)
    }

    @Test func aChangeShowsItsLinesWithCountsAndANewSkillItsText() throws {
        let p = try parsed()
        #expect(!SkillRules.showsDiff(p.items[0]))
        #expect(SkillRules.showsDiff(p.items[1]))
        #expect(!SkillRules.showsDiff(SkillProposal(id: "c", skill: "c", kind: SkillProposal.change)))   // a change without lines: its text
        let counts = SkillRules.counts(p.items[1])
        #expect(counts.added == 2 && counts.removed == 1)
        let long = (1...30).map { "line \($0)" }
        #expect(SkillRules.preview(long, all: false).count == SkillRules.previewLines)
        #expect(SkillRules.preview(long, all: true).count == 30)
        #expect(SkillRules.hasMore(long))
        #expect(!SkillRules.hasMore(Array(long.prefix(3))))
        #expect(SkillRules.textLines(p.items[0].text + "\n\n") == ["## When to use", "A loan runs out.", "", "## Steps", "1. Sign in."])
    }

    @Test func saveAsSkillShowsOnlyOnFinishedRunsAndKnowsWhenADraftIsBeingWritten() throws {
        let p = try parsed()
        #expect(SkillRules.canSave("done"))
        for s in ["running", "failed", "stopped", "waiting_approval", "interrupted"] { #expect(!SkillRules.canSave(s)) }
        #expect(SkillRules.drafting(p, taskId: 9))
        #expect(!SkillRules.drafting(p, taskId: 4))      // that one is a job's draft: the job's page shows it
        #expect(SkillRules.drafting(p, workerId: "w1"))
        #expect(!SkillRules.drafting(nil, taskId: 9))
        #expect(SkillRules.draftPath(taskId: 9) == "/api/tasks/9/skill-draft")
        #expect(SkillRules.draftPath(workerId: "w1") == "/api/workers/w1/skill-draft")
        #expect(SkillRules.approvePath(p.items[0]) == "/api/skills/proposals/a1/approve")
        #expect(SkillRules.discardPath(p.items[0]) == "/api/skills/proposals/a1/discard")
        // an edited skill is approved only as the owner saw it
        #expect(SkillRules.approveBody(p.items[2]) == ["expected_hash": "abc"])
        #expect(SkillRules.approveBody(p.items[0]) == nil)
    }

    @Test func aSkillIsShownByItsNameInWordsNeverItsRawId() {
        #expect(SkillRules.title("renew-library-book") == "Renew library book")
        #expect(SkillRules.title("check-parcel-2") == "Check parcel 2")
        #expect(SkillRules.title("x") == "X")
        #expect(SkillRules.name(SkillProposal(id: "a", skill: "renew-library-book", name: "도서관 책 연장")) == "도서관 책 연장")
        #expect(SkillRules.name(SkillProposal(id: "a", skill: "renew-library-book", name: "  ")) == "Renew library book")
        #expect(SkillRules.title("tidy_inbox--weekly") == "Tidy inbox weekly")
    }

    @Test func theCallsUseTheDocumentedPathsAndAnOlderServerHasNone() async throws {
        let t = FakeTransport(.ok(404, #"{"detail": "Not Found"}"#), .ok(200, #"{"ok": true}"#), .ok(200, #"{"ok": true}"#),
                              .ok(202, #"{"status": "drafting"}"#))
        let api = KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "" })
        #expect(try await api.skillProposals().available == false)
        let edited = SkillProposal(id: "e1", skill: "hand-made", kind: SkillProposal.edited, contentHash: "abc")
        try await api.answerSkill(edited, approve: true)
        try await api.answerSkill(edited, approve: false)
        try await api.draftSkill(workerId: "w1")
        let r = t.requests.value
        #expect(r.map { $0.url?.path ?? "" } == ["/api/skills/proposals", "/api/skills/proposals/e1/approve",
                                                   "/api/skills/proposals/e1/discard", "/api/workers/w1/skill-draft"])
        #expect(r.map { $0.httpMethod ?? "" } == ["GET", "POST", "POST", "POST"])
        let sent = try JSON.parse(r[1].httpBody ?? Data())
        #expect(sent["expected_hash"]?.text == "abc")
    }
}
