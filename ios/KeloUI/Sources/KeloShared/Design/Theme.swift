import SwiftUI

/// Kelo's palette, light and dark (Android ui/theme/Theme.kt, same values). Every text and icon colour comes from here.
public struct KeloColors: Sendable {
    public var bg, card, ink, muted, line, accent, bubble, mine, danger, success: Color
    public var dark: Bool
    public var onAccent, onDanger, onSuccess, warn, onWarn: Color
    public var dangerSoft, dangerInk, successSoft, successInk, infoSoft, infoInk, neutralSoft, neutralInk: Color
    public var disabledInk, disabledFill: Color
    public var media: Color = Color(hex: 0x000000)
    public var onMedia: Color = Color(hex: 0xFFFFFF)
    public var scrim: Color = Color(hex: 0x000000)

    public static let light = KeloColors(
        bg: Color(hex: 0xFBFBFA), card: Color(hex: 0xFFFFFF), ink: Color(hex: 0x1A1A1A), muted: Color(hex: 0x6B6B71),
        line: Color(hex: 0xE4E4E7), accent: Color(hex: 0x4F52E0), bubble: Color(hex: 0xECEEF0), mine: Color(hex: 0xDBE4FF),
        danger: Color(hex: 0xC62828), success: Color(hex: 0x237A47), dark: false,
        onAccent: Color(hex: 0xFFFFFF), onDanger: Color(hex: 0xFFFFFF), onSuccess: Color(hex: 0xFFFFFF), warn: Color(hex: 0x8F5A00),
        onWarn: Color(hex: 0xFFFFFF),
        dangerSoft: Color(hex: 0xFDE8E8), dangerInk: Color(hex: 0xA51111), successSoft: Color(hex: 0xDFF5E6), successInk: Color(hex: 0x1B6B3C),
        infoSoft: Color(hex: 0xE3ECFF), infoInk: Color(hex: 0x3B4CCF), neutralSoft: Color(hex: 0xEEF2F6), neutralInk: Color(hex: 0x3C4A5C),
        disabledInk: Color(hex: 0x7C7C82), disabledFill: Color(hex: 0xEAEAEC))

    public static let darkPalette = KeloColors(
        bg: Color(hex: 0x141416), card: Color(hex: 0x1F1F23), ink: Color(hex: 0xF2F2F3), muted: Color(hex: 0x9A9AA1),
        line: Color(hex: 0x34343A), accent: Color(hex: 0x8C8FF7), bubble: Color(hex: 0x26262B), mine: Color(hex: 0x343A6B),
        danger: Color(hex: 0xFF6B6B), success: Color(hex: 0x4CC17F), dark: true,
        onAccent: Color(hex: 0x111114), onDanger: Color(hex: 0x111114), onSuccess: Color(hex: 0x111114), warn: Color(hex: 0xE0A84A),
        onWarn: Color(hex: 0x111114),
        dangerSoft: Color(hex: 0x3B1D1D), dangerInk: Color(hex: 0xF5A5A5), successSoft: Color(hex: 0x1D3328), successInk: Color(hex: 0xA9DCBF),
        infoSoft: Color(hex: 0x263056), infoInk: Color(hex: 0xC7D0FF), neutralSoft: Color(hex: 0x262B33), neutralInk: Color(hex: 0xC3CAD4),
        disabledInk: Color(hex: 0x7A7A82), disabledFill: Color(hex: 0x2A2A2F))

    public static func of(_ scheme: ColorScheme) -> KeloColors { scheme == .dark ? darkPalette : light }

    /// The avatar gradient (the same in both themes) and its letter.
    public static let avatarGradient = [Color(hex: 0x5A4FE0), Color(hex: 0x7C4DEB)]
    public static let avatarInk = Color.white
    /// The letters on a service's coloured tile.
    public static let tileInk = Color.white
    /// The cream disc behind the character in the header badge (the launcher icon's background, light in both themes).
    public static let badgeCream = Color(hex: 0xFFF3D6)
}

public extension Color {
    /// 0xRRGGBB, opaque.
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: alpha)
    }
}

private struct KeloColorsKey: EnvironmentKey {
    static let defaultValue = KeloColors.light
}

public extension EnvironmentValues {
    /// The palette for the current colour scheme (set by `.keloTheme()` at the root).
    var kelo: KeloColors {
        get { self[KeloColorsKey.self] }
        set { self[KeloColorsKey.self] = newValue }
    }
}

/// Type sizes (Android KeloTypography), mapped to Dynamic Type styles so they grow with the phone's text size.
public enum KeloFont {
    /// 28 bold: page titles
    public static let display = Font.system(.title, weight: .bold)
    /// 26 bold: first-run titles
    public static let headline = Font.system(.title, weight: .bold)
    /// 20 semibold: top bars
    public static let titleLarge = Font.system(.title3, weight: .semibold)
    /// 16 semibold
    public static let titleMedium = Font.system(.callout, weight: .semibold)
    /// 14 semibold: section heads, the agent's name
    public static let titleSmall = Font.system(.subheadline, weight: .semibold)
    /// 16
    public static let bodyLarge = Font.system(.callout)
    /// 15
    public static let body = Font.system(.subheadline)
    /// 13
    public static let small = Font.system(.footnote)
    /// 16 semibold: buttons
    public static let button = Font.system(.callout, weight: .semibold)
    /// 11: tab labels and tiny tags
    public static let label = Font.system(.caption2)
    /// 12
    public static let caption = Font.system(.caption)
}

/// The app's motion (Android ui/theme/Motion.kt, Material 3 tokens). With Reduce Motion every animation is nil.
public enum KeloMotionSpec {
    public static func enter(_ reduce: Bool) -> Animation? { reduce ? nil : .timingCurve(0.05, 0.7, 0.1, 1, duration: 0.4) }
    public static func exit(_ reduce: Bool) -> Animation? { reduce ? nil : .timingCurve(0.3, 0, 0.8, 0.15, duration: 0.2) }
    public static func standard(_ reduce: Bool) -> Animation? { reduce ? nil : .timingCurve(0.2, 0, 0, 1, duration: 0.3) }
    public static func small(_ reduce: Bool) -> Animation? { reduce ? nil : .timingCurve(0.2, 0, 0, 1, duration: 0.15) }
    public static let pressShrink: CGFloat = 0.96
}

/// Puts the palette, the app's language (locale, layout direction) into the environment. Apply once at the root.
public struct KeloThemeModifier: ViewModifier {
    @Environment(\.colorScheme) private var scheme
    let strings: Strings

    public func body(content: Content) -> some View {
        content
            .environment(\.kelo, KeloColors.of(scheme))
            .environment(\.locale, strings.locale)
            .environment(\.layoutDirection, strings.isRightToLeft ? .rightToLeft : .leftToRight)
            .tint(KeloColors.of(scheme).accent)
    }
}

public extension View {
    @MainActor func keloTheme() -> some View { modifier(KeloThemeModifier(strings: Strings.shared)) }
    @MainActor func keloTheme(_ strings: Strings) -> some View { modifier(KeloThemeModifier(strings: strings)) }
}
