import Foundation
@testable import KeloWork

extension WorkModel {
    /// Waits for the reads under way (tests).
    func settled() async { for t in Array(reads.values) { await t.value } }
}
