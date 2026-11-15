import SwiftUI

// The app's building blocks (Android ui/components/Components.kt and my/Rows.kt). Every text wraps (no fixed heights, no
// single-line labels except names and tokens) so long words in any of the 20 languages fit at 320 pt.

/// Long unbroken tokens (ids, model names, file names, hosts) are shortened in the middle, never wrapped inside (Android TokenText).
public enum TokenText {
    public static let rowMax = 32

    public static func fit(_ text: String, max: Int = rowMax) -> String {
        guard text.count > max else { return text }
        return text.split(separator: " ", omittingEmptySubsequences: false)
            .map { $0.count > max ? middle(String($0), max: max) : String($0) }
            .joined(separator: " ")
    }

    public static func middle(_ token: String, max: Int = rowMax) -> String {
        guard token.count > max, max >= 3 else { return token }
        let keep = max - 1
        let head = (keep + 1) / 2
        return String(token.prefix(head)) + "…" + String(token.suffix(keep - head))
    }

    public static func isToken(_ text: String) -> Bool { !text.isEmpty && !text.contains { $0.isWhitespace } }
}

/// Shrinks a little while pressed (Android pressShrink); nothing with Reduce Motion.
public struct PressShrinkStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduce
    public init() {}
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduce ? KeloMotionSpec.pressShrink : 1)
            .animation(KeloMotionSpec.small(reduce), value: configuration.isPressed)
    }
}

/// No press feedback at all (the bottom tabs: no fade, no highlight).
public struct NoFeedbackStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View { configuration.label.contentShape(Rectangle()) }
}

/// The filled main action, full width.
public struct PrimaryButton: View {
    let title: String
    let enabled: Bool
    let busy: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    public init(_ title: String, enabled: Bool = true, busy: Bool = false, action: @escaping () -> Void) {
        self.title = title; self.enabled = enabled; self.busy = busy; self.action = action
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if busy { ProgressView().tint(c.onAccent).controlSize(.small) }
                Text(title).font(KeloFont.button).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, minHeight: 24)
            .padding(.horizontal, 20).padding(.vertical, 12)
            .foregroundStyle(enabled ? c.onAccent : c.disabledInk)
            .background(Capsule().fill(enabled ? c.accent : c.disabledFill))
        }
        .buttonStyle(PressShrinkStyle())
        .disabled(!enabled || busy)
    }
}

/// A plain button: card background with a hairline border.
public struct SecondaryButton: View {
    let title: String
    let icon: KeloIcon?
    let enabled: Bool
    let fill: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    public init(_ title: String, icon: KeloIcon? = nil, enabled: Bool = true, fill: Bool = false, action: @escaping () -> Void) {
        self.title = title; self.icon = icon; self.enabled = enabled; self.fill = fill; self.action = action
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let icon { KeloIconView(icon, size: 18, tint: enabled ? c.ink : c.disabledInk) }
                Text(title).font(KeloFont.bodyLarge).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: fill ? .infinity : nil)
            .padding(.horizontal, 14).padding(.vertical, 11)
            .foregroundStyle(enabled ? c.ink : c.disabledInk)
            .background(Capsule().fill(c.card))
            .overlay(Capsule().stroke(c.line, lineWidth: 1))
        }
        .buttonStyle(PressShrinkStyle())
        .disabled(!enabled)
    }
}

/// A text action in the accent colour (red when `danger`).
public struct LinkButton: View {
    let title: String
    let danger: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    public init(_ title: String, danger: Bool = false, action: @escaping () -> Void) {
        self.title = title; self.danger = danger; self.action = action
    }

    public var body: some View {
        Button(action: action) {
            Text(title).font(KeloFont.small).foregroundStyle(danger ? c.danger : c.accent)
                .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
    }
}

/// A filter chip: outlined when off, tinted with a check when on.
public struct ChoiceChip: View {
    let title: String
    let selected: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    public init(_ title: String, selected: Bool, action: @escaping () -> Void) {
        self.title = title; self.selected = selected; self.action = action
    }

