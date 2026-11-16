import Foundation
@testable import KeloShared

extension SecretAnswerModel {
    func settled() async { await jobs.settled() }
}

extension SkillProposalsModel {
    func settled() async { await jobs.settled() }
}

extension SaveAsSkillModel {
    func settled() async { await jobs.settled() }
}
