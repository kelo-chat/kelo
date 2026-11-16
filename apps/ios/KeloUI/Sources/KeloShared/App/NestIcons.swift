import SwiftUI

/// The icon names a Nest screen may use (docs/nest-ui.md, "Icons"), each drawn with an SF Symbol of the same meaning (Android
/// shell/NestIcons.kt). Anything else is a `star`.
public enum NestIcons {
    /// Every name of the schema, in the document's order.
    public static let names: [String] = (
        "home star heart check check_circle list calendar clock alarm bell box truck cart bag wallet card chart bar_chart book note pen camera " +
        "image music video map pin globe sun cloud moon fire drop leaf bolt flag tag gift trophy target person people chat mail phone link " +
        "search settings info warning plus refresh trash edit play lock key sparkle food coffee fitness pill plane car school work money weather " +
        "news game"
    ).split(separator: " ").map(String.init)

    private static let symbols: [String: String] = [
        "home": "house", "star": "star", "heart": "heart", "check": "checkmark", "check_circle": "checkmark.circle", "list": "list.bullet",
        "calendar": "calendar", "clock": "clock", "alarm": "alarm", "bell": "bell", "box": "shippingbox", "truck": "box.truck", "cart": "cart",
        "bag": "bag", "wallet": "wallet.pass", "card": "creditcard", "chart": "chart.line.uptrend.xyaxis", "bar_chart": "chart.bar",
        "book": "book", "note": "doc.text", "pen": "pencil", "camera": "camera", "image": "photo", "music": "music.note", "video": "video",
        "map": "map", "pin": "mappin", "globe": "globe", "sun": "sun.max", "cloud": "cloud", "moon": "moon", "fire": "flame", "drop": "drop",
        "leaf": "leaf", "bolt": "bolt", "flag": "flag", "tag": "tag", "gift": "gift", "trophy": "trophy", "target": "target",
        "person": "person", "people": "person.2", "chat": "bubble.left", "mail": "envelope", "phone": "phone", "link": "link",
        "search": "magnifyingglass", "settings": "slider.horizontal.3", "info": "info.circle", "warning": "exclamationmark.triangle",
        "plus": "plus", "refresh": "arrow.clockwise", "trash": "trash", "edit": "pencil", "play": "play.fill", "lock": "lock", "key": "key",
        "sparkle": "sparkles", "food": "fork.knife", "coffee": "cup.and.saucer", "fitness": "dumbbell", "pill": "pills", "plane": "airplane",
        "car": "car", "school": "graduationcap", "work": "briefcase", "money": "dollarsign.circle", "weather": "cloud.sun",
        "news": "newspaper", "game": "gamecontroller",
    ]

    /// The SF Symbol of a schema name; a name the schema does not have is a star.
    public static func symbol(_ name: String) -> String { symbols[name] ?? "star" }

    /// True for a name of the schema.
    public static func known(_ name: String) -> Bool { symbols[name] != nil }
}

/// A Nest icon, drawn like the app's own (sized, tinted, hidden from VoiceOver).
public struct NestIconView: View {
    let name: String
    let size: CGFloat
    let tint: Color?
    @Environment(\.kelo) private var c

    public init(_ name: String, size: CGFloat = 22, tint: Color? = nil) { self.name = name; self.size = size; self.tint = tint }

    public var body: some View {
        Image(systemName: NestIcons.symbol(name))
            .font(.system(size: size * 0.82, weight: .regular))
            .frame(width: size, height: size)
            .foregroundStyle(tint ?? c.ink)
            .accessibilityHidden(true)
    }
}

/// An SF Symbol drawn like the app's own icons (a Nest tab's icon in the bar).
public struct NestSymbol: View {
    let name: String
    let size: CGFloat
    let tint: Color
    public init(_ name: String, size: CGFloat, tint: Color) { self.name = name; self.size = size; self.tint = tint }
    public var body: some View {
        Image(systemName: name).font(.system(size: size * 0.82, weight: .regular)).frame(width: size, height: size).foregroundStyle(tint).accessibilityHidden(true)
    }
}
