import SwiftUI
import KeloCore
import KeloShared

public enum ComparisonRules {
    public static func choice(_ option: ComparisonOption, fallback: (String) -> String) -> String {
        let s = option.says.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? fallback(option.name) : option.says
    }
}

public struct ComparisonCardView: View {
    public let card: ComparisonCard
    public let onChoose: (String) -> Void
    @Environment(\.kelo) private var c
    // fixed heights (2 lines at most per cell) keep the label rows and the option rows level
    private static let headerH: CGFloat = 52
    private static let cellH: CGFloat = 52

    public init(card: ComparisonCard, onChoose: @escaping (String) -> Void) {
        self.card = card
        self.onChoose = onChoose
    }

    public var body: some View {
        CardBox {
            if !card.title.isEmpty {
                Text(card.title)
                    .font(KeloFont.titleMedium.weight(.bold))
                    .foregroundStyle(c.ink)
            }
            if !card.basis.isEmpty {
                Text(card.basis)
                    .font(KeloFont.small)
                    .foregroundStyle(c.muted)
            }

            table

            if !card.note.isEmpty {
                Text(card.note)
                    .font(KeloFont.small)
                    .foregroundStyle(c.muted)
            }

            if card.open && !card.options.isEmpty {
                buttons
            }
        }
    }

    private var table: some View {
        let scrollOptions = card.options.count > 2
        return HStack(alignment: .top, spacing: 8) {
            // Label column fixed on left
            VStack(alignment: .leading, spacing: 0) {
                Color.clear.frame(height: Self.headerH)
                ForEach(Array(card.rows.enumerated()), id: \.offset) { _, row in
                    Text(row)
                        .font(KeloFont.small)
                        .foregroundStyle(c.muted)
                        .lineLimit(2)
                        .padding(.vertical, 4)
                        .frame(maxWidth: .infinity, minHeight: Self.cellH, maxHeight: Self.cellH, alignment: .leading)
                }
            }
            .padding(.vertical, 6)   // the same as the option columns, so the rows stay level
            .frame(width: 80)

            // Option columns
            if scrollOptions {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 8) {
                        ForEach(Array(card.options.enumerated()), id: \.offset) { _, opt in
                            optionColumn(opt, minWidth: 96)
                        }
                    }
                }
            } else {
                HStack(alignment: .top, spacing: 8) {
                    ForEach(Array(card.options.enumerated()), id: \.offset) { _, opt in
                        optionColumn(opt, minWidth: nil)
                            .frame(maxWidth: .infinity)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func optionColumn(_ opt: ComparisonOption, minWidth: CGFloat?) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                if opt.recommended {
                    Text(L(R.string.comparison_recommended))
                        .font(KeloFont.label)
                        .foregroundStyle(c.accent)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(RoundedRectangle(cornerRadius: 4).fill(c.accent.opacity(0.12)))
                }
                Text(opt.name)
                    .font(KeloFont.body.weight(.bold))
                    .foregroundStyle(c.ink)
                    .lineLimit(opt.recommended ? 1 : 2)
            }
            .frame(maxWidth: .infinity, minHeight: Self.headerH, maxHeight: Self.headerH, alignment: .bottomLeading)

            ForEach(Array(card.rows.indices), id: \.self) { r in
                Text(r < opt.values.count ? opt.values[r] : "")
                    .font(KeloFont.body)
                    .foregroundStyle(c.ink)
                    .lineLimit(2)
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity, minHeight: Self.cellH, maxHeight: Self.cellH, alignment: .leading)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .frame(minWidth: minWidth)
        .overlay(
            Group {
                if opt.recommended {
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(c.accent, lineWidth: 1)
                }
            }
        )
    }

    private var buttons: some View {
        FlowLayout(spacing: 8, lineSpacing: 8) {
            ForEach(Array(card.options.enumerated()), id: \.offset) { _, opt in
                let label = L(R.string.comparison_choose, opt.name)
                let action = {
                    let text = ComparisonRules.choice(opt) { name in
                        L(R.string.comparison_choose_say, name)
                    }
                    onChoose(text)
                }
                if opt.recommended {
                    PrimaryButton(label, action: action)
                } else {
                    SecondaryButton(label, fill: false, action: action)
                }
            }
        }
    }
}
