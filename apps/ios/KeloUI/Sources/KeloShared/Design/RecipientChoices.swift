import SwiftUI
import KeloCore

/// The kind of data in the user's words (the server names the kind; a kind this app does not know reads as "data").
@MainActor public func trailKindWord(_ kind: String) -> String {
    switch RecipientRules.knownKind(kind) {
    case "mail": L(R.string.trail_kind_mail)
    case "file": L(R.string.trail_kind_file)
    case "memory": L(R.string.trail_kind_memory)
    case "contacts": L(R.string.trail_kind_contacts)
    case "calendar": L(R.string.trail_kind_calendar)
    case "photos": L(R.string.trail_kind_photos)
    case "messages": L(R.string.trail_kind_messages)
    case "notes": L(R.string.trail_kind_notes)
    case "vault": L(R.string.trail_kind_vault)
    case "profile": L(R.string.trail_kind_profile)
    case "web": L(R.string.trail_kind_web)
    case "channel": L(R.string.trail_kind_channel)
    case "device": L(R.string.trail_kind_device)
    case "connector": L(R.string.trail_kind_connector)
    default: L(R.string.trail_kind_other)
    }
}

/// The recipient as the user reads it; the public link has its own words.
@MainActor public func trailRecipientWord(_ recipient: String) -> String {
    RecipientRules.shown(recipient).map { TokenText.fit($0, max: 40) } ?? L(R.string.trail_recipient_link)
}

/// What the data-trail card says: which kind of data goes to which recipient, with the titles of the items (never their content).
public struct RecipientOffers: View {
    let offers: [TrailOffer]
    @Environment(\.kelo) private var c

    public init(offers: [TrailOffer]) { self.offers = offers }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(offers.enumerated()), id: \.offset) { _, o in
                VStack(alignment: .leading, spacing: 2) {
                    Text(L(R.string.trail_goes_to, trailKindWord(o.kind), trailRecipientWord(o.recipient)))
                        .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    if !o.titles.isEmpty {
                        Text(o.titles.map { TokenText.fit($0, max: 40) }.joined(separator: " · "))
                            .font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
    }
}

/// The data-trail card's body and its three answers, one under the other so a long label in any language fits: Allow once,
/// Always for this recipient, Deny.
public struct RecipientChoices: View {
    let offers: [TrailOffer]
    let enabled: Bool
    let onAnswer: (RecipientRules.Answer) -> Void

    public init(offers: [TrailOffer], enabled: Bool = true, onAnswer: @escaping (RecipientRules.Answer) -> Void) {
        self.offers = offers; self.enabled = enabled; self.onAnswer = onAnswer
    }

    public var body: some View {
        VStack(spacing: 10) {
            RecipientOffers(offers: offers)
            PrimaryButton(L(R.string.trail_allow_once), enabled: enabled) { onAnswer(.once) }
            SecondaryButton(L(R.string.trail_allow_always), enabled: enabled, fill: true) { onAnswer(.always) }
            SecondaryButton(L(R.string.chat_approval_deny), enabled: enabled, fill: true) { onAnswer(.deny) }
        }
    }
}
