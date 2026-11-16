import Foundation
@testable import KeloChat

extension ChatModel {
    /// Waits for the reply in progress (tests).
    func waitForTurn() async { await turn?.value }
}