    public var body: some View {
        Button {
            Haptics.tick(); action()
        } label: {
            HStack(spacing: 4) {
                if selected { KeloIconView(.check, size: 15, tint: c.ink) }
                Text(title).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, selected ? 8 : 12).padding(.trailing, 12).padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 8).fill(selected ? c.mine : c.card))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(selected ? .clear : c.line, lineWidth: 1))
        }
        .buttonStyle(PressShrinkStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// Input: card background, hairline border, accent when focused. A field that holds one value (an address, a key, a code, a name, a
/// number; `axis: .horizontal`, the default) is one line: its value scrolls sideways and its hint never wraps (cut with … instead).
/// Only message-like fields (`axis: .vertical`) grow. SingleLineFieldsTests checks every field of the app keeps to this.
public struct KeloTextField: View {
    let placeholder: String
    @Binding var text: String
    let secure: Bool
    let keyboard: KeloKeyboard
    let axis: Axis
    let maxLength: Int
    let onSubmit: () -> Void
    @FocusState private var focused: Bool
    @Environment(\.kelo) private var c

    public init(_ placeholder: String, text: Binding<String>, secure: Bool = false, keyboard: KeloKeyboard = .text,
                axis: Axis = .horizontal, maxLength: Int = .max, onSubmit: @escaping () -> Void = {}) {
        self.placeholder = placeholder; _text = text; self.secure = secure; self.keyboard = keyboard; self.axis = axis
        self.maxLength = maxLength; self.onSubmit = onSubmit
    }

    public var body: some View {
        Group {
            if secure {
                SecureField("", text: $text, prompt: Text(placeholder).foregroundStyle(c.muted))
            } else {
                TextField("", text: $text, prompt: Text(placeholder).foregroundStyle(c.muted), axis: axis)
            }
        }
        .lineLimit(axis == .horizontal ? 1 : nil)
        .truncationMode(.tail)
        .font(KeloFont.bodyLarge)
        .foregroundStyle(c.ink)
        .keloKeyboard(keyboard)
        .focused($focused)
        .onSubmit(onSubmit)
        .onChange(of: text) { _, v in if v.count > maxLength { text = String(v.prefix(maxLength)) } }
        .padding(.horizontal, 14).padding(.vertical, 13)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(focused ? c.accent : c.line, lineWidth: focused ? 2 : 1))
    }
}

/// A label above a field.
public struct FieldLabel: View {
    let text: String
    @Environment(\.kelo) private var c
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
    }
}

/// .card: 18 pt corners, hairline, padding.
public struct KeloCard<Content: View>: View {
    let spacing: CGFloat
    let content: Content
    @Environment(\.kelo) private var c

    public init(spacing: CGFloat = 10, @ViewBuilder content: () -> Content) {
        self.spacing = spacing; self.content = content()
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: spacing) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16).padding(.vertical, 14)
            .background(RoundedRectangle(cornerRadius: 18).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(c.line, lineWidth: 1))
    }
}

/// A card on a My page (12 pt corners), optionally tappable.
public struct MyCard<Content: View>: View {
    let action: (() -> Void)?
    let content: Content
    @Environment(\.kelo) private var c

    public init(action: (() -> Void)? = nil, @ViewBuilder content: () -> Content) {
        self.action = action; self.content = content()
    }

    public var body: some View {
        let inner = VStack(alignment: .leading, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        if let action {
            Button(action: action) { inner }.buttonStyle(PressShrinkStyle())
        } else {
            inner
        }
    }
}

/// The first-run screens' narrow column, a little below the top; scrolls with the keyboard up.
public struct CenterCard<Content: View>: View {
    let content: Content
    @Environment(\.kelo) private var c

    public init(@ViewBuilder content: () -> Content) { self.content = content() }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) { content }
                .frame(maxWidth: 420, alignment: .leading)
                .padding(.horizontal, 24).padding(.top, 72).padding(.bottom, 24)
                .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(c.bg.ignoresSafeArea())
    }
}

/// A centred muted line ("불러오는 중…") with optional actions under it.
public struct CenterMessage<Actions: View>: View {
    let text: String
    let actions: Actions
    public init(_ text: String, @ViewBuilder actions: () -> Actions = { EmptyView() }) {
        self.text = text; self.actions = actions()
    }
    public var body: some View {
        CenterCard {
            MutedText(text)
            actions
        }
    }
}

