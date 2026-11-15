import SwiftUI
import KeloCore
import KeloShared

/// The one question before chats first go to an AI service (`AiConsent`; Android chat/ConsentSheet.kt): what goes where, the
/// service chosen now, the privacy policy, and Agree / Not now. Dismissing it is Not now: nothing is sent.
struct ConsentSheet: View {
    let ask: ConsentAsk
    let busy: Bool
    let error: String?
    let onAgree: () -> Void
    let onNotNow: () -> Void
    @Environment(\.kelo) private var c
    @Environment(\.openURL) private var openURL

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(L(R.string.ai_consent_title)).font(KeloFont.titleLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
                    .padding(.top, 20)
                Text(L(R.string.ai_consent_body)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if !ask.need.company.isEmpty { MutedText(L(R.string.ai_consent_now, ask.need.company), small: true) }
                MutedText(L(R.string.ai_consent_not_now_note), small: true)
                LinkButton(L(R.string.ai_consent_privacy)) { if let u = URL(string: ask.need.privacyUrl) { openURL(u) } }
                    .accessibilityIdentifier("kelo.consent.privacy")
                if let error { ErrorText(error, small: true) }
                VStack(spacing: 10) {
                    PrimaryButton(L(R.string.ai_consent_agree), busy: busy, action: onAgree)
                        .accessibilityIdentifier("kelo.consent.agree")
                    SecondaryButton(L(R.string.ai_consent_not_now), enabled: !busy, fill: true, action: onNotNow)
                        .accessibilityIdentifier("kelo.consent.notNow")
                }
                .padding(.top, 6)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20).padding(.bottom, 16)
        }
        .scrollBounceBehavior(.basedOnSize)
        .interactiveDismissDisabled(busy)
    }
}

/// Puts `ConsentSheet` over the chat while `model.consentAsk` is set (its own modifier keeps ChatScreen's body quick to type-check).
struct ConsentSheetHost: ViewModifier {
    let model: ChatModel

    func body(content: Content) -> some View {
        content.background {
            Color.clear.keloSheet(item: Binding(get: { model.consentAsk }, set: { if $0 == nil { model.consentNotNow() } }),
                                  detents: [.medium, .large]) { ask in
                ConsentSheet(ask: ask, busy: model.consentBusy, error: model.consentError,
                             onAgree: model.consentAgree, onNotNow: model.consentNotNow)
            }
        }
    }
}
