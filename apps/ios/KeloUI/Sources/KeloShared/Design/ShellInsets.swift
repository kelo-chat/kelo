import SwiftUI

/// How much of a tab's start page the floating header (top) and the tab bar (bottom) cover (Android LocalHeaderHeight /
/// LocalBottomBarHeight). The shell measures both and hands them down; each tab's start page applies them inside its own
/// NavigationStack with `keloShellInsets()`, because a safe-area inset set outside a NavigationStack does not reach the pages in
/// it on iOS. The page still scrolls underneath the header and the bar, as on Android.
public struct ShellInsets: Equatable, Sendable {
    public var top: CGFloat
    public var bottom: CGFloat
    public init(top: CGFloat = 0, bottom: CGFloat = 0) { self.top = top; self.bottom = bottom }
}

private struct ShellInsetsKey: EnvironmentKey {
    static let defaultValue = ShellInsets()
}

public extension EnvironmentValues {
    var keloShellInsets: ShellInsets {
        get { self[ShellInsetsKey.self] }
        set { self[ShellInsetsKey.self] = newValue }
    }
}

private struct ShellInsetsModifier: ViewModifier {
    @Environment(\.keloShellInsets) private var insets
    func body(content: Content) -> some View {
        content
            .safeAreaInset(edge: .top, spacing: 0) { Color.clear.frame(height: insets.top).allowsHitTesting(false) }
            .safeAreaInset(edge: .bottom, spacing: 0) { Color.clear.frame(height: insets.bottom).allowsHitTesting(false) }
    }
}

public extension View {
    /// Keeps a tab's start page clear of the floating header and the tab bar (see `ShellInsets`).
    func keloShellInsets() -> some View { modifier(ShellInsetsModifier()) }
}
