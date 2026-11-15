import SwiftUI
import KeloCore
import KeloShared

// The work page's building blocks (Android work/WorkParts.kt + the small panels of JobPages / WatchPages): a rail down the left
// with a marker per entry and the time beside it, section titles, entry boxes, status chips, and a wrapping row for chips and
// actions (long words in any language wrap to the next line instead of being cut).

/// Lays its children out in rows, wrapping to the next row when one does not fit (Compose FlowRow).
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxW = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0, widest: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(ProposedViewSize(width: maxW, height: nil))
            if x > 0 && x + s.width > maxW { y += lineH + lineSpacing; x = 0; lineH = 0 }
            x += s.width + spacing
            lineH = max(lineH, s.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: proposal.width ?? widest, height: y + lineH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineH: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
            if x > bounds.minX && x + s.width > bounds.maxX { y += lineH + lineSpacing; x = bounds.minX; lineH = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: min(s.width, bounds.width), height: s.height))
            x += s.width + spacing
            lineH = max(lineH, s.height)
        }
    }
}

/// What sits on the rail beside an entry.
enum RailMarker { case live, ask, planned, paused, done, failed, none }

let railWidth: CGFloat = 68
private let timeWidth: CGFloat = 48
private let markY: CGFloat = 20

/// One entry on the timeline: the time (or nothing) on the left, the rail with its marker, and the entry.
struct RailItem<Content: View>: View {
    let marker: RailMarker
    let label: String?
    let content: Content
    @Environment(\.kelo) private var c

    init(_ marker: RailMarker, label: String? = nil, @ViewBuilder content: () -> Content) {
        self.marker = marker; self.label = label; self.content = content()
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            Text(label ?? "").font(KeloFont.label).foregroundStyle(c.muted).multilineTextAlignment(.trailing).lineLimit(2)
                .frame(width: timeWidth - 6, alignment: .trailing).padding(.trailing, 6).padding(.top, markY - 8)
            Color.clear.frame(width: railWidth - timeWidth)
            content.frame(maxWidth: .infinity, alignment: .leading).padding(.bottom, 12)
        }
        .background(alignment: .topLeading) {
            GeometryReader { g in
                let x = timeWidth + (railWidth - timeWidth) / 2
                ZStack(alignment: .topLeading) {
                    Rectangle().fill(c.line).frame(width: 2, height: g.size.height).offset(x: x - 1)
                    marker(at: x)
                }
            }
        }
    }

    @ViewBuilder private func marker(at x: CGFloat) -> some View {
        let r: CGFloat = 6
        switch marker {
        case .live:
            ZStack {
                Circle().fill(c.accent.opacity(0.25)).frame(width: r * 3.6, height: r * 3.6)
                Circle().fill(c.accent).frame(width: r * 2, height: r * 2)
            }
            .position(x: x, y: markY)
        case .ask: Circle().fill(c.warn).frame(width: r * 2, height: r * 2).position(x: x, y: markY)
        case .planned: Circle().fill(c.bg).overlay(Circle().stroke(c.accent, lineWidth: 2)).frame(width: r * 2, height: r * 2).position(x: x, y: markY)
        case .paused: Circle().fill(c.bg).overlay(Circle().stroke(c.muted, lineWidth: 2)).frame(width: r * 2, height: r * 2).position(x: x, y: markY)
        case .done: Circle().fill(c.success).frame(width: r * 1.6, height: r * 1.6).position(x: x, y: markY)
        case .failed: Circle().fill(c.danger).frame(width: r * 1.6, height: r * 1.6).position(x: x, y: markY)
        case .none: EmptyView()
        }
    }
}

/// A line on the rail without a marker (a placeholder, an empty section).
struct RailNote<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View { RailItem(.none) { content.padding(.top, 6) } }
}

/// A section's title: its name, how many things are in it, and an optional action.
struct SectionTitle<Trailing: View>: View {
    let text: String
    let count: Int?
    let attention: Bool
    let trailing: Trailing
    @Environment(\.kelo) private var c

    init(_ text: String, count: Int?, attention: Bool = false, @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.text = text; self.count = count; self.attention = attention; self.trailing = trailing()
    }

