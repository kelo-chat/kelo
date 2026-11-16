import Testing
import Foundation
import KeloCore
@testable import KeloMy

// Android agent/MemoryPassRulesTest.kt
@Suite struct MemoryPassTests {
    @Test func theReflectionAnswerCarriesTheLastMemoryPass() throws {
        let body = """
        {"enabled": true, "rules": [], "last_run": null, "guide": "",
         "memory": {"last_pass": {"at": 1.7e9, "status": "done", "code": "", "added": 3, "tidied": 2, "files": ["MEMORY.md", "user.md"],
                    "trigger": "nightly", "undone": false, "can_undo": true}, "last_attempt": {"status": "skipped"}}}
        """
        let look = try JSONDecoder().decode(MyLookback.self, from: Data(body.utf8))
        let pass = try #require(look.memoryPass)
        #expect(pass.added == 3 && pass.tidied == 2 && pass.canUndo && !pass.undone)
        #expect(MemoryPassRules.shown(pass))
        let older = try JSONDecoder().decode(MyLookback.self, from: Data(#"{"enabled": true, "rules": []}"#.utf8))
        #expect(older.memoryPass == nil)
    }

    @Test func theLineShowsAPassThatChangedSomethingOrWasUndone() {
        #expect(!MemoryPassRules.shown(nil))
        #expect(!MemoryPassRules.shown(MemoryPass(status: "done")))
        #expect(!MemoryPassRules.shown(MemoryPass(status: "failed", added: 2)))
        #expect(MemoryPassRules.shown(MemoryPass(status: "done", tidied: 1)))
        #expect(MemoryPassRules.shown(MemoryPass(status: "done", undone: true)))
    }

    @Test func changesAreGroupedByWhatTheyAreAbout() {
        let groups = MemoryPassRules.group([
            MemoryChange(file: "memory/people/jun.md", added: ["Leads the team"]),
            MemoryChange(file: "MEMORY.md", added: ["Likes tea"], removed: ["Old log", ""]),
            MemoryChange(file: "user.md", added: ["Timezone: Asia/Seoul"]),
            MemoryChange(file: "memory/people/ana.md", removed: ["Moved away"]),
            MemoryChange(file: "AGENTS.md"),
        ])
        #expect(groups.map(\.area) == [.aboutYou, .toRemember, .people])
        #expect(groups[1].removed == ["Old log"])
        #expect(groups[2].added == ["Leads the team"] && groups[2].removed == ["Moved away"])
        #expect(MemoryPassRules.area("AGENTS.md") == .workingNotes)
    }
}
