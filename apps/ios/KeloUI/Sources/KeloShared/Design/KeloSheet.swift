import SwiftUI

/// The app's bottom sheet (Android KeloSheet): opens fully, shows a grab handle, card background, and only closes on a real pull
/// (iOS sheets already need a deliberate drag; a tap outside or the close button closes at once).
public struct KeloSheetModifier<SheetContent: View>: ViewModifier {
    @Binding var isPresented: Bool
    let detents: Set<PresentationDetent>
    let sheet: () -> SheetContent
    @Environment(\.kelo) private var c

    public func body(content: Content) -> some View {
        content.sheet(isPresented: $isPresented) {
            sheet()
                .keloTheme()
                .presentationDetents(detents.keloAdjusted)
                .presentationDragIndicator(.visible)
                .presentationBackground(c.card)
                .presentationCornerRadius(28)
        }
    }
}

/// Where a full sheet stops: under the floating header (the agent's name tag), like Android's sheets, so the agent stays in view.
/// The shell writes the header's measured height; 0 (no header yet) means the system's full height.
public enum KeloSheetMetrics {
    /// Written on the main thread by the shell, read when a sheet is laid out (also on the main thread).
    nonisolated(unsafe) public static var headerHeight: CGFloat = 0
}

/// The "full" detent: the system's largest height less the floating header (Android KeloSheet stops below the name tag).
public struct UnderHeaderDetent: CustomPresentationDetent {
    public static func height(in context: Context) -> CGFloat? {
        let header = KeloSheetMetrics.headerHeight
        // the large detent already starts a little under the status bar; take off the header's own height but keep a
        // useful sheet even on a short screen or at a large text size
        guard header > 0 else { return context.maxDetentValue }
        return max(context.maxDetentValue * 0.6, context.maxDetentValue - header + 8)
    }
}

extension Set where Element == PresentationDetent {
    /// `.large` becomes the under-header height.
    var keloAdjusted: Set<PresentationDetent> {
        guard contains(.large) else { return self }
        var s = self
        s.remove(.large)
        s.insert(.custom(UnderHeaderDetent.self))
        return s
    }
}

public extension View {
    func keloSheet<S: View>(isPresented: Binding<Bool>, detents: Set<PresentationDetent> = [.large],
                            @ViewBuilder content: @escaping () -> S) -> some View {
        modifier(KeloSheetModifier(isPresented: isPresented, detents: detents, sheet: content))
    }

    func keloSheet<Item: Identifiable, S: View>(item: Binding<Item?>, detents: Set<PresentationDetent> = [.large],
                                                @ViewBuilder content: @escaping (Item) -> S) -> some View {
        modifier(KeloItemSheetModifier(item: item, detents: detents, sheet: content))
    }
}

public struct KeloItemSheetModifier<Item: Identifiable, SheetContent: View>: ViewModifier {
    @Binding var item: Item?
    let detents: Set<PresentationDetent>
    let sheet: (Item) -> SheetContent
    @Environment(\.kelo) private var c

    public func body(content: Content) -> some View {
        content.sheet(item: $item) { it in
            sheet(it)
                .keloTheme()
                .presentationDetents(detents.keloAdjusted)
                .presentationDragIndicator(.visible)
                .presentationBackground(c.card)
                .presentationCornerRadius(28)
        }
    }
}

/// A sheet's title row: the title and a close button.
public struct SheetHeader: View {
    let title: String
    let onClose: () -> Void
    @Environment(\.kelo) private var c
    public init(_ title: String, onClose: @escaping () -> Void) { self.title = title; self.onClose = onClose }
    public var body: some View {
        HStack(alignment: .top) {
            Text(title).font(KeloFont.titleLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            Button(action: onClose) { KeloIconView(.close, size: 22, tint: c.muted).frame(width: 36, height: 36) }
                .buttonStyle(.plain)
                .accessibilityLabel(L(R.string.close))
        }
        .padding(.top, 20)
    }
}
