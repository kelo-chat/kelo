import Foundation

/// The text so far while Apple's newer recognizer speaks in pieces: the final pieces kept so far plus the newest piece, with one
/// space between them unless the piece already starts with a space or punctuation.
public enum SpeechJoin {
    public static func join(_ done: String, _ piece: String) -> String {
        if done.isEmpty { return piece.trimmingCharacters(in: .whitespaces) }
        if piece.isEmpty { return done }
        if let f = piece.first, f.isWhitespace || ",.!?;:)".contains(f) { return done + piece }
        return done + " " + piece
    }
}