    var body: some View {
        HStack(spacing: 8) {
            Text(text).font(.system(.callout, weight: .bold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            if let count, count > 0 {
                Text(String(count)).font(.system(.caption2, weight: .bold)).foregroundStyle(attention ? c.onWarn : c.ink)
                    .padding(.horizontal, 7).padding(.vertical, 1)
                    .background(Capsule().fill(attention ? c.warn : c.bubble))
            }
            Spacer(minLength: 0)
            trailing
        }
        .padding(.leading, 4).padding(.top, 18).padding(.bottom, 8)
        .accessibilityAddTraits(.isHeader)
    }
}

/// A day inside a section ("오늘 · 11월 7일 (토)"), in line with the entries.
struct DayCaption: View {
    let text: String
    @Environment(\.kelo) private var c
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.accent).fixedSize(horizontal: false, vertical: true)
            .padding(.leading, railWidth).padding(.top, 4).padding(.bottom, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct EmptyLine: View {
    let text: String
    @Environment(\.kelo) private var c
    init(_ text: String) { self.text = text }
    var body: some View { Text(text).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
}

/// A small coloured word for a state.
struct StatusChip: View {
    let text: String
    let tone: Color
    init(_ text: String, _ tone: Color) { self.text = text; self.tone = tone }
    var body: some View {
        Text(text).font(.system(.caption2, weight: .semibold)).foregroundStyle(tone).lineLimit(1)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 6).fill(tone.opacity(0.12)))
    }
}

/// An entry's box: a hairline outline on the card colour; tappable when `action` is given.
struct EntryBox<Content: View>: View {
    let action: (() -> Void)?
    let content: Content
    @Environment(\.kelo) private var c

    init(action: (() -> Void)? = nil, @ViewBuilder content: () -> Content) { self.action = action; self.content = content() }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 6)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 12))
            .onTapGesture { action?() }
            .accessibilityAddTraits(action != nil ? .isButton : [])
    }
}

/// A small text action inside a card.
struct CardAction: View {
    let text: String
    let icon: KeloIcon?
    let danger: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    init(_ text: String, icon: KeloIcon? = nil, danger: Bool = false, action: @escaping () -> Void) {
        self.text = text; self.icon = icon; self.danger = danger; self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 3) {
                if let icon { KeloIconView(icon, size: 15, tint: danger ? c.danger : c.accent) }
                Text(text).font(.system(.footnote, weight: .semibold)).foregroundStyle(danger ? c.danger : c.accent)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 8).padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// A rounded panel on a sub page.
struct Panel<Content: View>: View {
    let content: Content
    @Environment(\.kelo) private var c
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
    }
}

/// A row that, dragged to the left, shows a trash can on red and calls `onSwiped` once dragged far enough (the row itself leaves
/// with the list's next state). Rows sit in a scroll view, so only a mostly sideways drag moves it. VoiceOver and other assistive
/// tech get the same as a named action (삭제).
struct SwipeToTrash<Content: View>: View {
    let onSwiped: () -> Void
    let content: Content
    @State private var offset: CGFloat = 0
    @State private var sideways: Bool?
    @State private var width: CGFloat = 320
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce

    init(onSwiped: @escaping () -> Void, @ViewBuilder content: () -> Content) {
        self.onSwiped = onSwiped
        self.content = content()
    }

    var body: some View {
        ZStack(alignment: .trailing) {
            if offset < 0 {
                c.danger
                    .overlay(alignment: .trailing) {
                        KeloIconView(.trash, size: 22, tint: c.onDanger).padding(.trailing, 18)
                    }
                    .accessibilityHidden(true)
            }
            content
                .background(c.card)
                .offset(x: offset)
        }
        .clipped()
        .background(GeometryReader { g in Color.clear.onAppear { width = g.size.width }.onChange(of: g.size.width) { _, w in width = w } })
        .simultaneousGesture(
            DragGesture(minimumDistance: 14)
                .onChanged { v in
                    if sideways == nil { sideways = abs(v.translation.width) > abs(v.translation.height) * 1.5 }
                    guard sideways == true else { return }
                    offset = min(0, v.translation.width)
                }
                .onEnded { v in
                    defer { sideways = nil }
                    guard sideways == true else { return }
                    let far = -max(width * 0.4, 80)
                    if v.translation.width < far || v.predictedEndTranslation.width < -width * 0.8 {
                        Haptics.tick()
                        withAnimation(reduce ? nil : .easeOut(duration: 0.18)) { offset = -width }
                        onSwiped()
                    } else {
                        withAnimation(reduce ? nil : .easeOut(duration: 0.18)) { offset = 0 }
                    }
                }
        )
        .accessibilityAction(named: Text(L(R.string.delete))) { onSwiped() }
    }
}

