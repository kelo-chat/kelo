import SwiftUI
import KeloCore
import KeloShared
import KeloChat
import KeloMy
import KeloWork
import KeloConnectors

/// The signed-in app (Android shell/MainShell.kt): the four tabs' pages, the floating header (the agent's round badge with its
/// name pill, centred; tapping it opens the work page), the tab bar at the bottom (no fade, no press highlight), the first-run
/// "choose a model" card, the work page and a browser tab's live view over everything. A sub page (any pushed page) has the whole
/// screen: header and bar step aside. Tapping the open tab again goes back to its start.
struct MainShell: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @Environment(\.scenePhase) private var phase
    @Environment(\.accessibilityReduceMotion) private var reduce
    /// Tabs visited so far (each keeps its state once built).
    @State private var built: Set<KeloTab> = [.chat]
    @State private var headerHeight: CGFloat = 0
    @State private var barHeight: CGFloat = 0
    /// the keyboard covers the bar, so the pages need no room for it then
    @State private var keyboardUp = false

    var body: some View {
        let router = app.router
        ZStack(alignment: .top) {
            c.bg.ignoresSafeArea()
            ZStack {
                ForEach(KeloTab.allCases, id: \.self) { tab in
                    if built.contains(tab) {
                        page(tab)
                            .opacity(router.tab == tab ? 1 : 0)
                            .allowsHitTesting(router.tab == tab)
                            .accessibilityHidden(router.tab != tab)
                    }
                }
            }
            // the header and the bar float over the pages; each tab's start page keeps clear of them (ShellInsets), and
            // scrolls underneath, as on Android
            .environment(\.keloShellInsets, ShellInsets(top: router.headerHidden ? 0 : headerHeight,
                                                         bottom: router.barHidden || keyboardUp ? 0 : barHeight))
            // the status bar keeps a strip in the page's colour, so the chat never scrolls up behind the clock (Android's
            // status-bar scrim)
            .overlay(alignment: .top) {
                GeometryReader { g in
                    VStack(spacing: 0) {
                        c.bg.frame(height: g.safeAreaInsets.top)
                        Spacer(minLength: 0)
                    }
                    .ignoresSafeArea(.container, edges: .top)
                }
                .allowsHitTesting(false)
                .accessibilityHidden(true)
            }
            .overlay(alignment: .top) {
                if !router.headerHidden {
                    FloatingHeader(onAgent: { router.openWork() })
                        .onGeometryChange(for: CGFloat.self, of: { $0.size.height }, action: { headerHeight = $0; KeloSheetMetrics.headerHeight = $0 })
                        .transition(.opacity)
                }
            }
            .overlay(alignment: .bottom) {
                // the bar steps aside while the keyboard is up, whoever has it (the composer, an in-chat form, a page's field), so it
                // never sits above the keyboard over a send or save button (Android hides it while the IME shows)
                if !router.barHidden && !keyboardUp {
                    TabBar()
                        .onGeometryChange(for: CGFloat.self, of: { $0.size.height }, action: { barHeight = $0 })
                        .ignoresSafeArea(.keyboard, edges: .bottom)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
            }
            .modifier(KeyboardWatch(up: $keyboardUp))
            .animation(KeloMotionSpec.small(reduce), value: router.headerHidden)
            .animation(KeloMotionSpec.small(reduce), value: router.barHidden)
            .animation(KeloMotionSpec.small(reduce), value: keyboardUp)
            // the keyguard_locked notification: the banner shows at once and starts the unlock check
            .onChange(of: router.unlockRequested, initial: true) { _, on in if on { app.setLocked(true) } }

            if let p = app.profile, ProfileRules.needsModel(p), router.tab == .chat, !router.workOpen, router.liveTab == nil,
               router.depth(.chat) == 0 {
                ChooseModelCard { router.openMyPage(.model) }
                    .padding(.top, 112).padding(.horizontal, 16)
                    .frame(maxWidth: 560)
            }

            // Keyguard: the server holds no key ("Kelo is locked"): unlock with this phone, or the recovery code in 마이 → 보안
            if app.locked, router.tab != .my, !router.workOpen, router.liveTab == nil {
                LockedBanner(onRecover: { router.openMyPage(.keyguardCode) })
                    .padding(.top, headerHeight + 8).padding(.horizontal, 12)
                    .frame(maxWidth: 560)
                    .transition(.opacity)
            }

            if router.workOpen {
                WorkScreen(onClose: { router.workOpen = false })
                    .background(c.bg.ignoresSafeArea())
                    .transition(.move(edge: .trailing).combined(with: .opacity))
                    .zIndex(1)
            }
            if let tab = router.liveTab {
                LiveOverlay(tabId: tab, takeControl: router.liveTakeControl, onClose: { router.closeLive() })
                    .background(c.bg.ignoresSafeArea())
                    .transition(.opacity)
                    .zIndex(2)
            }
            // the server needs a newer build (min_app_version.ios): this covers everything until the app is updated
            if app.appTooOld || AppUpdateRules.belowMin(build: AppUpdateRules.runningBuild, min: app.profile?.minAppVersion["ios"]) {
                UpdateRequiredView().zIndex(3)
            } else if app.pairAgain {
                // the server's KE2E key changed: nothing more goes to it until this phone is paired again
                PairAgainView(onPair: { app.signOut() }).zIndex(3)
            } else if let server = app.serverTooOld {
                ServerTooOldView(server: server, onRetry: { app.recheckServer() }).zIndex(3)
            }
        }
        .animation(KeloMotionSpec.enter(reduce), value: router.workOpen)
        .animation(KeloMotionSpec.enter(reduce), value: router.liveTab)
        .onChange(of: router.tab) { _, t in built.insert(t) }
        // the status poll and the notification check run while the app is in the foreground
        .task(id: phase == .active) {
            guard phase == .active else { return }
            await app.activity.poll { [weak app] in app?.api }
        }
        .task(id: phase == .active) {
            guard phase == .active else { return }
            await KeloBackground.run(app: app)
        }
        // the phone's location for the weather and "near me" questions: only when allowed, only now that the app is open
        .task(id: phase == .active) {
            guard phase == .active, let api = app.api else { return }
            await PhonePlace.maybeSend(api: api)
        }
        .task(id: app.profile?.avatarVersion) {
            if let v = app.profile?.avatarVersion { await app.avatar.sync(v, app: app) }
        }
        .onChange(of: app.activity.status?.avatarVersion) { _, v in
            if let v, v >= 0 { Task { await app.avatar.sync(v, app: app) } }
        }
        .onChange(of: phase) { _, p in if p == .background { KeloBackground.scheduleRefresh() } }
    }

    @ViewBuilder private func page(_ tab: KeloTab) -> some View {
        switch tab {
        case .chat: ChatScreen(cardSender: WalletCardSender(app: app)).environment(\.keloConnectPanel, ChatConnectPanel.builder)
        case .goals: GoalsScreen()
        case .connectors: ConnectorsScreen()
        case .my: MyScreen()
        }
    }
}

