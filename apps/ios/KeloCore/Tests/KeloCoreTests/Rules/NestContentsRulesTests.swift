import Foundation
import Testing
@testable import KeloCore

/// Port of android my/NestContentsTest.kt: the card's contents and an update's changed (parsing, count, size text rule, cut, hasContents).
@Suite struct NestContentsRulesTests {
    private func contents(_ s: String) throws -> NestContents { try JSON.decode(NestContents.self, from: try JSON.parse(s)) }
    private func changed(_ s: String) throws -> NestChanged { try JSON.decode(NestChanged.self, from: try JSON.parse(s)) }

    private let full = #"""
    {"files":[{"path":"SKILL.md","bytes":2048,"sha256":"aa"},{"path":"run.py","bytes":1025,"sha256":"bb"}],
     "total_bytes":3073,"texts":{"SKILL.md":"# Reader\n","kelo-package.json":"{}"},"cut":["SKILL.md"],
     "endpoints":["api.example.com"],"warnings":["Large file skipped"]}
    """#

    @Test func theCardsContentsParseEveryField() throws {
        let c = try contents(full)
        #expect(c.files.map(\.path) == ["SKILL.md", "run.py"])
        #expect(c.files.map(\.bytes) == [2048, 1025])
        #expect(c.files.first?.sha256 == "aa")
        #expect(c.totalBytes == 3073)
        #expect(c.texts == ["SKILL.md": "# Reader\n", "kelo-package.json": "{}"])
        #expect(c.cut == ["SKILL.md"])
        #expect(c.endpoints == ["api.example.com"])
        #expect(c.warnings == ["Large file skipped"])
    }

    @Test func aMissingOrOlderContentsReadsAsEmptyAndShowsNoRow() throws {
        let none = try contents("{}")
        #expect(none == NestContents())
        #expect(!NestContentsRules.hasContents(none))
        #expect(!NestContentsRules.hasContents(try contents(#"{"total_bytes":0,"files":[],"endpoints":[],"warnings":[]}"#)))
    }

    @Test func unknownFieldsAreIgnoredAndNullsReadAsMissing() throws {
        let c = try contents(#"{"files":[{"path":"ok.md","bytes":5,"mode":"x"}],"endpoints":["a.example"],"total_bytes":null}"#)
        #expect(c.files == [NestFile(path: "ok.md", bytes: 5, sha256: "")])
        #expect(c.endpoints == ["a.example"] && c.totalBytes == 0)
    }

    @Test func hasContentsIsTrueWhenAnyOfFilesEndpointsOrWarningsIsThere() throws {
        #expect(NestContentsRules.hasContents(try contents(#"{"files":[{"path":"a"}]}"#)))
        #expect(NestContentsRules.hasContents(try contents(#"{"endpoints":["x.test"]}"#)))
        #expect(NestContentsRules.hasContents(try contents(#"{"warnings":["w"]}"#)))
        #expect(!NestContentsRules.hasContents(try contents(#"{"texts":{"a.md":"x"},"total_bytes":9}"#)))
    }

    @Test func aPathIsCutOnlyWhenTheServerSaysSo() throws {
        let c = try contents(full)
        #expect(NestContentsRules.isCut(c, "SKILL.md"))
        #expect(!NestContentsRules.isCut(c, "kelo-package.json"))
    }

    @Test func changedCountIsAddedPlusRemovedPlusModified() throws {
        let ch = try changed(#"{"added":["a.md","b.md"],"removed":["c.md"],"modified":["d.md","e.md","f.md"]}"#)
        #expect(NestContentsRules.changedCount(ch) == 6)
        #expect(NestContentsRules.changedCount(NestChanged()) == 0)
        #expect(try changed(#"{"modified":["only.md"]}"#).modified == ["only.md"])
    }

    @Test func sizeIsBytesUnderOneKilobyteThenWholeKilobytesRoundedUp() {
        #expect(NestContentsRules.size(0) == .bytes(0))
        #expect(NestContentsRules.size(1023) == .bytes(1023))
        #expect(NestContentsRules.size(1024) == .kilobytes(1))
        #expect(NestContentsRules.size(1025) == .kilobytes(2))
        #expect(NestContentsRules.size(2048) == .kilobytes(2))
        #expect(NestContentsRules.size(65 * 1024) == .kilobytes(65))
    }
}
