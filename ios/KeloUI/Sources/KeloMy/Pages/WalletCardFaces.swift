import SwiftUI
import KeloCore
import KeloShared

// A card's faces: the brand words, the small thumbnail of the list and the full-width face of a card page and of the add form.

/// Brand names are product names, the same in every language.
enum WalletBrand {
    static func short(_ b: CardBrand) -> String {
        switch b {
        case .visa: "VISA"
        case .mastercard: "MC"
        case .amex: "AMEX"
        case .jcb: "JCB"
        case .discover: "DISC"
        case .unionpay: "UP"
        case .diners: "DC"
        case .other: "•••"
        }
    }

    static func name(_ b: CardBrand) -> String? {
        switch b {
        case .visa: "Visa"
        case .mastercard: "Mastercard"
        case .amex: "American Express"
        case .jcb: "JCB"
        case .discover: "Discover"
        case .unionpay: "UnionPay"
        case .diners: "Diners Club"
        case .other: nil
        }
    }
}

private func faceGradient(_ brand: CardBrand) -> LinearGradient {
    let (fg, bg) = CardFaceRules.palette(brand)
    return LinearGradient(colors: [Color(hex: fg & 0xFFFFFF), Color(hex: bg & 0xFFFFFF)], startPoint: .topLeading, endPoint: .bottomTrailing)
}

/// A card's small face: Kelo's own colour pair per brand (no logos) and the last four.
struct WalletCardThumb: View {
    let brand: CardBrand
    let last4: String
    var dimmed = false
    var body: some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(faceGradient(brand))
            .frame(width: 52, height: 34)
            .overlay(alignment: .topLeading) {
                Text(WalletBrand.short(brand)).font(.system(size: 7, weight: .bold)).foregroundStyle(.white).padding(4)
            }
            .overlay(alignment: .bottomTrailing) {
                Text(CardFaceRules.lastFour(last4)).font(.system(size: 8, weight: .semibold, design: .monospaced)).foregroundStyle(.white).padding(4)
            }
            .opacity(dimmed ? 0.5 : 1)
            .accessibilityHidden(true)
    }
}

/// A card's face at full width: the brand, the (masked) number, the holder and the expiry.
struct WalletCardFace: View {
    let brand: CardBrand
    let number: String
    let holder: String
    let expiry: String
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(WalletBrand.name(brand) ?? WalletBrand.short(brand)).font(.system(.subheadline, weight: .bold)).foregroundStyle(.white)
                Spacer()
                KeloIconView(.card, size: 20, tint: .white.opacity(0.85))
            }
            Spacer(minLength: 12)
            Text(number).font(.system(.title3, design: .monospaced).weight(.semibold)).foregroundStyle(.white)
                .lineLimit(1).minimumScaleFactor(0.5)
            Spacer(minLength: 8)
            HStack(alignment: .bottom) {
                Text(holder.uppercased()).font(.system(.caption, weight: .medium)).foregroundStyle(.white.opacity(0.9)).lineLimit(1)
                Spacer(minLength: 8)
                Text(expiry).font(.system(.caption, design: .monospaced)).foregroundStyle(.white.opacity(0.9))
            }
        }
        .padding(18)
        .frame(maxWidth: 360)
        .aspectRatio(1.586, contentMode: .fit)
        .background(RoundedRectangle(cornerRadius: 16).fill(faceGradient(brand)))
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}
