import SwiftUI
import KeloCore
import KeloShared

/// One browser tab, full screen and live (Android agent/BrowserPages.kt BrowserTabPage + LivePage.kt LiveTabPage). The picture
/// follows the tab as the agent works in it; pinch to zoom and drag to look around, double tap to zoom. "Take control" hands the
/// tab to this phone: the agent waits, taps and drags go to the page (a drag scrolls it), and a text line, keys and back /
/// forward / reload come up; "Hand back" returns it. Leaving the page hands it back too. When nothing live can be shown (the tab
/// closed) the newest picture kept of it shows instead.
struct BrowserTabPage: View {
    let id: String
    /// Opened to solve a human check: take the tab over once, as soon as the first picture is there and nobody else holds it.
    var takeControl = false
    let onBack: () -> Void
    @State private var live: LiveModel
    @State private var autoTaken = false
    @State private var confirmClose = false
    @State private var model: WorkModel
    @Environment(\.scenePhase) private var phase
    @Environment(\.kelo) private var c
    @Environment(AppModel.self) private var app
    private var activity: AgentActivityModel { app.activity }

    init(api: KeloApi, id: String, takeControl: Bool = false, onBack: @escaping () -> Void) {
        self.id = id; self.takeControl = takeControl; self.onBack = onBack
        _live = State(initialValue: LiveModel(api: api, tabId: id))
        _model = State(initialValue: WorkModel(api: { [api] in api }))
    }

    private var tab: BrowserTab? { live.tab }

    var body: some View {
        let controlling = live.status.youControl
        let device = LiveKinds.isDevice(id)   // the Android device: a portrait screen, Back / Home / Recents, no tab to close
        let title = device ? L(R.string.chat_phone_title)
            : (firstNonEmpty(live.frame?.title, tab?.title, tab?.host) ?? L(R.string.agent_tab_browser))
        let host = device ? "" : (firstNonEmpty(live.frame.map { hostOf($0.url) }, tab?.host) ?? "")
        VStack(spacing: 0) {
            top(title: title, host: host, canClose: !device && !controlling && live.mode != .ended)
            if live.mode != .ended { strip }
            if controlling { banner }
            ZStack(alignment: .bottom) {
                Group {
                    if live.mode == .ended && live.end != .noBrowser {
                        VStack(alignment: .leading, spacing: 10) {
                            note(L(R.string.live_tab_closed))
                            ScreenshotView(model: model, id: id)
                        }
                        .padding(.horizontal, 4)
                    } else if live.mode == .ended {
                        ScreenshotView(model: model, id: id)
                    } else {
                        LiveCanvas(live: live, controlling: controlling, device: device)
                    }
                }
                if let n = live.notice {
                    Text(n).font(KeloFont.small).foregroundStyle(c.bg).fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: 12).fill(c.ink))
                        .padding(12)
                        .task(id: n) {
                            try? await Task.sleep(nanoseconds: 3_500_000_000)
                            if live.notice == n { live.notice = nil }
                        }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(.horizontal, 8)
            if controlling {
                ControlBar(live: live, device: device)
            } else if live.mode != .ended {
                let other = live.status.someoneElse
                PrimaryButton(L(other ? R.string.live_held_elsewhere : R.string.live_take),
                              enabled: !other && !live.controlBusy && live.frame != nil) {
                    Task { await take() }
                }
                .padding(12)
            }
        }
        .background(c.bg.ignoresSafeArea())
        .keloSensitive(when: LiveRules.guardScreen(secure: live.frame?.secure, typing: controlling))
        .keloHideNavigationBar()
        .workToast($model.toast)
        .onAppear {
            live.start()
        }
        .onDisappear { live.stop(giveBack: true) }
        .onChange(of: live.mode) { _, mode in
            if mode == .ended && live.end != .noBrowser { activity.workChanged += 1 }
        }
        .onChange(of: phase) { _, p in
            if p == .active { live.start() } else if p == .background { live.stop(giveBack: false) }
        }
        .task { await live.loadTab() }
        .task(id: AutoTakeKey(hasFrame: live.frame != nil, controlling: controlling, someoneElse: live.status.someoneElse)) {
            guard LiveRules.autoTake(asked: takeControl, done: autoTaken, hasFrame: live.frame != nil, controlling: controlling,
                                     someoneElse: live.status.someoneElse, busy: live.controlBusy) else { return }
            autoTaken = true
            await take()
        }
        .keloConfirm(L(R.string.agent_tab_close_confirm), isPresented: $confirmClose, yes: L(R.string.agent_tab_close)) {
            model.closeTab(id, then: onBack)
        }
    }