/// Lays out its one child at most `max` wide, at the child's own width when that is less (a `frame(maxWidth:)` would stretch).
private struct CapWidth: Layout {
    let max: CGFloat
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let v = subviews.first else { return .zero }
        return v.sizeThatFits(ProposedViewSize(width: min(proposal.width ?? max, max), height: proposal.height))
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}

/// Whether the keyboard is showing (iOS); the tab bar stays behind it.
private struct KeyboardWatch: ViewModifier {
    @Binding var up: Bool
    func body(content: Content) -> some View {
        #if os(iOS)
        content
            .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillShowNotification)) { _ in up = true }
            .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in up = false }
        #else
        content
        #endif
    }
}

/// The header's badge and pill sizes (Android HEADER_BADGE, HEADER_CHARACTER).
enum HeaderMetrics {
    static let badge: CGFloat = 68
    static let character: CGFloat = 68 * 0.92
}

/// The floating header: the agent at the top centre and its name in a small pill below, with nothing behind them (the page shows
/// through). Without a photo the agent is Kelo's character, whose drawing follows what the agent is doing; a photo gets a ring
/// that turns green while the agent works. A red dot means something waits for the owner (WorkBadge). VoiceOver reads the name,
/// what it is doing and how many things wait.
struct FloatingHeader: View {
    let onAgent: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    /// how far the badge sits above the pill's bottom: it covers only the pill's top edge, also with larger text
    @ScaledMetric(relativeTo: .subheadline) private var tuck: CGFloat = 21

