import SwiftUI
#if canImport(UIKit)
import UIKit

/// Pages hide the system navigation bar and draw their own back bar, which switches off the navigation controller's edge-swipe back.
/// This gives the gesture back: its delegate says yes while there is a page to go back to, and no while a page that must not be left
/// half-way (`keloSwipeBackBlocked`) is on screen.
final class SwipeBackDelegate: NSObject, UIGestureRecognizerDelegate {
    static let shared = SwipeBackDelegate()
    nonisolated(unsafe) static var blockedBy = Set<ObjectIdentifier>()

    func gestureRecognizerShouldBegin(_ g: UIGestureRecognizer) -> Bool {
        guard let nav = Self.nav(of: g) else { return false }
        return nav.viewControllers.count > 1 && !nav.transitionCoordinatorIsBusy && Self.blockedBy.isEmpty
    }

    func gestureRecognizer(_ g: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { false }

    private static func nav(of g: UIGestureRecognizer) -> UINavigationController? {
        var v: UIView? = g.view
        while let x = v { if let n = x.next as? UINavigationController { return n }; v = x.superview }
        return nil
    }
}

private extension UINavigationController {
    var transitionCoordinatorIsBusy: Bool { transitionCoordinator != nil }
}

/// Finds the page's navigation controller (up the responder chain) and installs the delegate; optionally blocks the swipe while the
/// page is on screen.
private struct SwipeBackHook: UIViewRepresentable {
    let blocks: Bool
    func makeUIView(context: Context) -> Hook { Hook(blocks: blocks) }
    func updateUIView(_ v: Hook, context: Context) { v.blocks = blocks; v.apply() }

    final class Hook: UIView {
        var blocks: Bool
        init(blocks: Bool) { self.blocks = blocks; super.init(frame: .zero); isUserInteractionEnabled = false;  }
        required init?(coder: NSCoder) { fatalError() }
        override func didMoveToWindow() { super.didMoveToWindow(); apply() }
        override func layoutSubviews() { super.layoutSubviews(); apply() }
        deinit { SwipeBackDelegate.blockedBy.remove(ObjectIdentifier(self)) }

        private var nav: UINavigationController? {
            var r: UIResponder? = self
            while let x = r { if let n = x as? UINavigationController { return n }; r = x.next }
            return nil
        }

        func apply() {
            if blocks, window != nil { SwipeBackDelegate.blockedBy.insert(ObjectIdentifier(self)) }
            else { SwipeBackDelegate.blockedBy.remove(ObjectIdentifier(self)) }
            guard window != nil, let g = nav?.interactivePopGestureRecognizer else { return }
            if g.delegate !== SwipeBackDelegate.shared { g.delegate = SwipeBackDelegate.shared }
            g.isEnabled = true
        }
    }
}
#endif

public extension View {
    /// Brings back the edge swipe that goes to the previous page (lost with the hidden system bar).
    func keloSwipeBack() -> some View {
        #if canImport(UIKit)
        return background(SwipeBackHook(blocks: false).frame(width: 0, height: 0))
        #else
        return self
        #endif
    }

    /// A page that must not be left by the edge swipe (a code shown once, a step that has to be finished).
    func keloSwipeBackBlocked() -> some View {
        #if canImport(UIKit)
        return background(SwipeBackHook(blocks: true).frame(width: 0, height: 0))
        #else
        return self
        #endif
    }
}
