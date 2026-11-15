import Foundation
import KeloCore

/// How the chat keeps the secure entries the agent asked for (Android ChatViewModel refreshSecrets / settleSecret). Pure.
public enum SecretEntries {
    /// The entry `req` was answered here (saved, cancelled) or found gone. `followsHere` (`SecretRules.followsHere`): its line folds
    /// where it is (or joins the rows when only the server's list showed it) and that reply's end clears it; otherwise it goes now.
    /// The kept list drops it either way (the server lists only open entries).
    public static func settle(items: [ChatItem], secrets: [SecretRequest], _ req: SecretRequest, state: String,
                              result: SecretResult?, followsHere: Bool) -> (items: [ChatItem], secrets: [SecretRequest]) {
        var done = req
        // an entry answered already keeps its first answer
        if done.open {
            done.state = state
            if let result { done.result = result }
        }
        return (ChatReducer.afterAnswer(items, done, followsHere: followsHere), secrets.filter { $0.id != req.id })
    }

    /// Whether the status poll's open-entry count `now` (after `before`; nil = the first poll) means the list should be asked
    /// again: on every change, and on the first poll only when something is open (the screen loads the list itself on start).
    public static func pollWants(before: Int?, now: Int) -> Bool {
        guard let before else { return now > 0 }
        return before != now
    }
}
