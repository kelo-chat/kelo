import Testing
import Foundation
import KeloCore
@testable import KeloConnectors

@Suite struct PermissionRulesTests {
    static let answer = """
    {"connector":"github","name":"GitHub","kinds":[
      {"kind":"read","mode":"allow","default":"allow","actions":[]},
      {"kind":"send","mode":"ask","default":"ask","actions":[{"id":"create_issue","label":"Create an issue","mode":"ask","default":"ask"}]}]}
    """

    private func parsed() throws -> ConnectorPermissions {
        try JSONDecoder().decode(ConnectorPermissions.self, from: Data(Self.answer.utf8))
    }

    @Test func aGetAnswerDecodes() throws {
        let p = try parsed()
        #expect(p.name == "GitHub")
        #expect(p.kinds.map(\.kind) == ["read", "send"])
        #expect(p.kinds[0].actions.isEmpty)
        #expect(p.kinds[1].actions.first?.id == "create_issue")
    }

    @Test func changedOnlyWhenSomethingDiffers() throws {
        var p = try parsed()
        #expect(!PermissionRules.changed(p))
        p.kinds[1].actions[0].mode = "off"
        #expect(PermissionRules.changed(p))
        p.kinds[1].actions[0].mode = "ask"
        p.kinds[0].mode = "off"
        #expect(PermissionRules.changed(p))
    }

    @Test func anActionAtItsDefaultSendsNull() throws {
        let a = try parsed().kinds[1].actions[0]
        #expect(PermissionRules.actionBody(a, a.default) == ["actions": .object(["create_issue": .null])])
        #expect(PermissionRules.actionBody(a, "off") == ["actions": .object(["create_issue": .string("off")])])
        #expect(PermissionRules.kindBody("send", "off") == ["send": .string("off")])
    }

    @Test func theListRowKnowsLimited() throws {
        let x = try JSONDecoder().decode(ConnectorInfo.self, from: Data(#"{"id":"github","limited":true}"#.utf8))
        #expect(x.limited)
    }
}
