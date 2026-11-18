import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// The designed mail's card in the chat (Android ChatItem.Approval.mail): the mail part reaches the chat item, and a card without it has none.
@Suite struct MailPreviewCardTests {
    private func approval(_ items: [ChatItem]) -> ChatItem.Approval? {
        if case .approval(let a)? = items.first { return a }
        return nil
    }

    @Test func aDesignedMailKeepsItsPreviewInTheChatItem() throws {
        let mail = MailDesign(previewPath: "/api/approvals/a1/mail-preview", pictures: 1, links: 0)
        let items = ChatReducer.apply([], .approvalRequest(.init(approvalId: "a1", name: "email_send", mail: mail)), newKey: { "k" })
        let a = try #require(approval(items))
        #expect(a.mail == mail)
    }

    @Test func aPlainApprovalHasNoPreview() throws {
        let items = ChatReducer.apply([], .approvalRequest(.init(approvalId: "a2", name: "calendar_add")), newKey: { "k" })
        let a = try #require(approval(items))
        #expect(a.mail == nil)
    }
}
