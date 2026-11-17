import SwiftUI

/// How wide the window is, in points (the Kotlin side's WindowClass: the same numbers and test vectors). Decided from the window,
/// never from the device: an iPad in Split View or Slide Over is narrow; an unfolded foldable is wide.
public enum WidthClass: Int, Comparable, Sendable {
    case narrow, middle, wide, roomy
    public static func < (a: WidthClass, b: WidthClass) -> Bool { a.rawValue < b.rawValue }

    /// narrow < 600, middle 600-839, wide 840-1199, roomy >= 1200.
    public static func of(width: CGFloat) -> WidthClass {
        if width >= 1200 { return .roomy }
        if width >= 840 { return .wide }
        if width >= 600 { return .middle }
        return .narrow
    }
}

/// The window's size and what the layout does with it. Narrow = today's phone layout, untouched.
public struct WindowShape: Equatable, Sendable {
    public var width: CGFloat
    public var height: CGFloat
    public init(width: CGFloat = 0, height: CGFloat = 0) { self.width = width; self.height = height }

    public var widthClass: WidthClass { WidthClass.of(width: width) }
    /// Two panes from 840 wide, and only when the window is at least 480 high (a phone turned on its side stays one pane).
    public var twoPanes: Bool { widthClass >= .wide && height >= Self.minPaneHeight }
    /// Pages sit in a centred column from 600 wide.
    public var centredColumn: Bool { widthClass >= .middle }
    /// The chat's column: 720 in the middle class, 860 from wide; nil = the whole width (narrow).
    public var chatColumnMax: CGFloat? {
        switch widthClass {
        case .narrow: nil
        case .middle: 720
        case .wide, .roomy: 860
        }
    }
    /// Other pages (lists, settings) are a centred column of at most 640 from 600 wide (Kotlin PAGE_COLUMN); nil = the window's width.
    public var pageColumnMax: CGFloat? { centredColumn ? Self.pageColumn : nil }
    public static let pageColumn: CGFloat = 640
    /// A chat bubble is at most 540 wide once the window is 600 or more; nil = the usual (narrow).
    public var bubbleMax: CGFloat? { centredColumn ? 540 : nil }
    /// The tab bar is capped at 560 and centred whenever the window is not narrow (Kotlin TAB_BAR_MAX).
    public var tabBarMax: CGFloat? { centredColumn ? 560 : nil }

    /// What the second pane shows: the live view when one is open, else the Work list; only on the chat tab in a two-pane window.
    public enum Side: Equatable, Sendable { case none, live, work }
    public func side(onChatTab: Bool, liveOpen: Bool) -> Side {
        !twoPanes || !onChatTab ? .none : (liveOpen ? .live : .work)
    }

    public static let minPaneHeight: CGFloat = 480
}

/// The divider between the two panes: where it starts (55 / 45), how far it can go (320 pt each side), what a drag or a keyboard
/// step does, and how it is kept per device.
public enum SplitRules {
    public static let defaultRatio: CGFloat = 0.55
    public static let minPane: CGFloat = 320
    public static let handle: CGFloat = 16
    public static let step: CGFloat = 0.05
    static let key = "ui.split_permille"

    /// The first pane's share of `total`, kept so that both panes are at least `minPane` wide; a window too narrow for two
    /// minimums keeps the default share (Kotlin firstShare).
    public static func clamp(_ ratio: CGFloat, total: CGFloat) -> CGFloat {
        guard total >= minPane * 2 else { return defaultRatio }
        return min(max(ratio, minPane / total), 1 - minPane / total)
    }

    /// The share after the divider moved to `wantedFirst` from the left edge.
    public static func firstShare(wantedFirst: CGFloat, total: CGFloat) -> CGFloat {
        total > 0 ? clamp(wantedFirst / total, total: total) : defaultRatio
    }

    /// The first pane's width.
    public static func leftWidth(total: CGFloat, ratio: CGFloat) -> CGFloat { (total * clamp(ratio, total: total)).rounded() }

