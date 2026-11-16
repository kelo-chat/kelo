import SwiftUI
import KeloCore
import KeloShared

/// A choice question's options as a vertical list of tappable cards (Android chat/ReplyCards.kt): a square picture on the left, the
/// option, its detail line, and a mark on the one Kelo suggests. A tap does what the option's button does (`onPick`); `picked` is the
/// option chosen but not sent yet.
struct ReplyCardList: View {
    let options: [String]
    let cards: [ReplyCard]
    let picked: String?
    let onPick: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(options.enumerated()), id: \.offset) { i, option in
                ReplyCardRow(option: option, card: i < cards.count ? cards[i] : ReplyCard(), selected: picked == option) { onPick(option) }
            }
        }
        .frame(maxWidth: 420, alignment: .leading)
    }
}

private struct ReplyCardRow: View {
    let option: String
    let card: ReplyCard
    let selected: Bool
    let onTap: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let recommended = L(R.string.reply_recommended)
        let said = ReplyCardRules.label(option, card, recommended: recommended)
        let shape = RoundedRectangle(cornerRadius: 12)
        let detail = card.detail.trimmingCharacters(in: .whitespacesAndNewlines)
        Button(action: onTap) {
            HStack(spacing: 12) {
                ReplyCardThumb(image: card.image)
                VStack(alignment: .leading, spacing: 2) {
                    Text(option).font(KeloFont.body).foregroundStyle(c.ink).lineLimit(3)
                    if !detail.isEmpty {
                        Text(detail).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if card.recommended {
                    KeloIconView(.spark, size: 20, tint: c.accent)
                        .accessibilityLabel(recommended)
                }
            }
            .padding(8)
            .background(shape.fill(selected ? c.mine : c.card))
            .overlay(shape.stroke(card.recommended ? c.accent : c.line, lineWidth: 1))
            .contentShape(shape)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(said)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }
}

/// The card's square picture, read with the signed-in API like the chat's pictures (`ChatPicture`); an empty tile until it loads, or
/// when the option has none.
private struct ReplyCardThumb: View {
    let image: ReplyCardImage?
    @Environment(\.kelo) private var c
    @Environment(\.keloApi) private var api
    @State private var model = RemotePictureModel()

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 10)
        ZStack {
            shape.fill(c.bg)
            if let picture = model.picture {
                Image(platformImage: picture).resizable().scaledToFill()
            }
        }
        .frame(width: 80, height: 80)
        .clipShape(shape)
        .overlay(shape.stroke(c.line, lineWidth: 1))
        .accessibilityHidden(true)
        .task(id: image?.url ?? "") {
            guard let url = image?.url, !url.isEmpty else { return }
            await model.load(url, api: api)
        }
    }
}
