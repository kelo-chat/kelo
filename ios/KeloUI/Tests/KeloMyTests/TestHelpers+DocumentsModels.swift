import Foundation
@testable import KeloMy

extension DocumentsModel {
    /// Waits for the upload under way (tests).
    func finished() async { await upload?.value }
}
