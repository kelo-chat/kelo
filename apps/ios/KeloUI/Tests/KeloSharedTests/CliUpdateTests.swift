import Testing
import Foundation
import KeloCore
@testable import KeloShared

// Android my/CliUpdateTest.kt
@Suite struct CliUpdateTests {
    private let cli = CliStatus(installed: true, remote: true, version: "2.1.300 (Claude Code)", updateAvailable: true,
                                latestVersion: "2.1.319", updatable: true)

    private func st(_ json: String) throws -> CliUpdateStatus { try JSONDecoder().decode(CliUpdateStatus.self, from: Data(json.utf8)) }

    @Test func buttonOnlyForTheOwnerWhenNewerAndIdle() {
        #expect(CliUpdateRules.showButton(cli, busy: false))
        #expect(!CliUpdateRules.showButton(cli, busy: true))
        var c = cli; c.updatable = false
        #expect(!CliUpdateRules.showButton(c, busy: false))
        c = cli; c.updateAvailable = false
        #expect(!CliUpdateRules.showButton(c, busy: false))
        c = cli; c.installed = false
        #expect(!CliUpdateRules.showButton(c, busy: false))
    }

    @Test func aNoticeForTheOtherServiceGetsItsOwnBox() {
        let withCli = ["claude-subscription-directsdk-experimental", "antigravity-oauth"]
        #expect(CliUpdateRules.focus("antigravity-oauth", selected: "claude-subscription-directsdk-experimental", withCli: withCli) == "antigravity-oauth")
        #expect(CliUpdateRules.focus("antigravity-oauth", selected: "antigravity-oauth", withCli: withCli) == nil)   // the form shows it
        #expect(CliUpdateRules.focus("openai", selected: "antigravity-oauth", withCli: withCli) == nil)               // not a CLI
        #expect(CliUpdateRules.focus("", selected: "openai", withCli: withCli) == nil)
        #expect(CliUpdateRules.focus(nil, selected: "openai", withCli: withCli) == nil)
    }

    @Test func statusReadsIntoAnOutcome() throws {
        let running = try st(#"{"state":"running","step":"installing","from":"2.1.300","to":"2.1.319","active":{"source":"image"}}"#)
        #expect(!CliUpdateRules.finished(running) && CliUpdateRules.outcome(running) == .running)
        let done = try st(#"{"state":"done","ok":true,"from":"2.1.300","to":"2.1.319","product":"Claude Code"}"#)
        #expect(CliUpdateRules.finished(done) && CliUpdateRules.outcome(done) == .updated(product: "Claude Code", version: "2.1.319"))
        #expect(CliUpdateRules.outcome(try st(#"{"state":"done","ok":true,"unchanged":true}"#), fallbackProduct: "Antigravity") == .unchanged(product: "Antigravity"))
        #expect(CliUpdateRules.outcome(try st(#"{"state":"failed","ok":false,"error":"The new version does not start"}"#)) == .failed("The new version does not start"))
    }

    @Test func cliStatusReadsUpdatable() throws {
        let c = try JSONDecoder().decode(CliStatus.self, from: Data(#"{"installed":true,"update_available":true,"updatable":true}"#.utf8))
        #expect(c.updatable && CliUpdateRules.showButton(c, busy: false))
        let old = try JSONDecoder().decode(CliStatus.self, from: Data(#"{"installed":true,"update_available":true}"#.utf8))
        #expect(!old.updatable)
        #expect(CliUpdateRules.path("claude-subscription-directsdk-experimental") == "/api/providers/claude-subscription-directsdk-experimental/cli/update")
    }
}
