import SwiftUI

// The top of every page follows the iPhone's own default (Messages, Settings): at rest the top area is the page background; once
// content scrolls under it, the system bar material (`.bar`: the blur, and opaque by itself with Reduce Transparency) shows behind
// the status bar and the app's own header, with the thin separator. Nothing is ever drawn behind the clock without that material.
// Android does the same with its tonal surface colour (apps/android).

/// Whether a scroll view is past its rest position.
public enum TopEdgeRules {
    /// `offset` is the scroll view's content offset (y); `topInset` its top content inset (the safe area and a header). At rest the
    /// offset is -topInset, so anything more than half a point below that counts as scrolled.
    public static func scrolled(offset: CGFloat, topInset: CGFloat) -> Bool { offset + topInset > 0.5 }
}

/// Which page is on screen, for the scroll probes: `open` = the tab this page belongs to is showing; `pushed` = a page is pushed on
/// the tab's start page (that start page then steps aside and reports nothing).
public struct TabStance: Equatable, Sendable {
    public var open: Bool
    public var pushed: Bool
    public init(open: Bool = true, pushed: Bool = false) { self.open = open; self.pushed = pushed }
}

/// The scroll state of the pages on screen: scrolled while any reporting scroll view is past its rest position. One per shell
/// (`MainShell` provides it); a page outside the shell (a sheet) reads the shared default.
@Observable public final class TopEdge {
    private var scrollingIds: Set<UUID> = []
    public init() {}

    /// Whether content scrolls under the top area now.
    public var scrolled: Bool { !scrollingIds.isEmpty }

    /// A scroll view reports whether it is scrolled (`on`); a view that goes away reports false.
    public func report(_ id: UUID, _ on: Bool) {
        if on == scrollingIds.contains(id) { return }
        if on { scrollingIds.insert(id) } else { scrollingIds.remove(id) }
    }
}

private struct TopEdgeKey: EnvironmentKey {
    static let defaultValue = TopEdge()
}

private struct TabStanceKey: EnvironmentKey {
    static let defaultValue = TabStance()
}

public extension EnvironmentValues {
    /// The shell's scroll state for the top area.
    var keloTopEdge: TopEdge {
        get { self[TopEdgeKey.self] }
        set { self[TopEdgeKey.self] = newValue }
    }

    /// Whether the tab around this page is showing, and whether a page is pushed on it (set by the shell per tab).
    var keloTabStance: TabStance {
        get { self[TabStanceKey.self] }
        set { self[TabStanceKey.self] = newValue }
    }
}

private struct TopScrollProbe: ViewModifier {
    /// A tab's start page: it reports only while nothing is pushed on it.
    let root: Bool
    @Environment(\.keloTopEdge) private var edge
    @Environment(\.keloTabStance) private var stance
    @State private var id = UUID()
    /// the scroll view is past its rest position (set by the scroll geometry; iOS 17 has none, so it counts as scrolled)
    @State private var moved = false

    func body(content: Content) -> some View {
        let reporting = stance.open && !(root && stance.pushed) && moved
        probe(content)
            .onChange(of: reporting, initial: true) { _, on in edge.report(id, on) }
            .onDisappear { edge.report(id, false) }
    }

    @ViewBuilder private func probe<V: View>(_ content: V) -> some View {
        if #available(iOS 18.0, macOS 15.0, *) {
            content.onScrollGeometryChange(for: Bool.self, of: { g in
                TopEdgeRules.scrolled(offset: g.contentOffset.y, topInset: g.contentInsets.top)
            }, action: { _, past in moved = past })
        } else {
            content.onAppear { moved = true }
        }
    }
}

public extension View {
    /// Reports this scroll view to the top edge (see `TopEdge`). `root`: a tab's start page, which reports nothing while a page is
    /// pushed on it. Put it on the `ScrollView` or `List` itself.
    func keloTopScroll(root: Bool = false) -> some View { modifier(TopScrollProbe(root: root)) }
}

/// The fill of the top area: the page colour at rest; once content scrolls under it, the system bar material, with the thin
/// separator when `rule` is on. Used as the background of a page's own top bar and by `TopEdgeBand`.
public struct TopEdgeFill: View {
    let rule: Bool
    @Environment(\.kelo) private var c
    @Environment(\.keloTopEdge) private var edge
    @Environment(\.accessibilityReduceMotion) private var reduce

    public init(rule: Bool = false) { self.rule = rule }

    public var body: some View {
        let on = edge.scrolled
        ZStack(alignment: .bottom) {
            c.bg
            Rectangle().fill(.bar).opacity(on ? 1 : 0)
            if rule { Hairline().opacity(on ? 1 : 0) }
        }
        .animation(KeloMotionSpec.small(reduce), value: on)
        .accessibilityHidden(true)
    }
}

/// The band over the top of the shell's pages: the status bar, and the floating header's area when `below` (the header's height)
/// is more than 0. It covers the top of every tab and every page pushed on a tab, and takes no touches.
public struct TopEdgeBand: View {
    /// the part under the status bar that the band covers too (0 when no header shows)
    let below: CGFloat

    public init(below: CGFloat) { self.below = below }

    public var body: some View {
        GeometryReader { g in
            VStack(spacing: 0) {
                TopEdgeFill(rule: below > 0)
                    .frame(height: g.safeAreaInsets.top + below)
                Spacer(minLength: 0)
            }
            .ignoresSafeArea(.container, edges: .top)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
