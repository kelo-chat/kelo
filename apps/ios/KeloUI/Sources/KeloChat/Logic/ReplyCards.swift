import Foundation
import KeloCore

// Picture cards on a choice question (Android chat/ReplyCards.kt, ChatModels.kt ReplyQuestion.cardList): one card per option, shown as a
// list of tappable cards instead of the option buttons. The rules here are the same for a button and for a card: one tap, one answer.

extension ReplyQuestion {
    /// The cards when they match the options one to one and at least one of them shows something; nil = plain buttons.
    public var cardList: [ReplyCard]? {
        cards.count == options.count && !options.isEmpty && cards.contains { !$0.isEmpty } ? cards : nil
    }
}

public enum ReplyCardRules {
    /// What VoiceOver says for a picture card: "option, detail, recommended" (the picture adds nothing).
    public static func label(_ option: String, _ card: ReplyCard, recommended: String) -> String {
        [option, card.detail, card.recommended ? recommended : ""]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: ", ")
    }
}

extension QuickReplyRules {
    /// What a tap on one option of question `i` does. A button and a card share it: sent at once, or picked to go with the send button.
    public enum Tap: Equatable, Sendable {
        case send(String, [ReplyAnswer])
        case pick
    }

    public static func tap(_ set: ReplySet, _ i: Int, _ option: String) -> Tap {
        guard set.questions.indices.contains(i) else { return .pick }
        return sendsOnTap(set, set.questions[i], option) ? .send(option, [ReplyAnswer(i, option)]) : .pick
    }

    /// The picks after a tap that picks: the same option again takes its pick back.
    public static func toggled(_ picks: [Int: String], _ i: Int, _ option: String) -> [Int: String] {
        var next = picks
        next[i] = picks[i] == option ? nil : option
        return next
    }
}
