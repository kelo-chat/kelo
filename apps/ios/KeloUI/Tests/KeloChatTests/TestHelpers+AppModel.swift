import Foundation
import KeloCore
@testable import KeloShared

extension AppModel {
    /// The signed-in API, or a 401 error (the tests' way to ask for it).
    func requireApi() throws -> KeloApi {
        guard let api else { throw ApiError(status: 401, detail: "signed out") }
        return api
    }
}