    /// What the auto take-over waits on (Android LivePage's LaunchedEffect keys).
    private struct AutoTakeKey: Equatable { let hasFrame: Bool, controlling: Bool, someoneElse: Bool }

    private func take() async {
        switch await live.control(true) {
        case .held: live.notice = L(R.string.live_held_elsewhere)
        case .gone: live.notice = L(R.string.live_tab_closed)
        case .failed: live.notice = L(R.string.live_input_failed)
        case .ok: Haptics.success()
        }
    }

    private func top(title: String, host: String, canClose: Bool) -> some View {
        HStack(spacing: 4) {
            Button(action: onBack) { KeloIconView(.chevronLeft, size: 24, tint: c.ink).frame(width: 44, height: 44) }
                .buttonStyle(.plain).accessibilityLabel(L(R.string.back))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(.callout, weight: .bold)).foregroundStyle(c.ink).lineLimit(1)
                if !host.isEmpty { Text(host).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if canClose {
                Button { confirmClose = true } label: { KeloIconView(.trash, size: 22, tint: c.muted).frame(width: 44, height: 44) }
                    .buttonStyle(.plain).accessibilityLabel(L(R.string.agent_tab_close))
            }
        }
        .padding(.horizontal, 8).padding(.top, 6).padding(.bottom, 2)
    }

    /// What the agent is doing, who is watching, who holds the tab.
    private var strip: some View {
        let s = live.status
        let (word, tone): (String, Color) = {
            if s.youControl { return (L(R.string.live_state_you), c.accent) }
            if s.someoneElse { return (L(R.string.live_state_other), c.muted) }
            if s.agent.waitingApproval { return (L(R.string.live_state_waiting), c.accent) }
            if s.agent.busy || tab?.status == "working" { return (L(R.string.agent_tab_working), c.accent) }
            return (L(R.string.agent_tab_idle), c.muted)
        }()
        let line: String = switch live.mode {
        case .connecting: L(R.string.live_connecting)
        case .waiting: L(R.string.live_reconnecting)
        default: firstNonEmpty(s.label, s.agent.status, tab?.statusText) ?? ""
        }
        return FlowLayout(spacing: 8, lineSpacing: 4) {
            pill(word, tone)
            if !line.isEmpty { Text(line).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2) }
            if live.mode == .polling { pill(L(R.string.live_slow_mode), c.muted) }
            if s.viewers > 0 {
                HStack(spacing: 2) {
                    KeloIconView(.eye, size: 16, tint: c.muted)
                    Text(String(s.viewers)).font(KeloFont.label).foregroundStyle(c.muted)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(P(R.plurals.live_viewers, s.viewers, s.viewers))
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 4)
    }

    private func pill(_ text: String, _ tone: Color) -> some View {
        Text(text).font(.system(.caption2, weight: .semibold)).foregroundStyle(tone).lineLimit(1)
            .padding(.horizontal, 8).padding(.vertical, 3).background(Capsule().fill(tone.opacity(0.12)))
    }

    private func note(_ text: String) -> some View {
        HStack(spacing: 10) {
            KeloIconView(.alert, size: 20, tint: c.muted)
            Text(text).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.card))
    }

