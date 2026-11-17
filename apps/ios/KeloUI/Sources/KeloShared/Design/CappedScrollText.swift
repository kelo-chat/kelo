import SwiftUI

/// A text that grows with its content up to `lines` lines, then scrolls inside its box (a schedule's whole task on an approval card).
public struct CappedScrollText: View {
    public static let defaultLines = 8
    public let text: String
    public let lines: Int
    @ScaledMetric(relativeTo: .body) private var lineHeight: CGFloat = 22
    @State private var contentHeight: CGFloat?
    @Environment(\.kelo) private var c

    public init(_ text: String, lines: Int = CappedScrollText.defaultLines) {
        self.text = text
        self.lines = lines
    }

    private var cap: CGFloat { lineHeight * CGFloat(lines) }

    public var body: some View {
        ScrollView(.vertical, showsIndicators: true) {
            Text(text)
                .font(KeloFont.body)
                .foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GeometryReader { g in Color.clear.preference(key: CappedHeightKey.self, value: g.size.height) })
        }
        .onPreferenceChange(CappedHeightKey.self) { contentHeight = $0 }
        .frame(height: min(contentHeight ?? cap, cap))
    }
}

private struct CappedHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}