    /// A drag that ended at `x` from the window's left edge.
    public static func ratio(dragX x: CGFloat, total: CGFloat) -> CGFloat { firstShare(wantedFirst: x, total: total) }

    public static func stored(_ prefs: Prefs = .shared) -> CGFloat {
        let m = prefs.int(key, default: Int(defaultRatio * 1000))
        return m > 0 && m < 1000 ? CGFloat(m) / 1000 : defaultRatio
    }
    public static func store(_ ratio: CGFloat, _ prefs: Prefs = .shared) { prefs.set(key, Int((ratio * 1000).rounded())) }
}

/// What the shell hands to the tabs: the window, and how wide the first pane is (nil = one pane).
public struct PaneLayout: Equatable, Sendable {
    public var left: CGFloat?
    public init(left: CGFloat? = nil) { self.left = left }
}

private struct WindowShapeKey: EnvironmentKey { static let defaultValue = WindowShape() }
private struct PaneLayoutKey: EnvironmentKey { static let defaultValue = PaneLayout() }

public extension EnvironmentValues {
    var keloWindow: WindowShape {
        get { self[WindowShapeKey.self] }
        set { self[WindowShapeKey.self] = newValue }
    }
    var keloPanes: PaneLayout {
        get { self[PaneLayoutKey.self] }
        set { self[PaneLayoutKey.self] = newValue }
    }
}

/// The first pane, and the second when the window has two. The first view is always the same child of the same stack, so a layout
/// change (a fold, a rotation, Split View) keeps its state: what was typed, the scroll position.
public struct TwoPaneRow<Left: View, Right: View>: View {
    let left: Left
    let right: Right
    @Environment(\.keloPanes) private var panes
    @Environment(\.keloShellInsets) private var insets
    @Environment(\.kelo) private var c

    public init(@ViewBuilder left: () -> Left, @ViewBuilder right: () -> Right) { self.left = left(); self.right = right() }

    public var body: some View {
        HStack(spacing: 0) {
            if let w = panes.left {
                left.frame(width: w)
                Rectangle().fill(c.line).frame(width: 1).ignoresSafeArea()
                // the second pane has no floating header; it keeps clear of the tab bar
                right
                    .environment(\.keloShellInsets, ShellInsets(top: 0, bottom: 0))
                    .safeAreaInset(edge: .bottom, spacing: 0) { Color.clear.frame(height: insets.bottom).allowsHitTesting(false) }
                    .background(c.bg.ignoresSafeArea())
                    .frame(maxWidth: .infinity)
            } else {
                left
            }
        }
    }
}

/// A tab whose pages push on a stack, as a list with its detail beside it when the window has two panes: the list on the left (its
/// rows still push onto the tab's path in the router), the pushed pages on the right. One pane: the plain stack.
public struct ListDetailStack<Route: Hashable, Root: View, Detail: View>: View {
    let tab: KeloTab
    let root: Root
    let detail: (Route) -> Detail
    @Environment(AppModel.self) private var app
    @Environment(\.keloPanes) private var panes

    public init(tab: KeloTab, @ViewBuilder root: () -> Root, @ViewBuilder detail: @escaping (Route) -> Detail) {
        self.tab = tab; self.root = root(); self.detail = detail
    }

    public var body: some View {
        let two = panes.left != nil
        TwoPaneRow(left: {
            NavigationStack(path: two ? .constant(NavigationPath()) : app.router.path(tab)) {
                root.navigationDestination(for: Route.self, destination: detail)
            }
        }, right: {
            NavigationStack(path: app.router.path(tab)) {
                Color.clear.navigationDestination(for: Route.self, destination: detail).keloHideNavigationBar()
            }
        })
    }
}

public extension View {
    /// A chat bubble at most `max` wide (nil = as it is: the phone's layout is not touched).
    @ViewBuilder func keloBubbleCap(_ max: CGFloat?, alignment: Alignment) -> some View {
        if let max { frame(maxWidth: max, alignment: alignment) } else { self }
    }
}
