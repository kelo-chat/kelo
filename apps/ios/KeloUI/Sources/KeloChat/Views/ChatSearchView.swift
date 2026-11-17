import SwiftUI
import KeloCore
import KeloShared

/// The magnifier at the end of the shell's header, shown on the chat tab (Android ShellParts: the chat's search).
public struct ChatSearchButton: View {
    let action: () -> Void
    @Environment(\.kelo) private var c

    public init(action: @escaping () -> Void) { self.action = action }

    public var body: some View {
        Button(action: action) {
            KeloIconView(.search, size: 22, tint: c.ink).frame(width: 44, height: 44).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L(R.string.chat_search_open))
        .accessibilityIdentifier("kelo.chatSearchButton")
    }
}

/// The search screen over the chat (Android ChatSearchScreen): the field is focused when it opens; each result shows who said it
/// (You, or the agent), when, and its snippet with the match in bold. The list pages by `before` near its end. A tap opens that
/// message in the chat (`onOpen`), which closes this screen.
struct ChatSearchView: View {
    @State private var model: ChatSearchModel
    @State private var wantFocus = false
    let agentName: String
    let onOpen: (Int64) -> Void
    let onBack: () -> Void
    @Environment(\.kelo) private var c

    init(api: @escaping () -> KeloApi?, agentName: String, onOpen: @escaping (Int64) -> Void, onBack: @escaping () -> Void) {
        _model = State(initialValue: ChatSearchModel(api: api))
        self.agentName = agentName
        self.onOpen = onOpen
        self.onBack = onBack
    }

    var body: some View {
        SubPage(L(R.string.chat_search_title), onBack: onBack) {
            field
            results
        }
        .onAppear { wantFocus = true }
        .accessibilityIdentifier("kelo.chatSearch")
    }

    /// The field: the search runs once typing pauses (Search runs it at once); a clear button empties it.
    private var field: some View {
        KeloTextField(L(R.string.chat_search_field), text: Binding(get: { model.query }, set: { model.setQuery($0) }),
                      trailingInset: model.query.isEmpty ? 0 : 36, focusRequest: wantFocus, onSubmit: { model.submit() })
            .overlay(alignment: .trailing) {
                if !model.query.isEmpty {
                    Button { model.setQuery("") } label: {
                        KeloIconView(.close, size: 14, tint: c.muted).frame(width: 36, height: 44).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(L(R.string.chat_search_clear))
                }
            }
            .accessibilityIdentifier("kelo.chatSearch.field")
    }

    @ViewBuilder private var results: some View {
        if !model.hasQuery {
            hint(L(R.string.chat_search_hint))
        } else if model.hits.isEmpty {
            if model.failed { hint(L(R.string.chat_search_failed)) }
            else if !model.loading { hint(L(R.string.chat_search_none)) }
        } else {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(model.hits) { hit in
                    Button { onOpen(hit.id) } label: { row(hit) }
                        .buttonStyle(.plain)
                        .onAppear { if hit.id == model.hits.last?.id { model.loadMore() } }
                }
                if model.failed { hint(L(R.string.chat_search_failed)) }
                if model.loading { ProgressView().frame(maxWidth: .infinity).padding(.vertical, 12) }
            }
        }
    }

    private func hint(_ text: String) -> some View {
        Text(text)
            .font(KeloFont.body).foregroundStyle(c.muted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity).padding(.horizontal, 24).padding(.vertical, 32)
    }

    private func row(_ hit: MessageHit) -> some View {
        ChatSearchRow(hit: hit, who: MessageSearchRules.isMine(hit.role) ? L(R.string.chat_search_you) : agentName)
    }
}

/// One result: who said it and when (muted), then the snippet with the match in bold accent. Wraps its text inside the width it is given.
struct ChatSearchRow: View {
    let hit: MessageHit
    let who: String
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(who)
                    .font(KeloFont.caption).fontWeight(.medium).foregroundStyle(c.muted)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Text(TimeLabels.label(Int64(hit.createdAt * 1000), locale: appLocale(),
                                      today: L(R.string.chat_time_today), yesterday: L(R.string.chat_time_yesterday)))
                    .font(KeloFont.caption).foregroundStyle(c.muted)
                    .lineLimit(1)
            }
            Text(Self.snippet(hit.snippet, accent: c.accent, ink: c.ink))
                .font(KeloFont.body)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    /// The snippet with its matched words in bold and the accent colour (the « » marks are never shown).
    static func snippet(_ text: String, accent: Color, ink: Color) -> AttributedString {
        var out = AttributedString()
        for part in MessageSearchRules.parts(text) {
            var s = AttributedString(part.text)
            s.foregroundColor = part.matched ? accent : ink
            if part.matched { s.inlinePresentationIntent = .stronglyEmphasized }
            out += s
        }
        return out
    }
}