    /// While the user holds the tab: the agent waits; one tap gives it back.
    private var banner: some View {
        HStack(spacing: 10) {
            KeloIconView(.hand, size: 20, tint: c.accent)
            Text(L(R.string.live_paused_banner)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button { Task { _ = await live.control(false) } } label: {
                Text(L(R.string.live_give_back)).font(.system(.subheadline, weight: .semibold)).foregroundStyle(c.bg)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Capsule().fill(live.controlBusy ? c.muted : c.ink))
            }
            .buttonStyle(.plain)
            .disabled(live.controlBusy)
        }
        .padding(.leading, 14).padding(.trailing, 6).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.accent.opacity(0.14)))
        .padding(.horizontal, 12).padding(.vertical, 4)
    }

    private func firstNonEmpty(_ xs: String?...) -> String? { xs.compactMap { $0 }.first { !$0.isEmpty } }
    private func hostOf(_ url: String) -> String {
        guard let h = URL(string: url)?.host else { return "" }
        return h.hasPrefix("www.") ? String(h.dropFirst(4)) : h
    }
}

/// The picture. Watching: pinch to zoom (1x to 5x), drag to move, double tap to zoom in or out. Controlling: a tap or a long press
/// goes to that point of the page and a drag scrolls it (sent at most every 50 ms); the zoom stays and is accounted for.
private struct LiveCanvas: View {
    let live: LiveModel
    let controlling: Bool
    var device = false
    @State private var zoom: Double = 1
    @State private var baseZoom: Double = 1
    @State private var pan: CGSize = .zero
    @State private var basePan: CGSize = .zero
    @State private var size: CGSize = .zero
    @State private var pending = PendingScroll()
    @State private var pressStart: (at: CGPoint, time: Date, moved: Bool)?
    @State private var dragEnd: CGPoint = .zero
    @State private var lastDrag: CGSize = .zero
    @State private var lastPointerAt: Date? = nil
    @State private var pointerExpired: Bool = false
    @Environment(\.kelo) private var c

    struct PendingScroll { var dx = 0.0; var dy = 0.0; var x = 0.0; var y = 0.0 }

