import SwiftUI
import KeloCore

/// "Your chats go to Google" under the chosen provider (model picker, Model page; Android chat/ConsentSheet.kt ChatsGoLine). Nothing
/// when it cannot be told (no provider chosen, an older server).
public struct ChatsGoLine: View {
    let provider: String?
    let company: String?
    public init(provider: String?, company: String?) { self.provider = provider; self.company = company }

    public var body: some View {
        switch AiConsent.chatsGo(provider: provider, company: company) {
        case .to(let name): MutedText(L(R.string.ai_chats_go_to, name), small: true).accessibilityIdentifier("kelo.chatsGo")
        case .local: MutedText(L(R.string.ai_chats_stay), small: true).accessibilityIdentifier("kelo.chatsGo")
        case nil: EmptyView()
        }
    }
}
