import SwiftUI
import KeloShared

/// The matching "/" commands in a small card above the composer; a tap puts the command in the field.
struct SlashMenu: View {
    let commands: [SlashCommands.Command]
    let onPick: (SlashCommands.Command) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(spacing: 0) {
            ForEach(commands, id: \.word) { cmd in
                Button { onPick(cmd) } label: {
                    HStack(spacing: 12) {
                        Text(cmd.word).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.ink)
                        Text(L(cmd.about)).font(KeloFont.small).foregroundStyle(c.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.horizontal, 16).padding(.vertical, 12)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("kelo.slash.\(cmd.word.dropFirst())")
            }
        }
        .padding(.vertical, 4)
        .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.line, lineWidth: 1))
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
    }
}