    var body: some View {
        GeometryReader { g in
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(c.bubble)
                if let f = live.frame {
                    Image(platformImage: f.image).resizable().scaledToFit()
                        .scaleEffect(zoom).offset(pan)
                        .accessibilityLabel(L(R.string.agent_tab_picture))
                    if live.isPageBusy {
                        Color.black.opacity(0.22)
                            .allowsHitTesting(false)
                    }
                } else {
                    ProgressView().tint(c.muted)
                }

                if let p = live.pointer, !controlling, !pointerExpired {
                    LivePointerView(pointer: p, frame: live.frame, viewSize: size, zoom: zoom, pan: pan)
                        .allowsHitTesting(false)
                }

                if live.isPageBusy && live.frame != nil {
                    PageBusyBanner(live: live)
                        .padding(12)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .contentShape(Rectangle())
            .gesture(controlling ? AnyGesture(controlGesture.map { _ in () }) : AnyGesture(watchGesture.map { _ in () }))
            .onAppear { size = g.size }
            .onChange(of: g.size) { _, s in size = s }
            .onChange(of: live.pointer) { _, p in
                if p != nil {
                    lastPointerAt = Date()
                    pointerExpired = false
                }
            }
            .task(id: lastPointerAt) {
                guard lastPointerAt != nil else { return }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                pointerExpired = true
            }
        }
        .task(id: controlling) {
            // drags add up and leave as one scroll every 50 ms
            while controlling && !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 50_000_000)
                guard let f = live.frame, pending.dx != 0 || pending.dy != 0 else { continue }
                let d = LiveGeometry.scrollDelta(dx: pending.dx, dy: pending.dy, viewW: size.width, viewH: size.height,
                                                 frameW: f.w, frameH: f.h, zoom: zoom)
                let at = LiveGeometry.toFrame(x: pending.x, y: pending.y, viewW: size.width, viewH: size.height, frameW: f.w, frameH: f.h,
                                              zoom: zoom, panX: pan.width, panY: pan.height)
                pending.dx = 0; pending.dy = 0
                if d.x != 0 || d.y != 0 { live.send(LiveMessages.scroll(d, at: at, frameW: f.w, frameH: f.h)) }
            }
        }
    }

    private var watchGesture: some Gesture {
        let pinch = MagnificationGesture()
            .onChanged { v in
                zoom = LiveGeometry.clampZoom(baseZoom * v)
                clampPan(pan)
            }
            .onEnded { _ in baseZoom = zoom; basePan = pan }
        let drag = DragGesture(minimumDistance: 4)
            .onChanged { v in clampPan(CGSize(width: basePan.width + v.translation.width, height: basePan.height + v.translation.height)) }
            .onEnded { _ in basePan = pan }
        let double = TapGesture(count: 2).onEnded {
            if zoom > 1.01 { zoom = 1; pan = .zero } else { zoom = 2.5 }
            baseZoom = zoom; basePan = pan
        }
        return SimultaneousGesture(SimultaneousGesture(pinch, drag), double)
    }

    private func clampPan(_ p: CGSize) {
        let (x, y) = LiveGeometry.clampPan(panX: p.width, panY: p.height, zoom: zoom, viewW: size.width, viewH: size.height)
        pan = CGSize(width: x, height: y)
    }

    /// One gesture for tap, long press and drag: no movement → a tap (or a long press after half a second); movement → scroll.
    private var controlGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { v in
                if pressStart == nil { pressStart = (v.startLocation, Date(), false); lastDrag = .zero }
                let moved = abs(v.translation.width) > 8 || abs(v.translation.height) > 8
                if moved { pressStart?.moved = true }
                dragEnd = v.location
                if pressStart?.moved == true && !device {
                    pending.dx += v.translation.width - lastDrag.width
                    pending.dy += v.translation.height - lastDrag.height
                    pending.x = v.location.x; pending.y = v.location.y
                    lastDrag = v.translation
                }
            }
            .onEnded { v in
                defer { pressStart = nil; lastDrag = .zero }
                if device, let start = pressStart, start.moved, let f = live.frame {
                    // the device has no wheel scroll: a drag is one swipe from where the finger went down to where it came up
                    let a = LiveGeometry.toFrame(x: start.at.x, y: start.at.y, viewW: size.width, viewH: size.height, frameW: f.w, frameH: f.h,
                                                 zoom: zoom, panX: pan.width, panY: pan.height)
                    let b = LiveGeometry.toFrame(x: dragEnd.x, y: dragEnd.y, viewW: size.width, viewH: size.height, frameW: f.w, frameH: f.h,
                                                 zoom: zoom, panX: pan.width, panY: pan.height)
                    if let a, let b {
                        let ms = LiveKinds.swipeMs(Int(Date().timeIntervalSince(start.time) * 1000))
                        live.send(LiveMessages.swipe(from: a, to: b, frameW: f.w, frameH: f.h, ms: ms))
                    }
                    return
                }
                guard let start = pressStart, !start.moved, let f = live.frame else { return }
                guard let p = LiveGeometry.toFrame(x: v.location.x, y: v.location.y, viewW: size.width, viewH: size.height,
                                                   frameW: f.w, frameH: f.h, zoom: zoom, panX: pan.width, panY: pan.height) else { return }
                Haptics.tick()
                if !device && Date().timeIntervalSince(start.time) >= 0.5 {
                    live.send(LiveMessages.longPress(p, frameW: f.w, frameH: f.h))
                } else {
                    live.send(LiveMessages.tap(p, frameW: f.w, frameH: f.h))
                }
            }
    }
}

/// Keys, back / forward / reload, and a line to type into the page (nothing typed is kept or logged).
private struct ControlBar: View {
    let live: LiveModel
    var device = false
    @State private var text = ""
    @State private var hidden = false
    @Environment(\.kelo) private var c

