import SwiftUI
import KeloCore
import KeloShared

/// The long-press menu of a bubble (Android ActionBubble): 복사, 공유, 텍스트 선택, 다시 시도, 수정해서 다시 보내기, 신고하기.
struct BubbleMenu: ViewModifier {
    let actions: [MsgAction]
    let text: String
    let onAction: (MsgAction) -> Void

    func body(content: Content) -> some View {
        if actions.isEmpty {
            content
        } else {
            content.contextMenu {
                ForEach(Array(actions.enumerated()), id: \.offset) { _, a in
                    switch a {
                    case .copy:
                        Button { onAction(.copy) } label: { Label(L(R.string.msg_action_copy), systemImage: KeloIcon.copy.rawValue) }
                    case .share:
                        ShareLink(item: text) { Label(L(R.string.msg_action_share), systemImage: KeloIcon.share.rawValue) }
                    case .select:
                        Button { onAction(.select) } label: { Label(L(R.string.msg_action_select), systemImage: KeloIcon.textSelect.rawValue) }
                    case .retry:
                        Button { onAction(.retry) } label: { Label(L(R.string.msg_action_retry), systemImage: KeloIcon.retry.rawValue) }
                    case .edit:
                        Button { onAction(.edit) } label: { Label(L(R.string.msg_action_edit), systemImage: KeloIcon.edit.rawValue) }
                    case .report:
                        Button(role: .destructive) { onAction(.report) } label: { Label(L(R.string.msg_action_report), systemImage: KeloIcon.flag.rawValue) }
                    }
                }
            }
            .accessibilityHint(L(R.string.msg_actions_label))
        }
    }
}

/// The user's own message, on the right. `reaction`: the emoji Kelo put on it (Reactions), a chip over the bubble's bottom edge
/// near its end corner (BubbleWithReaction); the row keeps room for the part that hangs below.
struct UserBubble: View {
    let text: String
    var reaction: String = ""
    let actions: [MsgAction]
    let onAction: (MsgAction) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            BubbleWithReaction {
                Text(text).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 15).padding(.vertical, 11)
                    .background(RoundedRectangle(cornerRadius: 20).fill(c.mine))
                    .contentShape(RoundedRectangle(cornerRadius: 20))
                    .modifier(BubbleMenu(actions: actions, text: text, onAction: onAction))
                if !reaction.isEmpty { ReactionChip(emoji: reaction) }
            }
        }
    }
}

/// The user's bubble (first view) with its reaction chip (second view, when there is one), as Android's BubbleWithReaction: the
/// chip overlaps the bubble's bottom edge near its end corner (`ReactionChip.endInset` in from the end side) and hangs
/// `ReactionChip.overhang` below it; the layout takes exactly that much room under the bubble, so the chip never covers the next
/// row. A bubble too short for the chip and the inset gets the chip centred under it (`Reactions.chipStart`). Placed from the start
/// edge: SwiftUI mirrors a custom Layout in right-to-left, so in Arabic the chip sits near the bubble's left corner.
struct BubbleWithReaction: Layout {
    private func bubbleSize(_ proposal: ProposedViewSize, _ subviews: Subviews) -> CGSize {
        subviews.first?.sizeThatFits(ProposedViewSize(width: proposal.width, height: nil)) ?? .zero
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let b = bubbleSize(proposal, subviews)
        return CGSize(width: b.width, height: b.height + (subviews.count > 1 ? ReactionChip.overhang : 0))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let bubble = subviews.first else { return }
        let b = bubbleSize(ProposedViewSize(width: bounds.width, height: nil), subviews)
        bubble.place(at: bounds.origin, proposal: ProposedViewSize(b))
        guard subviews.count > 1 else { return }
        let chip = subviews[1], size = chip.sizeThatFits(.unspecified)
        let x = Reactions.chipStart(bubbleWidth: b.width, chipWidth: size.width, inset: ReactionChip.endInset)
        chip.place(at: CGPoint(x: bounds.minX + x, y: bounds.minY + b.height + ReactionChip.overhang - size.height),
                   proposal: ProposedViewSize(size))
    }
}

