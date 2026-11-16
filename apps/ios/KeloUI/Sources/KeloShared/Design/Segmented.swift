import SwiftUI

/// Two or more choices side by side (.segmented).
public struct Segmented: View {
    let options: [String]
    let selected: Int
    let onSelect: (Int) -> Void
    @Environment(\.kelo) private var c

    public init(options: [String], selected: Int, onSelect: @escaping (Int) -> Void) {
        self.options = options; self.selected = selected; self.onSelect = onSelect
    }

    public var body: some View {
        HStack(spacing: 0) {
            ForEach(options.indices, id: \.self) { i in
                Button { onSelect(i) } label: {
                    Text(options[i]).font(i == selected ? KeloFont.titleSmall : KeloFont.body).foregroundStyle(c.ink)
                        .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity).padding(.vertical, 10).padding(.horizontal, 4)
                        .background(RoundedRectangle(cornerRadius: 11).fill(i == selected ? c.card : c.bubble))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(i == selected ? [.isSelected, .isButton] : .isButton)
            }
        }
        .padding(3)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.bubble))
    }
}