    private static let keys: [(String, StringKey, StringKey)] = [
        ("Enter", R.string.live_key_enter, R.string.live_key_enter_desc), ("Backspace", R.string.live_key_backspace, R.string.live_key_backspace_desc),
        ("Tab", R.string.live_key_tab, R.string.live_key_tab_desc), ("Escape", R.string.live_key_escape, R.string.live_key_escape_desc),
        ("ArrowLeft", R.string.live_key_left, R.string.live_key_left_desc), ("ArrowUp", R.string.live_key_up, R.string.live_key_up_desc),
        ("ArrowDown", R.string.live_key_down, R.string.live_key_down_desc), ("ArrowRight", R.string.live_key_right, R.string.live_key_right_desc),
    ]

    var body: some View {
        VStack(spacing: 6) {
            if device { phoneKeys } else {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    navKey(.arrowLeft, R.string.live_back) { live.send(LiveMessages.nav("back")) }
                    navKey(.arrowRight, R.string.live_forward) { live.send(LiveMessages.nav("forward")) }
                    navKey(.retry, R.string.live_reload) { live.send(LiveMessages.nav("reload")) }
                    ForEach(ControlBar.keys, id: \.0) { key, label, desc in
                        Button { live.send(LiveMessages.key(key)) } label: {
                            Text(L(label)).font(KeloFont.button).foregroundStyle(c.ink).lineLimit(1)
                                .frame(minWidth: 52, minHeight: 40).padding(.horizontal, 4)
                                .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(L(desc))
                    }
                }
            }
            }
            HStack(spacing: 6) {
                KeloTextField(L(device ? R.string.live_phone_type_hint : R.string.live_type_hint), text: $text, secure: hidden, keyboard: .ascii, maxLength: LiveMessages.maxText, onSubmit: sendText)
                Button { hidden.toggle() } label: { KeloIconView(hidden ? .lock : .eye, size: 22, tint: c.muted).frame(width: 40, height: 40) }
                    .buttonStyle(.plain)
                    .accessibilityLabel(L(hidden ? R.string.live_type_show : R.string.live_type_hide))
                Button(action: sendText) { KeloIconView(.send, size: 22, tint: text.isEmpty ? c.muted : c.accent).frame(width: 40, height: 40) }
                    .buttonStyle(.plain)
                    .disabled(text.isEmpty)
                    .accessibilityLabel(L(R.string.live_type_send))
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(c.card)
    }

    private func sendText() {
        guard !text.isEmpty else { return }
        live.type(text)
        text = ""
    }

    /// The device's Back / Home / Recents under its screen: an icon and its word each (the word is also what a screen reader says).
    private var phoneKeys: some View {
        let keys: [(String, KeloIcon, StringKey)] = [
            (LiveKinds.phoneKeys[0], .arrowLeft, R.string.live_phone_back), (LiveKinds.phoneKeys[1], .home, R.string.live_phone_home),
            (LiveKinds.phoneKeys[2], .recents, R.string.live_phone_recents),
        ]
        return HStack(spacing: 8) {
            ForEach(keys, id: \.0) { key, icon, label in
                Button { live.send(LiveMessages.deviceKey(key)) } label: {
                    HStack(spacing: 4) {
                        KeloIconView(icon, size: 20, tint: c.ink)
                        Text(L(label)).font(KeloFont.button).foregroundStyle(c.ink).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L(label))
            }
        }
    }

    private func navKey(_ icon: KeloIcon, _ desc: StringKey, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            KeloIconView(icon, size: 20, tint: c.ink).frame(width: 40, height: 40)
                .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L(desc))
    }
}

/// The newest picture kept of a tab (when there is no live view).
private struct ScreenshotView: View {
    let id: String
    @State private var shot: ScreenshotModel
    @Environment(\.kelo) private var c

    init(model: WorkModel, id: String) {
        self.id = id
        _shot = State(initialValue: ScreenshotModel(work: model, id: id))
    }

    private var image: PlatformImage? { shot.image }
    private var loading: Bool { shot.loading }
    private var problem: String? { shot.problem }

