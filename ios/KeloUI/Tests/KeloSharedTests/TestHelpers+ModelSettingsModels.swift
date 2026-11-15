import Foundation
@testable import KeloShared

extension ModelSettingsModel {
    /// Waits for the lists under way (tests).
    func listsSettled() async {
        await mainLoad?.value; await fbLoad?.value; await bgLoad?.value; await providersLoad?.value
    }
}