/// The emoji on the user's own message (Android ReactionChip): a small round chip over the bubble's bottom edge near its end corner
/// (BubbleWithReaction). 👀 pops in when Kelo starts on the message and gives way to the final emoji with a small bounce (a plain
/// swap with Reduce Motion). Drawn with the system emoji font, so a ZWJ sequence (🏃‍♂️) is one picture. VoiceOver says "읽음" /
/// "Read", then the emoji.
struct ReactionChip: View {
    /// How far the chip hangs below the bubble (the row keeps this much room under it).
    static let overhang: CGFloat = 11
    /// How far in from the bubble's end edge the chip's box sits; the chip draws inside a 2 pt halo for its shadow, so what shows
    /// is 12 pt in from the bubble's edge.
    static let endInset: CGFloat = 10
    let emoji: String
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Text(emoji).font(.system(size: 11)).lineLimit(1).fixedSize()
                .padding(.horizontal, 4).padding(.vertical, 2)
                .frame(minWidth: 22, minHeight: 20)
                .background(Capsule().fill(c.bubble).shadow(color: .black.opacity(0.16), radius: 1.2, y: 0.8))
                .overlay(Capsule().strokeBorder(c.bg, lineWidth: 1.25))
                .padding(2)
                .id(emoji)
                .transition(reduceMotion ? .identity : .scale(scale: 0.4).combined(with: .opacity))
        }
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 0.55), value: emoji)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }

    private var spoken: String {
        let read = L(R.string.chat_reaction_read), reacted = L(R.string.chat_reaction_done, emoji)
        return Reactions.spoken(emoji, readWord: read) { _ in reacted }
    }
}

/// A reply, one bubble per <message_break/>. While it streams the text is revealed smoothly (RevealPacing) instead of jumping by
/// a chunk at each poll; with Reduce Motion it shows as it comes.
struct AssistantBubbles: View {
    let text: String
    let streaming: Bool
    let actions: [MsgAction]
    let onAction: (String, MsgAction) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduce
    /// Kept across frames without causing redraws (the TimelineView drives them).
    @State private var pacer = Pacer()

    final class Pacer {
        var state: RevealPacing.State?
        var last: Date?

        /// Moves the counter on by the time since the last frame.
        func advance(to total: Int, at date: Date) -> Int {
            var r = state ?? RevealPacing.start(total, animate: true)
            if r.target != total { r = RevealPacing.retarget(r, total) }
            let dt = last.map { date.timeIntervalSince($0) * 1000 } ?? 16
            r = RevealPacing.advance(r, dtMs: dt)
            state = r
            last = r.done ? nil : date
            return Int(r.shown.rounded(.down))
        }

        var behind: Bool { state.map { !$0.done } ?? false }
    }

    /// The text length the reveal last caught up with: no frames are drawn until more text comes (a reply still streaming often
    /// waits a while between pieces, and frames for nothing inside the chat's lazy list cost it a full layout each).
    @State private var caughtUpAt = -1

    var body: some View {
        let total = text.count
        if !reduce && (streaming || pacer.behind) {
            // a reply that ended while behind keeps going until it has caught up
            TimelineView(.animation(minimumInterval: 1.0 / 60, paused: caughtUpAt == total)) { tl in
                let shown = pacer.advance(to: total, at: tl.date)
                bubbles(shown: shown)
                    .onChange(of: shown >= total, initial: true) { _, done in if done && caughtUpAt != total { caughtUpAt = total } }
            }
        } else {
            bubbles(shown: total)
        }
    }

    @ViewBuilder
    private func bubbles(shown: Int) -> some View {
        let visible = shown >= text.count ? text : String(text.prefix(shown))
        let parts = Markdown.splitBubbles(visible)
        let full = Markdown.splitBubbles(text)
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(parts.enumerated()), id: \.offset) { i, part in
                let plain = Markdown.plain(i < full.count ? full[i] : part)
                HStack {
                    MarkdownView(blocks: Markdown.blocks(part))
                        .padding(.horizontal, 15).padding(.vertical, 11)
                        .background(RoundedRectangle(cornerRadius: 20).fill(BubbleColor.assistant))
                        .contentShape(RoundedRectangle(cornerRadius: 20))
                        .modifier(BubbleMenu(actions: actions, text: plain) { a in onAction(plain, a) })
                    Spacer(minLength: 48)
                }
            }
        }
    }
}

