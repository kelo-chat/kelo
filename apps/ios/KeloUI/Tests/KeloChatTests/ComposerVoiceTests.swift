#if os(iOS)
import Testing
import Foundation
import KeloShared
@testable import KeloChat

/// The composer's voice on iPhone: the words go into the field, and only ↑ sends them (the phone's recognizer and the server path alike).
@MainActor
@Suite struct ComposerVoiceTests {
    private final class Field {
        var text = ""
        var sent: [String] = []
    }

    private func voice(_ field: Field) -> ComposerVoiceModel {
        let v = ComposerVoiceModel()
        v.target = ComposerVoiceModel.Target(get: { field.text }, set: { field.text = $0 }, send: { field.sent.append($0) })
        return v
    }

    @Test func wordsWithFieldGoIntoTheFieldAndAreNotSent() {
        let field = Field()
        field.text = "hi"
        voice(field).deliver("there", after: .field)
        #expect(field.text == "hi there")
        #expect(field.sent.isEmpty)
    }

    @Test func wordsWithSendAreSentMerged() {
        let field = Field()
        field.text = "hi"
        voice(field).deliver("there", after: .send)
        #expect(field.sent == ["hi there"])
    }

    @Test func noTargetDoesNotCrash() {
        ComposerVoiceModel().deliver("words", after: .send)
    }

    @Test func nothingRecordingMeansNoBar() {
        let v = ComposerVoiceModel()
        #expect(v.bar == nil)
        #expect(v.showsBar == false)
    }

    @Test func aHeldMicKeepsTheComposerEvenWhileServerRecords() {
        let v = ComposerVoiceModel()
        v.held = true
        #expect(v.showsBar == false)
    }
}
#endif