/// An accent heading above a panel.
struct PanelLabel: View {
    let text: String
    @Environment(\.kelo) private var c
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.accent).fixedSize(horizontal: false, vertical: true).padding(.top, 6)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A label and its value side by side (wraps under each other at large text sizes).
struct FactRow: View {
    let label: String
    let value: String
    var skipped = false
    @Environment(\.kelo) private var c
    @Environment(\.dynamicTypeSize) private var size
    var body: some View {
        let layout = size.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 2)) : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
        layout {
            Text(label).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: size.isAccessibilitySize ? .infinity : 120, alignment: .leading)
            Text(value).font(KeloFont.small).foregroundStyle(skipped ? c.muted : c.ink).italic(skipped)
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
    }
}

/// Text that may carry Markdown (an answer, a report), shown with its inline styling.
struct MarkdownBlock: View {
    let text: String
    @Environment(\.kelo) private var c
    init(_ text: String) { self.text = text }
    var body: some View {
        let md = (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
        Text(md).font(KeloFont.bodyLarge).foregroundStyle(c.ink).tint(c.accent).textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A thin progress track.
struct ThinProgress: View {
    let value: Double
    let tone: Color
    var height: CGFloat = 4
    @Environment(\.kelo) private var c
    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(c.line)
                Capsule().fill(tone).frame(width: g.size.width * min(max(value, 0), 1))
            }
        }
        .frame(height: height)
    }
}

/// The short message at the bottom (Android snackbar): shows for a few seconds, then goes. One with an action (실행 취소) has a
/// button at its end and stays a little longer (longer still with VoiceOver on); `onEnd` runs when it goes without the action.
struct ToastHost: ViewModifier {
    @Binding var toast: WorkModel.Toast?
    var bottom: CGFloat = 16
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver

    func body(content: Content) -> some View {
        content.overlay(alignment: .bottom) {
            if let t = toast {
                HStack(spacing: 12) {
                    Text(t.text).font(KeloFont.body).foregroundStyle(c.bg).multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let action = t.action {
                        Button {
                            guard toast?.id == t.id else { return }
                            toast = nil
                            t.onAction?()
                        } label: {
                            Text(action).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.bg)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.vertical, 4).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                .frame(maxWidth: 560, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 8).fill(c.ink))
                .padding(.horizontal, 12).padding(.bottom, bottom)
                .transition(.opacity)
                .task(id: t.id) {
                    let seconds: Double = t.action == nil ? 3.5 : (voiceOver ? 10 : 4)
                    try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                    if Task.isCancelled { return }   // the page went: nothing is decided for it
                    if toast?.id == t.id { toast = nil; t.onEnd?() }
                }
                .onTapGesture { if toast?.id == t.id { toast = nil; t.onEnd?() } }
                .accessibilityElement(children: t.action == nil ? .combine : .contain)
                .accessibilityAddTraits(.updatesFrequently)
            }
        }
        .animation(KeloMotionSpec.small(reduce), value: toast)
    }
}

extension View {
    func workToast(_ toast: Binding<WorkModel.Toast?>, bottom: CGFloat = 16) -> some View { modifier(ToastHost(toast: toast, bottom: bottom)) }
}

/// Tones for states.
extension KeloColors {
    func task(_ status: String) -> Color {
        switch status {
        case AgentTask.statusRunning: return accent
        case AgentTask.statusWaiting: return warn
        case AgentTask.statusFailed: return danger
        case AgentTask.statusStopped, "interrupted", "waiting_model": return muted
        default: return success
        }
    }

    func job(_ status: String) -> Color {
        switch status {
        case Worker.statusRunning: return accent
        case Worker.statusNeedsInput, Worker.statusBlocked, Worker.statusInterrupted: return warn
        case Worker.statusFailed: return danger
        case Worker.statusStopped: return muted
        default: return success
        }
    }

    func watch(_ state: String) -> Color {
        switch state {
        case "watching": return accent
        case "error": return danger
        case "done": return success
        default: return muted
        }
    }
}

/// The browser tab a task works in (Timeline.tabFor needs an AgentTask; a task page only has the detail).
func taskBrief(_ d: TaskDetail) -> AgentTask {
    AgentTask(id: d.id, turnId: d.turnId, title: d.title, status: d.status, result: d.result, source: d.source, startedAt: d.startedAt,
              finishedAt: d.finishedAt, liveStatus: d.liveStatus, parentId: d.parentId, workerId: d.workerId)
}