public struct MutedText: View {
    let text: String
    let small: Bool
    @Environment(\.kelo) private var c
    public init(_ text: String, small: Bool = false) { self.text = text; self.small = small }
    public var body: some View {
        Text(text).font(small ? KeloFont.small : KeloFont.bodyLarge).foregroundStyle(c.muted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

public struct ErrorText: View {
    let text: String
    let small: Bool
    @Environment(\.kelo) private var c
    public init(_ text: String, small: Bool = false) { self.text = text; self.small = small }
    public var body: some View {
        Text(text).font(small ? KeloFont.small : KeloFont.bodyLarge).foregroundStyle(c.danger)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityAddTraits(.updatesFrequently)
    }
}

/// `template` with its "%1$s" in bold (or as code), Android `emphasized`.
public func emphasized(_ template: String, _ arg: String, code: Bool = false) -> AttributedString {
    var out = AttributedString()
    guard let r = template.range(of: "%1$s") else { return AttributedString(template) }
    out += AttributedString(String(template[..<r.lowerBound]))
    var a = AttributedString(arg)
    if code { a.font = .system(.callout, design: .monospaced) } else { a.font = KeloFont.button }
    out += a
    out += AttributedString(String(template[r.upperBound...]))
    return out
}

/// A main tab's big title at the top of its page (it scrolls away under the floating header).
public struct TabTitle: View {
    let title: String
    @Environment(\.kelo) private var c
    public init(_ title: String) { self.title = title }
    public var body: some View {
        Text(title).font(KeloFont.display).foregroundStyle(c.ink)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 2).padding(.bottom, 4)
            .accessibilityAddTraits(.isHeader)
    }
}

/// The top of a full-screen page: a back arrow and the title, left-aligned. `actions` go at the end.
public struct BackTitleBar<Actions: View>: View {
    let title: String
    let onBack: () -> Void
    let actions: Actions
    @Environment(\.kelo) private var c

    public init(_ title: String, onBack: @escaping () -> Void, @ViewBuilder actions: () -> Actions = { EmptyView() }) {
        self.title = title; self.onBack = onBack; self.actions = actions()
    }

    public var body: some View {
        HStack(spacing: 4) {
            Button(action: onBack) {
                KeloIconView(.chevronLeft, size: 24, tint: c.ink).frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L(R.string.back))
            Text(title).font(KeloFont.titleLarge).foregroundStyle(c.ink).lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            actions
        }
        .padding(.horizontal, 4)
        .frame(minHeight: 56)
    }
}

/// A sub page: back bar on top, the content scrolling under it. Used inside a NavigationStack (the system bar is hidden; the
/// edge swipe still goes back).
public struct SubPage<Content: View, Actions: View>: View {
    let title: String
    let onBack: () -> Void
    let actions: Actions
    let content: Content
    @Environment(\.kelo) private var c

    public init(_ title: String, onBack: @escaping () -> Void, @ViewBuilder actions: () -> Actions = { EmptyView() },
                @ViewBuilder content: () -> Content) {
        self.title = title; self.onBack = onBack; self.actions = actions(); self.content = content()
    }

    public var body: some View {
        VStack(spacing: 0) {
            BackTitleBar(title, onBack: onBack) { actions }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) { content }
                    .frame(maxWidth: 640, alignment: .leading)
                    .padding(.horizontal, 16).padding(.top, 10).padding(.bottom, 24)
                    .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .background(c.bg.ignoresSafeArea())
        .keloHideNavigationBar()
    }
}

/// A group's heading, in the accent colour.
public struct SectionLabel: View {
    let text: String
    @Environment(\.kelo) private var c
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.accent)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 16).padding(.top, 20).padding(.bottom, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A count of new things in a quiet pill ("99+" past 99): nothing to act on, so not red (red is kept for what waits for the owner:
/// WorkBadge).
public struct CountPill: View {
    let count: Int
    @Environment(\.kelo) private var c
    public init(_ count: Int) { self.count = count }
    public var body: some View {
        Text(count > 99 ? L(R.string.count_badge_max) : String(count))
            .font(.system(size: 12, weight: .bold)).foregroundStyle(c.ink)
            .padding(.horizontal, 6).padding(.vertical, 1)
            .background(Capsule().fill(c.mine))
    }
}

/// A settings row: icon, name, and the current value under it. Red when `danger`; a count pill at the end.
public struct MenuRow: View {
    let icon: KeloIcon
    let label: String
    let value: String?
    let valueColor: Color?
    let danger: Bool
    let badge: Int?
    let action: () -> Void
    @Environment(\.kelo) private var c

    public init(_ icon: KeloIcon, _ label: String, value: String? = nil, valueColor: Color? = nil, danger: Bool = false,
                badge: Int? = nil, action: @escaping () -> Void) {
        self.icon = icon; self.label = label; self.value = value; self.valueColor = valueColor; self.danger = danger
        self.badge = badge; self.action = action
    }

    public var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                KeloIconView(icon, size: 24, tint: danger ? c.danger : c.muted)
                VStack(alignment: .leading, spacing: 2) {
                    Text(TokenText.fit(label)).font(KeloFont.bodyLarge).foregroundStyle(danger ? c.danger : c.ink)
                        .lineLimit(TokenText.isToken(label) ? 1 : 3)
                        .fixedSize(horizontal: false, vertical: true)
                    if let value, !value.isEmpty {
                        Text(TokenText.fit(value)).font(KeloFont.small).foregroundStyle(valueColor ?? c.muted).lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let badge, badge > 0 {
                    CountPill(badge).accessibilityLabel(P(R.plurals.count_new, badge, badge))
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// One block inside a card; blocks after the first get a hairline above.
public struct KeyBox<Content: View>: View {
    let first: Bool
    let content: Content
    @Environment(\.kelo) private var c
    public init(first: Bool = true, @ViewBuilder content: () -> Content) { self.first = first; self.content = content() }
    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !first { Divider().overlay(c.line).padding(.bottom, 4) }
            content
        }
    }
}

/// A muted centred line for an empty list.
public struct EmptyNote: View {
    let text: String
    @Environment(\.kelo) private var c
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text).font(KeloFont.small).foregroundStyle(c.muted).multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity).padding(.top, 24)
    }
}

/// A small rounded word (red when `denied`).
public struct KeloBadge: View {
    let text: String
    let denied: Bool
    @Environment(\.kelo) private var c
    public init(_ text: String, denied: Bool = false) { self.text = text; self.denied = denied }
    public var body: some View {
        Text(text).font(KeloFont.caption).foregroundStyle(denied ? c.dangerInk : c.muted).lineLimit(1)
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background(Capsule().fill(denied ? c.dangerSoft : c.bubble))
    }
}

/// A small tinted label ("지금 쓰는 모델").
public struct KeloTag: View {
    let text: String
    @Environment(\.kelo) private var c
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text).font(KeloFont.label).foregroundStyle(c.ink).lineLimit(1)
            .padding(.horizontal, 8).padding(.vertical, 2)
            .background(Capsule().fill(c.mine))
    }
}

