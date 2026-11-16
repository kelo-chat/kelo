import Testing
import Foundation
@testable import KeloShared
@testable import KeloChat

struct SlashCommandsTests {
    @Test func aSlashListsTheCommands() {
        #expect(SlashCommands.matches("/").map(\.word) == ["/new"])
    }

    @Test func theTypedStartFilters() {
        #expect(SlashCommands.matches("/N").map(\.word) == ["/new"])
        #expect(SlashCommands.matches("/x").isEmpty)
    }

    @Test func aSpaceOrPlainTextClosesIt() {
        #expect(SlashCommands.matches("/new ").isEmpty)
        #expect(SlashCommands.matches("hello /new").isEmpty)
        #expect(SlashCommands.matches("").isEmpty)
    }

    @Test func pickingPutsTheWordAndASpace() {
        #expect(SlashCommands.picked(SlashCommands.all.first!) == "/new ")
    }
}