    var body: some View {
        Group {
            if let image {
                ScrollView {
                    Image(platformImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 12))
                        .accessibilityLabel(L(R.string.agent_tab_picture))
                }
            } else if loading {
                ProgressView().tint(c.muted).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let problem {
                MutedText(problem).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: id) { await shot.load() }
    }
}

/// An agent pointer overlay: smooth movement between updates (~80 ms), ripple on down == true.
private struct LivePointerView: View {
    let pointer: LivePointer
    let frame: LiveShown?
    let viewSize: CGSize
    let zoom: Double
    let pan: CGSize

    @State private var targetX: Double = 0
    @State private var targetY: Double = 0
    @State private var rippleScale: Double = 0
    @State private var rippleOpacity: Double = 0

    var body: some View {
        if let f = frame,
           let pt = LiveGeometry.pointerToView(
               cssX: pointer.x,
               cssY: pointer.y,
               viewW: viewSize.width,
               viewH: viewSize.height,
               frameW: f.w,
               frameH: f.h,
               cssW: f.cssW,
               cssH: f.cssH,
               zoom: zoom,
               panX: pan.width,
               panY: pan.height
           ) {
            ZStack {
                if pointer.down {
                    Circle()
                        .stroke(Color.white.opacity(rippleOpacity), lineWidth: 2)
                        .frame(width: 32, height: 32)
                        .scaleEffect(rippleScale)
                        .position(x: targetX, y: targetY)
                }
                LivePointerArrow()
                    .position(x: targetX + 7, y: targetY + 10)
            }
            .onAppear {
                targetX = pt.x
                targetY = pt.y
                if pointer.down { triggerRipple() }
            }
            .onChange(of: pt.x) { _, newX in
                withAnimation(.linear(duration: 0.08)) {
                    targetX = newX
                    targetY = pt.y
                }
            }
            .onChange(of: pt.y) { _, newY in
                withAnimation(.linear(duration: 0.08)) {
                    targetX = pt.x
                    targetY = newY
                }
            }
            .onChange(of: pointer.down) { _, isDown in
                if isDown { triggerRipple() }
            }
        }
    }

    private func triggerRipple() {
        rippleScale = 0.2
        rippleOpacity = 0.6
        withAnimation(.easeOut(duration: 0.3)) {
            rippleScale = 1.0
            rippleOpacity = 0.0
        }
    }
}

/// Small arrow cursor: white arrow with dark outline stroke, matching Kelo's look.
private struct LivePointerArrow: View {
    var body: some View {
        LiveArrowShape()
            .fill(Color.white)
            .overlay(
                LiveArrowShape()
                    .stroke(Color(white: 0.12), lineWidth: 1.5)
            )
            .frame(width: 14, height: 20)
            .shadow(color: Color.black.opacity(0.25), radius: 2, x: 0, y: 1)
    }
}

private struct LiveArrowShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let w = rect.width
        let h = rect.height
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + h * 0.8))
        path.addLine(to: CGPoint(x: rect.minX + w * 0.36, y: rect.minY + h * 0.6))
        path.addLine(to: CGPoint(x: rect.minX + w * 0.68, y: rect.minY + h * 0.95))
        path.addLine(to: CGPoint(x: rect.minX + w * 0.88, y: rect.minY + h * 0.875))
        path.addLine(to: CGPoint(x: rect.minX + w * 0.56, y: rect.minY + h * 0.525))
        path.addLine(to: CGPoint(x: rect.minX + w, y: rect.minY + h * 0.525))
        path.closeSubpath()
        return path
    }
}

/// Small banner shown when page is busy or stale ("The page is not responding") with reload control.
private struct PageBusyBanner: View {
    let live: LiveModel
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            KeloIconView(.alert, size: 16, tint: c.ink)
            Text(L(R.string.live_page_not_responding))
                .font(KeloFont.small)
                .foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                live.send(LiveMessages.nav("reload"))
            } label: {
                KeloIconView(.retry, size: 16, tint: c.ink)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L(R.string.live_reload))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        .shadow(color: Color.black.opacity(0.1), radius: 4, x: 0, y: 2)
    }
}