    var body: some View {
        let activity = app.activity
        let name = app.profile?.agentName ?? ""
        let mood = activity.mood()
        let line = activity.line
        let waiting = activity.badge
        let news = waiting != nil
        let photo = app.avatar.image
        let words = [name, mood == .offline ? L(R.string.agent_offline) : nil, line,
                     waiting.map { _ in WaitingWords.of(activity.status) }]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ", ")
        Button(action: onAgent) {
            ZStack(alignment: .bottom) {
                // the name pill sits on the lower edge of the badge, so the two read as one
                // as wide as the name (at most 260), not stretched to the most
                CapWidth(max: 260) {
                    Text(name).font(KeloFont.titleSmall).foregroundStyle(c.ink).lineLimit(1).truncationMode(.tail)
                        .padding(.horizontal, 12).padding(.vertical, 3)
                }
                    .background(RoundedRectangle(cornerRadius: 14).fill(c.card)
                        .shadow(color: c.scrim.opacity(0.14), radius: 3, y: 1))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
                    .zIndex(0)
                ZStack(alignment: .topTrailing) {
                    ZStack {
                        Circle().fill(photo != nil ? c.card : KeloColors.badgeCream)
                        if let photo {
                            AgentPicture(name: name, image: photo, size: HeaderMetrics.badge)
                        } else {
                            KeloCharacterView(mood)
                                .frame(width: HeaderMetrics.character, height: HeaderMetrics.character)
                                .offset(x: HeaderMetrics.character * mood.centerShift / 120, y: 3)
                        }
                    }
                    .frame(width: HeaderMetrics.badge, height: HeaderMetrics.badge)
                    .clipShape(Circle())
                    .overlay(Circle().stroke(photo != nil && line != nil ? c.success : c.card, lineWidth: 2))
                    .shadow(color: c.scrim.opacity(0.18), radius: 6, y: 2)
                    if news {
                        Circle().fill(c.danger).frame(width: 8, height: 8)
                            .padding(2).background(Circle().fill(c.bg))
                            .offset(x: -2, y: 2)
                    }
                }
                .padding(.bottom, tuck)
                .zIndex(1)
            }
            .padding(.horizontal, 8).padding(.vertical, 2)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .padding(.top, 6).padding(.bottom, 6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(words)
        .accessibilityHint(L(R.string.agent_open))
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("kelo.header")
    }
}

/// Four destinations; the chosen one sits in a tinted pill. No fade or highlight on press, no animation on switching.
struct TabBar: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    /// 12 pt at the default text size, growing with the phone's text size like Android's 12 sp (Dynamic Type)
    @ScaledMetric(relativeTo: .caption) private var labelSize: CGFloat = 12

    var body: some View {
        VStack(spacing: 0) {
            Hairline()
            HStack(spacing: 0) {
                ForEach(KeloTab.allCases, id: \.self) { tab in
                    let on = app.router.tab == tab
                    Button { app.router.tap(tab) } label: {
                        VStack(spacing: 4) {
                            KeloIconView(tab.icon, size: 22, tint: on ? c.ink : c.muted)
                                .frame(width: 56, height: 30)
                                .background(Capsule().fill(on ? c.mine : .clear))
                            Text(tab.label).font(.system(size: labelSize, weight: on ? .semibold : .regular))
                                .foregroundStyle(on ? c.ink : c.muted)
                                .lineLimit(1).minimumScaleFactor(0.75).truncationMode(.tail)
                                .padding(.horizontal, 2)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.top, 8).padding(.bottom, 6)
                    }
                    .buttonStyle(NoFeedbackStyle())
                    .accessibilityLabel(tab.label)
                    .accessibilityAddTraits(on ? [.isSelected, .isButton] : .isButton)
                    .accessibilityIdentifier("kelo.tab.\(tab.rawValue)")
                }
            }
        }
        .background(c.bg.ignoresSafeArea(edges: .bottom))
        .transaction { $0.animation = nil }
        // the largest accessibility sizes would push the bar over half the page; the labels stop growing there (VoiceOver and
        // the large-content viewer still read them)
        .dynamicTypeSize(...DynamicTypeSize.accessibility2)
    }
}

/// The first thing a new owner sees on the chat while the server has no model (Android ChooseModelCard).
struct ChooseModelCard: View {
    let onChoose: () -> Void
    @Environment(\.kelo) private var c
    var body: some View {
        KeloCard {
            HStack(spacing: 10) {
                KeloIconView(.chat, size: 22, tint: c.accent)
                Text(L(R.string.model_first_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            MutedText(L(R.string.model_first_body))
            PrimaryButton(L(R.string.model_first_button), action: onChoose)
        }
        .shadow(color: c.scrim.opacity(0.15), radius: 8, y: 3)
        .accessibilityElement(children: .contain)
    }
}

/// What waits, for VoiceOver: "입력을 기다리는 키 1개, 답을 기다리는 질문 2개" (by kind; WorkBadge.parts).
@MainActor enum WaitingWords {
    static func of(_ status: AgentStatus?) -> String {
        WorkBadge.parts(status).map { p in
            let key: PluralKey
            switch p.kind {
            case WorkBadge.approvals: key = R.plurals.work_badge_approvals
            case WorkBadge.keys: key = R.plurals.work_badge_keys
            case WorkBadge.logins: key = R.plurals.work_badge_logins
            case WorkBadge.cards: key = R.plurals.work_badge_cards
            case WorkBadge.pins: key = R.plurals.work_badge_pins
            default: key = R.plurals.work_badge_waiting
            }
            return P(key, p.count, p.count)
        }.joined(separator: ", ")
    }
}
