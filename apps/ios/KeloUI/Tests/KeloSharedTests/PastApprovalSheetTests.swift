import Testing
import KeloCore
@testable import KeloShared

/// Who answered a past approval, as the detail sheet says it (Android PastApprovalSheet). A rule's allow or a run-out has no answerer.
@MainActor @Suite struct PastApprovalSheetTests {
    @Test func aPersonsAnswerIsNamedAndNothingElseIs() {
        #expect(PastApprovalSheet.answeredBy("app") == L(R.string.approval_via_app))
        #expect(PastApprovalSheet.answeredBy("notification") == L(R.string.approval_via_notification))
        #expect(PastApprovalSheet.answeredBy("messenger") == L(R.string.approval_via_messenger))
        #expect(PastApprovalSheet.answeredBy("") == nil)
        #expect(PastApprovalSheet.answeredBy("rule") == nil)
    }
}