/// A thin track with a filled part; red when `hot`.
public struct KeloBar: View {
    let fraction: Double
    let hot: Bool
    @Environment(\.kelo) private var c
    public init(_ fraction: Double, hot: Bool = false) { self.fraction = fraction; self.hot = hot }
    public var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(c.bubble)
                Capsule().fill(hot ? c.danger : c.accent).frame(width: g.size.width * min(max(fraction, 0), 1))
            }
        }
        .frame(height: 8).padding(.vertical, 2)
    }
}

/// A bold line with an icon in front.
public struct IconTitle: View {
    let icon: KeloIcon
    let text: String
    @Environment(\.kelo) private var c
    public init(_ icon: KeloIcon, _ text: String) { self.icon = icon; self.text = text }
    public var body: some View {
        HStack(spacing: 8) {
            KeloIconView(icon, size: 20, tint: c.ink)
            Text(text).font(KeloFont.button).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A floating pill saying the screen shows what was kept from last time (the server could not be reached). Tappable to retry.
public struct StaleHint: View {
    let visible: Bool
    let onRetry: (() -> Void)?
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce
    public init(visible: Bool, onRetry: (() -> Void)? = nil) { self.visible = visible; self.onRetry = onRetry }
    public var body: some View {
        Group {
            if visible {
                Button { onRetry?() } label: {
                    HStack(spacing: 7) {
                        Circle().fill(c.muted).frame(width: 7, height: 7)
                        Text(L(R.string.stale_hint)).font(KeloFont.small).foregroundStyle(c.muted).multilineTextAlignment(.center)
                            .lineLimit(3)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(Capsule().fill(c.card).shadow(color: c.scrim.opacity(0.12), radius: 3, y: 1))
                    .overlay(Capsule().stroke(c.line, lineWidth: 1))
                    .frame(maxWidth: 420)
                }
                .buttonStyle(.plain)
                .disabled(onRetry == nil)
                .padding(.horizontal, 16).padding(.vertical, 4)
                .transition(.opacity)
            }
        }
        .animation(KeloMotionSpec.small(reduce), value: visible)
    }
}

/// Grey placeholder bars while a page loads (Android Skeleton), pulsing gently unless Reduce Motion is on.
public struct SkeletonRows: View {
    let rows: Int
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce
    @State private var dim = false
    public init(rows: Int = 5) { self.rows = rows }
    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            ForEach(0..<rows, id: \.self) { i in
                HStack(spacing: 14) {
                    RoundedRectangle(cornerRadius: 8).fill(c.bubble).frame(width: 24, height: 24)
                    VStack(alignment: .leading, spacing: 6) {
                        RoundedRectangle(cornerRadius: 6).fill(c.bubble).frame(height: 14)
                            .frame(maxWidth: .infinity).padding(.trailing, CGFloat((i * 37) % 90))
                        RoundedRectangle(cornerRadius: 6).fill(c.bubble).frame(width: 90, height: 10)
                    }
                }
            }
        }
        .padding(16)
        // the pulse belongs to the fade alone: a repeating `withAnimation` started on appear also caught the frames of whatever
        // appeared with it (rows of a lazy list moving for ever, which held the main thread on the work page)
        .opacity(dim ? 0.55 : 1)
        .animation(reduce ? nil : .easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: dim)
        .onAppear { if !reduce { dim = true } }
        .accessibilityLabel(L(R.string.loading))
    }
}

/// A row of a radio list (language list, choices): title, optional detail, a check when selected.
public struct RadioRow: View {
    let title: String
    let detail: String?
    let selected: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c
    public init(_ title: String, detail: String? = nil, selected: Bool, action: @escaping () -> Void) {
        self.title = title; self.detail = detail; self.selected = selected; self.action = action
    }
    public var body: some View {
        Button(action: action) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    if let detail, !detail.isEmpty {
                        Text(detail).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                if selected { KeloIconView(.check, size: 20, tint: c.ink) }
            }
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }
}

/// A list of rows in a rounded card with hairlines between them (.menu-card).
public struct MenuCard<Content: View>: View {
    let content: Content
    @Environment(\.kelo) private var c
    public init(@ViewBuilder content: () -> Content) { self.content = content() }
    public var body: some View {
        VStack(spacing: 0) { content }
            .padding(.horizontal, 16)
            .background(RoundedRectangle(cornerRadius: 20).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(c.line, lineWidth: 1))
    }
}

/// A hairline in the palette's line colour.
public struct Hairline: View {
    @Environment(\.kelo) private var c
    public init() {}
    public var body: some View { Rectangle().fill(c.line).frame(height: 1) }
}

/// An on/off row: title, an explanation under it, and a switch.
public struct ToggleRow: View {
    let title: String
    let detail: String?
    @Binding var isOn: Bool
    let enabled: Bool
    @Environment(\.kelo) private var c
    public init(_ title: String, detail: String? = nil, isOn: Binding<Bool>, enabled: Bool = true) {
        self.title = title; self.detail = detail; _isOn = isOn; self.enabled = enabled
    }
    public var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                if let detail { Text(detail).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
            }
        }
        .tint(c.accent)
        .disabled(!enabled)
        .padding(.vertical, 10)
    }
}

public extension View {
    /// A yes/no question (Android ConfirmDialog): `danger` makes the yes button destructive.
    func keloConfirm(_ text: String, isPresented: Binding<Bool>, yes: String, danger: Bool = true, onYes: @escaping () -> Void) -> some View {
        alert(text, isPresented: isPresented) {
            Button(yes, role: danger ? .destructive : nil, action: onYes)
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    /// Page padding for a main tab's content (16 pt sides, max reading width).
    func keloPageWidth() -> some View {
        frame(maxWidth: 640, alignment: .leading).frame(maxWidth: .infinity)
    }
}