/// The reply bubble's fill (the palette's bubble colour, read through the environment in a view).
enum BubbleColor {
    static var assistant: some ShapeStyle { BubbleFill() }
}

private struct BubbleFill: ShapeStyle {
    func resolve(in environment: EnvironmentValues) -> Color { environment.kelo.bubble }
}

/// An error; a long press offers 복사 and (after the user's last message) 다시 시도.
struct ErrorBubble: View {
    let text: String
    let actions: [MsgAction]
    let onAction: (MsgAction) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        HStack {
            Text(text).font(KeloFont.bodyLarge).foregroundStyle(c.dangerInk)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 15).padding(.vertical, 11)
                .background(RoundedRectangle(cornerRadius: 20).fill(c.dangerSoft))
                .modifier(BubbleMenu(actions: actions, text: text, onAction: onAction))
            Spacer(minLength: 48)
        }
    }
}

/// A small outlined line (a tool's result, a notice).
/// The small, muted, centred time line above a message that starts a new stretch of the chat (TimeLabels): not a bubble, wraps in
/// any language.
struct TimeLine: View {
    let atMs: Int64
    @Environment(\.kelo) private var c

    var body: some View {
        Text(TimeLabels.label(atMs, locale: Strings.shared.locale, today: L(R.string.chat_time_today), yesterday: L(R.string.chat_time_yesterday)))
            .font(KeloFont.caption).foregroundStyle(c.muted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 300)
            .frame(maxWidth: .infinity)
            .padding(.top, 8).padding(.bottom, 2)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A tool step's chip. A folded one (several calls of one kind in a row) updates in place and opens to list each call; a plain one
/// still running shows nothing (the status line under the messages says it).
struct ToolChip: View {
    let tool: ChatItem.Tool
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce
    @State private var open = false

    var body: some View {
        if tool.folded {
            VStack(alignment: .leading, spacing: 4) {
                Button {
                    withAnimation(reduce ? nil : .easeInOut(duration: 0.2)) { open.toggle() }
                } label: {
                    HStack {
                        Text(ChatWords.folded(tool) + (open ? "  ▴" : "  ▾")).font(KeloFont.caption).foregroundStyle(c.muted)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 10).padding(.vertical, 3)
                            .overlay(Capsule().stroke(c.line, lineWidth: 1))
                            .contentShape(Capsule())
                        Spacer(minLength: 0)
                    }
                }
                .buttonStyle(.plain)
                .accessibilityValue(L(open ? R.string.a11y_expanded : R.string.a11y_collapsed))
                .accessibilityIdentifier("kelo.toolchip")
                if open {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(tool.members.enumerated()), id: \.offset) { _, one in
                            Text("\(ChatWords.mark(one.state)) \(one.detail.isEmpty ? one.shown : one.detail)")
                                .font(KeloFont.caption).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.leading, 10)
                    .transition(.opacity)
                }
            }
        } else if tool.state != .running {
            NoticeChip(text: ChatWords.tool(tool))
        }
    }
}

struct NoticeChip: View {
    let text: String
    @Environment(\.kelo) private var c

    var body: some View {
        HStack {
            Text(text).font(KeloFont.caption).foregroundStyle(c.muted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10).padding(.vertical, 3)
                .overlay(Capsule().stroke(c.line, lineWidth: 1))
            Spacer(minLength: 0)
        }
    }
}

