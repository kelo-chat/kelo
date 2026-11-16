import Foundation

/// The words around an approval card's question ("사이트 열기를 할까요?"): an English "-ing" label reads lower-case inside the
/// sentence, and Korean gets its object particle (Android ApprovalWords).
public enum AskWords {
    /// `word` as it reads inside "Allow …?": an English capitalized "-ing" phrase is lowered ("Allow adding an event?").
    public static func askWord(_ word: String, language: String) -> String {
        guard language == "en" else { return word }
        let first = word.split(separator: " ").first.map(String.init) ?? word
        guard first.count >= 4, first.hasSuffix("ing"), let c0 = first.first, c0.isUppercase,
              !first.dropFirst().contains(where: { $0.isUppercase }) else { return word }
        return word.prefix(1).lowercased() + word.dropFirst()
    }

    /// The Korean object particle after `word` (사이트 열기를, 로그인 정보 입력을).
    public static func objectParticle(_ word: String) -> String {
        guard let last = word.trimmingCharacters(in: .whitespaces).last(where: { $0.isLetter || $0.isNumber }) else { return "를" }
        if let v = last.unicodeScalars.first?.value, (0xAC00...0xD7A3).contains(v) { return (v - 0xAC00) % 28 != 0 ? "을" : "를" }
        if last.isNumber { return "013678".contains(last) ? "을" : "를" }
        return "aeiouy".contains(last.lowercased()) ? "를" : "을"
    }
}
