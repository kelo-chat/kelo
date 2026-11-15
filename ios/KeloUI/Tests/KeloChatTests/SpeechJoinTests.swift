import Testing
@testable import KeloChat

struct SpeechJoinTests {
    @Test func joinsPieces() {
        #expect(SpeechJoin.join("", " 안녕") == "안녕")
        #expect(SpeechJoin.join("안녕", "하세요") == "안녕 하세요")
        #expect(SpeechJoin.join("Hi", ", there") == "Hi, there")
        #expect(SpeechJoin.join("Hi", " there") == "Hi there")
        #expect(SpeechJoin.join("Hi", "") == "Hi")
    }
}
