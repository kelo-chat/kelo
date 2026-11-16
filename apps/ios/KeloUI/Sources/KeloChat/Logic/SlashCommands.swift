import Foundation
import KeloShared

/// The chat's "/" commands (Android chat/SlashCommands.kt; the server knows the same words: server.py NEW_SESSION_WORDS). Pure, so it is tested.
public enum SlashCommands {
    public struct Command: Equatable, Sendable {
        public let word: String
        public let about: StringKey
    }

    public static let all: [Command] = [Command(word: "/new", about: R.string.slash_new_about)]

    /// The commands that start with what is typed: only while the text starts with "/" and has no space or line break yet.
    public static func matches(_ input: String) -> [Command] {
        guard input.hasPrefix("/"), !input.contains(where: { $0.isWhitespace }) else { return [] }
        let typed = input.lowercased()
        return all.filter { $0.word.hasPrefix(typed) }
    }

    /// The field after picking `c`: the word and a space (the space closes the list).
    public static func picked(_ c: Command) -> String { c.word + " " }
}
