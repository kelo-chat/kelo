import Foundation
@testable import KeloChat

extension ModelChooserModel {
    func settled() async {
        for t in Array(loads.values) { await t.value }
        await jobs.settled()
    }
}