/// 텍스트 선택: the message on a page of its own, selectable.
struct SelectTextPage: View {
    let text: String
    let onClose: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SheetHeader(L(R.string.msg_select_title), onClose: onClose)
            Text(L(R.string.msg_select_hint)).font(KeloFont.small).foregroundStyle(c.muted)
            ScrollView {
                Text(text).font(KeloFont.bodyLarge).foregroundStyle(c.ink).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 8)
            }
        }
        .padding(.horizontal, 20)
    }
}

/// A card's folded result: a small icon and one line ("허용함 · gmail.send").
struct ResultLine: View {
    let icon: KeloIcon
    let text: String
    var muted = false
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            KeloIconView(icon, size: 16, tint: muted ? c.muted : c.ink)
            Text(text).font(KeloFont.small).foregroundStyle(muted ? c.muted : c.ink).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// .approval / .secure-card: a full-width card in the chat list.
struct CardBox<Content: View>: View {
    let content: Content
    @Environment(\.kelo) private var c
    init(@ViewBuilder content: () -> Content) { self.content = content() }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14).padding(.vertical, 12)
            .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.line, lineWidth: 1))
    }
}

/// The agent's questions with answers to tap: one question sends with one tap; several are picked and sent together. An answer to
/// write ("Other", "직접 입력할게요") opens a text box under its question and the typed words are sent instead (QuickReplyRules).
struct QuickReplies: View {
    let set: ReplySet
    let onSend: (String, [ReplyAnswer]) -> Void
    @State private var picks: [Int: String] = [:]
    @State private var typed: [Int: String] = [:]
    @State private var values: [Int: String]
    @Environment(\.kelo) private var c

    init(set: ReplySet, onSend: @escaping (String, [ReplyAnswer]) -> Void) {
        self.set = set; self.onSend = onSend
        // what each field (a picker or a box) holds: its plain form once complete, "" while half filled; starts on its default
        var v: [Int: String] = [:]
        for (i, q) in set.questions.enumerated() where ReplyFields.isField(q) { if let s = ReplyFields.start(q) { v[i] = s } }
        _values = State(initialValue: v)
    }

    private func send() {
        guard QuickReplyRules.canSend(set, picks: picks, typed: typed, values: values) else { return }
        onSend(QuickReplyRules.compose(set, picks: picks, typed: typed, values: values, show: ReplyFieldWords.show),
               QuickReplyRules.answers(set, picks: picks, typed: typed, values: values))
    }

    var body: some View {
        let writing = QuickReplyRules.writing(set, picks: picks)
        let canSend = QuickReplyRules.canSend(set, picks: picks, typed: typed, values: values)
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(set.questions.enumerated()), id: \.offset) { i, q in
                if !q.question.trimmingCharacters(in: .whitespaces).isEmpty && (set.questions.count > 1 || ReplyFields.isField(q)) {
                    Text(q.question).font(KeloFont.titleSmall).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                if ReplyFields.isField(q) {
                    ReplyFieldView(q: q, value: Binding(get: { values[i] ?? "" }, set: { values[i] = $0 }))
                } else {
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        ForEach(QuickReplyRules.choices(q), id: \.self) { o in
                            ChoiceChip(o == QuickReplyRules.other ? L(R.string.reply_other) : o, selected: picks[i] == o) {
                                if QuickReplyRules.sendsOnTap(set, q, o) { onSend(o, [ReplyAnswer(i, o)]) }
                                else if picks[i] == o { picks[i] = nil } else { picks[i] = o }
                            }
                        }
                    }
                }
                if writing.contains(i) {
                    KeloTextField(QuickReplyRules.placeholder(q) ?? L(R.string.reply_type_hint),
                                  text: Binding(get: { typed[i] ?? "" }, set: { typed[i] = $0 }), axis: .vertical, maxLength: 300) {
                        send()
                    }
                }
            }
            if QuickReplyRules.showsSend(set, picks: picks) {
                PrimaryButton(L(R.string.reply_send), enabled: canSend) { send() }
                if !QuickReplyRules.sendsAtOnce(set) { Text(L(R.string.reply_pick_hint)).font(KeloFont.small).foregroundStyle(c.muted) }
            }
        }
        .padding(.leading, 4).padding(.trailing, 32)
    }
}
