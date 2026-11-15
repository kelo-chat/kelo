import SwiftUI
import KeloCore
import KeloShared

/// 채팅 tab (Android chat/ChatScreen.kt). Entry point the shell uses; keep this signature.
///
/// The messages scroll under the floating header; the composer sits at the bottom (above the tab bar, which steps aside while the
/// composer has the keyboard). Another phone talking to the same agent shows up within a few seconds while this screen is idle.
public struct ChatScreen: View {
    @Environment(AppModel.self) private var app
    @State private var model: ChatModel?

    /// Sends a card stored only on this phone for a payment approval (the wallet's end-to-end key; the shell passes it in).
    let cardSender: ChatCardSender?
    public init(cardSender: ChatCardSender? = nil) { self.cardSender = cardSender }

    public var body: some View {
        NavigationStack(path: app.router.path(.chat)) {
            Group {
                if let model {
                    ChatPage(model: model)
                } else {
                    Color.clear
                }
            }
            .keloShellInsets()
            .keloHideNavigationBar()
        }
        .onAppear { if model == nil { model = ChatModel(app: app, cardSender: cardSender) } }
        .onChange(of: app.historyCleared) { _, _ in model?.forgetHistory() }
    }
}

/// A sheet the chat shows.
private enum ChatSheet: Identifiable {
    case chooser, modelSheet, select(String), report(String)
    var id: String {
        switch self {
        case .chooser: "chooser"
        case .modelSheet: "model"
        case .select(let t): "select:\(t.hashValue)"
        case .report(let k): "report:\(k)"
        }
    }
}

struct ChatPage: View {
    @Bindable var model: ChatModel
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduce
    @Environment(\.openURL) private var openURL
    @State private var sheet: ChatSheet?
    @State private var atBottom = true
    /// The floating composer's height (with its scrim): when it grows or shrinks, a list that holds on to the newest row follows.
    @State private var composerHeight: CGFloat = 0
    /// The list keeps the newest row in view (until the reader pulls it down to read older rows)
    @State private var stick = true
    @State private var unread = false
    @State private var seenSignature = ""
    @State private var reportDrafts: [String: ReportDraft] = [:]
    @State private var thanks = false
    @State private var picker = AttachmentPickers()
    @State private var attachOpen = false
    @State private var askNotify = false
    /// The notification question is due, but waits until the server has taken the message (see `askNotifyIfTaken`)
    @State private var notifyPending = false
    @State private var composerFocused = false
    /// A secure entry's card a row in 답이 필요해요 led to: marked for a moment
    @State private var markedEntry: String?

    var body: some View {
        // The composer floats over the messages (a bottom safe-area inset: the list keeps room for its measured height and scrolls
        // on under it); the messages fade out in its scrim instead of being cut off by an opaque band. The "↓" is part of the
        // composer: it sits on the card's top border (ChatOverlayRules), never over a message.
        // At the top the chat is like every other page: the messages scroll on under the floating agent and its name tag (only the
        // shell's short status-bar fade above them); the list's top inset starts the first message below the tag.
        ZStack(alignment: .bottom) {
            messages
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: 0) {
                AttachmentTray(items: model.attachments, onRemove: model.removeAttachment)
                Composer(model: model, focused: $composerFocused,
                         onAttach: { composerFocused = false; attachOpen = true },
                         onChip: { composerFocused = false; sheet = .chooser })
            }
            .overlay(alignment: .top) {
                // on the card's top border at the trailing side, centred over 보내기; on top of the attachments while they wait
                let drop = ChatOverlayRules.jumpDrop(cardTop: Composer.topRoom, tray: !model.attachments.isEmpty)
                HStack(spacing: 0) {
                    Spacer(minLength: 0)
                    if pillShown {
                        JumpPill(unread: unread) { scrollToBottomRequest += 1 }
                            .transition(.opacity.combined(with: .scale(scale: 0.8)))
                    }
                }
                .padding(.trailing, Composer.sideRoom + Composer.jumpInset)
                .frame(maxWidth: Composer.maxWidth).frame(maxWidth: .infinity)
                .alignmentGuide(.top) { d in d.height - drop }
            }
            .padding(.top, ComposerScrim.fade)
            .background(ComposerScrim(bg: c.bg))
            .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { composerHeight = $0 }
        }
        .overlay(alignment: .top) {
            StaleHint(visible: model.offline && !model.busy, onRetry: model.reload)
        }
        .overlay(alignment: .bottom) {
            if thanks {
                Text(L(R.string.report_thanks)).font(KeloFont.small).foregroundStyle(c.bg)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Capsule().fill(c.ink))
                    .padding(.bottom, 90).padding(.horizontal, 16)
                    .transition(.opacity)
            }
        }
        .background(c.bg.ignoresSafeArea())
        .onAppear {
            model.onEvent = { e in
                switch e {
                case .sent:
                    Haptics.tick()
                    // after the first message: shall Kelo say when a reply is ready? (once per phone)
                    Task { if await NotifyAsk.takeTurn() { notifyPending = true; askNotifyIfTaken() } }
                case .replyDone, .paymentAuto: Haptics.success()
                case .error: Haptics.warning()
                }
            }
            model.start()
            model.refreshAsks(force: true)   // a background job's question waiting since before the chat was shown
            takeShared()
            takeCardKept()
            model.loadModelList(provider: app.profile?.provider ?? "")
        }
        .onChange(of: app.profile?.provider ?? "") { _, p in model.loadModelList(provider: p) }
        .onChange(of: composerFocused) { _, f in app.router.hideChrome(Router.keyboardAsker, f) }
        // the keyboard goes when another page covers the chat (the Work page) or a row there leads back to a card
        .onChange(of: app.router.workOpen) { _, open in if open { composerFocused = false } }
        .onChange(of: app.router.chatEntryFocus) { _, id in if id != nil { composerFocused = false } }
        .onDisappear {
            app.router.hideChrome(Router.keyboardAsker, false)
            model.saveDraftNow()
        }
        .onChange(of: scenePhase) { _, p in
            if p != .active { model.saveDraftNow(); composerFocused = false }
            // back in the app: the open follow-ups, a waiting question and a fill-in card still open (Android ON_RESUME)
            if p == .active { model.loadFollowups(); model.refreshSecrets(); model.refreshAsks(force: true); takeShared() }
        }
        .modifier(ChatStatusFollow(model: model, status: app.activity.status))
        // another phone may have talked to the same agent: keep in step while idle and in view
        .task(id: scenePhase == .active) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                await model.checkForNews()
                try? await Task.sleep(nanoseconds: 4_000_000_000)
            }
        }
        .keloSheet(item: $sheet, detents: [.large]) { s in sheetView(s) }
        .background {
            // "+" opens a short sheet of its own
            Color.clear.keloSheet(isPresented: $attachOpen, detents: [.medium, .large]) {
                AttachSheet { source in
                    attachOpen = false
                    picker.open(source, model: model)
                }
            }
        }
        .background {
            // a message the server held back for a key or password (SecretGuard): save it, send it as is, or edit it
            Color.clear.keloSheet(item: Binding(get: { model.secretAsk }, set: { if $0 == nil { model.secretEdit() } }),
                                  detents: [.medium, .large]) { ask in
                SecretSheet(ask: ask,
                            onSaved: { value, reference in model.secretSaved(value: value, reference: reference) },
                            onCard: {
                                model.secretToCard(app.secretHandoff)
                                app.router.openMyPage(.walletAdd)
                            },
                            onSendAsIs: model.secretSendAsIs,
                            onEdit: model.secretEdit)
            }
        }
        // the one question before chats first go to an AI service (AiConsent): Agree sends the message, Not now keeps it here
        .modifier(ConsentSheetHost(model: model))
        // the wallet's card form kept a card number from such a message: its reference goes into the composer
        .onChange(of: app.secretCardKeptTick) { _, _ in takeCardKept() }
        .onChange(of: [model.secretAsk != nil, model.consentAsk != nil, model.busy, model.phase?.kind == .waiting]) { _, _ in askNotifyIfTaken() }
        .modifier(picker.modifiers(model: model))
        .alert(L(R.string.notify_ask_title), isPresented: $askNotify) {
            Button(L(R.string.notify_ask_yes)) { Task { await NotifyAsk.enable() } }
            Button(L(R.string.notify_ask_later), role: .cancel) {}
        } message: {
            Text(L(R.string.notify_ask_body, agentName))
        }
    }

    @State private var scrollToBottomRequest = 0

    /// Shows the notification question once the server has taken the message (the reply started or ended). A message held back
    /// for a key or password was not sent: no question over the secret sheet (an alert first would keep that sheet from opening),
    /// it waits for the next message, as on Android.
    private func askNotifyIfTaken() {
        guard notifyPending else { return }
        if model.secretAsk != nil || model.consentAsk != nil {
            notifyPending = false
            NotifyAsk.giveBack()
        } else if !model.busy || model.phase?.kind != .waiting {
            notifyPending = false
            askNotify = true
        }
    }

    private func takeCardKept() {
        if app.secretHandoff.takeSaved() { model.secretCardSaved(reference: L(R.string.secret_saved_ref, L(R.string.secret_use_card))) }
    }

    /// What "Send to Kelo" in another app's share sheet left: the text goes into the draft, a file is uploaded like "+" does.
    private func takeShared() {
        guard let shared = ShareInbox.take() else { return }
        if !shared.text.isEmpty { model.receive(text: shared.text) }
        if let file = shared.file {
            model.attach(image: false, label: String(file.lastPathComponent.drop { $0 != "-" }.dropFirst())) {
                try FilePrep.prepare(file, temporary: true)
            }
        }
        composerFocused = true
    }

    @ViewBuilder
    private func sheetView(_ s: ChatSheet) -> some View {
        switch s {
        case .chooser:
            ModelChooser(model: model, onMore: { sheet = .modelSheet }, onClose: { sheet = nil })
        case .modelSheet:
            ModelSheet(onClose: { sheet = nil }, onSaved: { model.loadProviders() })
        case .select(let text):
            SelectTextPage(text: text) { sheet = nil }
        case .report(let key):
            if let reply = ReportRules.replyText(model.items, key: key) {
                ReportSheet(reply: reply, asked: ReportRules.askedBefore(model.items, key: key),
                            draft: reportDrafts[key] ?? ReportDraft(),
                            onDraft: { reportDrafts[key] = $0 },
                            onSent: {
                                reportDrafts[key] = nil
                                sheet = nil
                                withAnimation { thanks = true }
                                Task { try? await Task.sleep(nanoseconds: 3_000_000_000); withAnimation { thanks = false } }
                            },
                            onClose: { sheet = nil })
            }
        }
    }

    /// A tap on the messages lets go of the keyboard (the list's swipe does nothing when the list is short); taps on buttons and links still work
    private func dismissKeyboard() {
        composerFocused = false
        #if os(iOS)
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
        #endif
    }

    private var agentName: String { app.profile?.agentName ?? "Kelo" }

    // ---- the list -----------------------------------------------------------------------------------------------------------

    @ViewBuilder
    private var messages: some View {
        let asks = ChatAsks.visible(model.asks, items: model.items)
        // the messages that start a new stretch (first shown, a new day, or TimeLabels.gapMinutes after the one before) get a time line
        let timed = TimeLabels.labelled(model.items, zone: .current)
        if model.loaded && model.rows.isEmpty && model.phase == nil && asks.isEmpty {
            ScrollView {
                Text(L(R.string.chat_hint, agentName)).font(KeloFont.bodyLarge).foregroundStyle(c.muted)
                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity).padding(24).padding(.top, 60)
            }
            .scrollDismissesKeyboard(.immediately)
            .scrollBounceBehavior(.always)
            .simultaneousGesture(TapGesture().onEnded { dismissKeyboard() })
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    // two lazy stacks: the history, and what lives at its end (the status line with its moving dots, payment steps,
                    // PINs, questions). Anything that moves on every frame inside the history's stack made it place all its rows
                    // again on every frame, which held the main thread in a long chat; in its own small stack it costs nothing.
                    VStack(alignment: .leading, spacing: 8) {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            Color.clear.frame(height: 1).onAppear { model.loadOlder() }
                            if model.loadingOlder { ProgressView().frame(maxWidth: .infinity) }
                            ForEach(model.rows) { item in
                                VStack(alignment: .leading, spacing: 6) {
                                    if timed.contains(item.key), let m = item.asMsg { TimeLine(atMs: m.at) }
                                    row(item)
                                }
                                .id(item.key)
                                .modifier(RiseIn(active: model.takeFresh(item.key) && !reduce, fromEnd: item.isMine))
                            }
                        }
                        LazyVStack(alignment: .leading, spacing: 8) {
                            if let p = model.phase { StatusLine(phase: p) }
                            // payment steps that need the owner, and PINs waiting for this phone: time-critical, near the very bottom
                            ForEach(PaymentStepRules.visible(model.paymentSteps)) { step in
                                PaymentStepCard(step: step, model: model).id("step:" + step.stepId)
                            }
                            ForEach(model.pinNeeds.suffix(2)) { row in
                                PinNeedCard(row: row) { Haptics.tick(); model.releasePin(row.need.slotId) }.id("pin:" + row.need.slotId)
                            }
                            // questions from work the chat is not following (a background job, a scheduled run), live at the bottom
                            ForEach(asks) { ask in
                                ChatAskCard(ask: ask, onSolve: {
                                    Haptics.tick()
                                    app.router.openLive(ask.approval.tab, takeControl: true)
                                }) { ok, rememberSite, rememberSchedule in
                                    if ok, let card = ask.approval.event.card, card.needsCard {
                                        Haptics.tick()
                                        model.answerAsk(ask.id, approve: true, cardId: card.card.id)
                                    } else {
                                        if ok { Haptics.success() } else { Haptics.warning() }
                                        model.answerAsk(ask.id, approve: ok, rememberSite: rememberSite, rememberSchedule: rememberSchedule)
                                    }
                                }
                                .id("ask:" + ask.id)
                                .transition(.opacity)
                            }
                            // pulled up a little: the list keeps only `listEnd` after its last row (ChatOverlayRules.listGap)
                            Color.clear.frame(height: 1).padding(.top, -3).id(ChatPage.bottomId)
                                .onAppear { if !ChatScroll.measures { reachedBottom(true) } }
                                .onDisappear { if !ChatScroll.measures { reachedBottom(false) } }
                        }
                    }
                    .padding(.horizontal, 14).padding(.top, 10)
                    .frame(maxWidth: 760).frame(maxWidth: .infinity)
                }
                // pulling the list down (towards older rows) lets go of the newest; reaching the end again holds on to it. The
                // end's appear / disappear alone is not enough: a growing reply or the keyboard can push it out of view before
                // the list follows, and the list would then stop following.
                .simultaneousGesture(DragGesture(minimumDistance: 8).onChanged { g in
                    if ChatScroll.letsGo(dragY: g.translation.height) { stick = false }
                })
                .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { _ in
                    // the list got shorter or taller (the keyboard, the bar): keep the newest row in view if it was
                    if stick { proxy.scrollTo(ChatPage.bottomId, anchor: .bottom) }
                }
                .onChange(of: composerHeight) { _, _ in
                    // the composer over the list grew or shrank (more lines, the tray): the newest row stays above it
                    if stick { proxy.scrollTo(ChatPage.bottomId, anchor: .bottom) }
                }
                // where the list is, measured from its own geometry (iOS 18+): the newest row's sentinel alone can stay "shown" in a lazy
                // list after the reader scrolled away, which hid "↓", or flap while rows above it settle their heights
                .modifier(BottomWatch { reachedBottom($0) })
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.immediately)
                .scrollBounceBehavior(.always)
                .simultaneousGesture(TapGesture().onEnded { dismissKeyboard() })
                .onChange(of: followSignal) { _, _ in
                    let lastMine = model.items.last?.isMine == true
                    if ChatScroll.follow(atBottom: stick, lastIsMine: lastMine) {
                        stick = true
                        proxy.scrollTo(ChatPage.bottomId, anchor: .bottom)
                    }
                    let sig = ChatScroll.replySignature(model.items)
                    unread = ChatScroll.unread(unread, atBottom: atBottom, replyGrew: sig != seenSignature)
                    seenSignature = sig
                }
                // "↓": back to the newest row, and hold on to it again. Not animated, and not through `ScrollPosition`'s end edge: an
                // animated scroll to a row of a lazy list keeps re-aiming while the rows above settle their heights, and a list bound
                // to its end edge measures every row again on each frame something in it moves (the status dots) — either held the
                // main thread. A second jump on the next turn of the run loop lands exactly once the rows on the way have their sizes.
                .onChange(of: scrollToBottomRequest) { _, _ in
                    Haptics.tick()
                    stick = true
                    unread = false
                    proxy.scrollTo(ChatPage.bottomId, anchor: .bottom)
                    DispatchQueue.main.async { proxy.scrollTo(ChatPage.bottomId, anchor: .bottom) }
                }
                .onTabReselect(.chat) { scrollToBottomRequest += 1 }
                // a row in 답이 필요해요 asked for a secure entry's card: scroll to it and mark it for a moment
                .task(id: "\(app.router.chatEntryFocus ?? "")-\(model.rows.count)") {
                    guard let id = app.router.chatEntryFocus else { return }
                    if let key = model.rows.first(where: { if case .secret(let sc) = $0 { return sc.request.id == id }; return false })?.key {
                        proxy.scrollTo(key, anchor: .center)
                        app.router.chatEntryFocus = nil
                        withAnimation(KeloMotionSpec.small(reduce)) { markedEntry = id }
                    } else if model.loaded {
                        await model.refreshSecretsNow()
                        if !model.rows.contains(where: { if case .secret(let sc) = $0 { return sc.request.id == id }; return false }) {
                            app.router.chatEntryFocus = nil   // gone meanwhile: the chat stays where it is
                        }
                    }
                }
                // the mark goes after a moment (its own task: a list change must not cancel it)
                .task(id: markedEntry) {
                    guard let id = markedEntry else { return }
                    try? await Task.sleep(nanoseconds: 1_800_000_000)
                    if markedEntry == id { withAnimation(KeloMotionSpec.small(reduce)) { markedEntry = nil } }
                }
                // a follow-up notification asked for one message: scroll to it once it is in the list
                .task(id: "\(app.router.chatFocus ?? 0)-\(model.items.count)") {
                    guard let id = app.router.chatFocus else { return }
                    if let key = model.rowKey(forMessage: id) {
                        // not animated: an animated scroll to a row of this lazy list can keep re-aiming (see "↓" above)
                        proxy.scrollTo(key, anchor: .center)
                        app.router.chatFocus = nil
                    } else if model.loaded {
                        try? await model.reloadNow()
                        if let key = model.rowKey(forMessage: id) { proxy.scrollTo(key, anchor: .center) }
                        app.router.chatFocus = nil
                    }
                }
            }
        }
    }

    static let bottomId = "__bottom__"
    /// The room the list keeps after its last row: 8 between rows, then the 1-pt end marker pulled up 3 (ChatOverlayRules.listGap)
    static let listEnd: CGFloat = 6

    private var pillShown: Bool { !atBottom && !stick && !model.items.isEmpty }

    /// The list reached its end (holds on to the newest row again) or left it. Only a change writes, so nothing redraws for nothing.
    private func reachedBottom(_ at: Bool) {
        // only the "↓" depends on it: its fade is the only thing animated (not the whole page, whose list must not re-lay out)
        if atBottom != at { withAnimation(KeloMotionSpec.small(reduce)) { atBottom = at } }
        if at {
            if !stick { stick = true }
            if unread { unread = false }
        }
    }

    /// Changes whenever the list grows or the newest row changes (rows, phase, streamed text).
    private var followSignal: String {
        "\(model.rows.count)|\(model.paymentSteps.count)|\(model.pinNeeds.count)|\(model.items.last?.key ?? "")|\(model.items.last?.asMsg?.text.count ?? 0)|\(String(describing: model.phase?.kind))|\(model.asks.filter(\.open).map(\.id).joined(separator: ","))"
    }

    @ViewBuilder
    private func row(_ item: ChatItem) -> some View {
        let actions = MessageActions.actions(model.items, key: item.key, busy: model.busy)
        switch item {
        case .msg(let m):
            if m.mine {
                let words = ChatWords.attachmentWords()
                let shown = MessageFileRules.shownText(m.text, files: m.files, autoText: m.autoText, line: words.line)
                if m.files.isEmpty {
                    UserBubble(text: m.text, reaction: m.reaction, actions: actions) { a in act(item, text: m.text, a) }
                } else {
                    VStack(alignment: .trailing, spacing: 6) {
                        HStack { Spacer(minLength: 48); UserFiles(files: m.files) }
                        if !shown.isEmpty {
                            UserBubble(text: shown, reaction: m.reaction, actions: actions) { a in act(item, text: m.text, a) }
                        }
                    }
                }
            } else if let card = m.briefing {
                BriefingCardView(card: card, onTap: briefingTap, onHours: { h, accept in
                    Task { _ = try? await app.api?.request("POST", accept ? ContactHoursRules.acceptPath(h.id) : ContactHoursRules.keepPath(h.id)) }
                })
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    let streaming = model.busy && m.id == nil && model.items.last?.key == m.key
                    if !m.text.isEmpty || m.images.isEmpty {
                        AssistantBubbles(text: m.text, streaming: streaming, actions: streaming ? [] : actions) { text, a in act(item, text: text, a) }
                    }
                    ForEach(m.images, id: \.self) { img in ChatPicture(image: img) }
                    if let id = m.id, let f = model.followups[id] { MessageFollowups(followup: f, model: model) }
                    if let r = m.replies, !model.busy, QuickReplyRules.isOpen(model.items, key: m.key) {
                        QuickReplies(set: r) { text, answers in Haptics.tick(); model.send(text, answers: answers) }
                    }
                }
            }
        case .tool(let t):
            ToolChip(tool: t)
        case .approval(let a):
            ApprovalCard(approval: a, onAnswer: { ok in
                if ok && a.pin != nil { model.pinAllowed(a.id) }   // its pin_needed row then asks for the owner's check at once
                if ok, let card = a.card, card.needsCard {
                    Haptics.tick()
                    model.approveWithCard(a.id, cardId: card.card.id)
                } else {
                    if ok { Haptics.success() } else { Haptics.warning() }
                    model.approve(a.id, ok: ok)
                }
            }, onKeepForSchedule: { Haptics.success(); model.approve(a.id, ok: true, rememberSchedule: true) })
        case .browser(let b):
            // "보기" opens the tab this card's pictures came from (a background job's own tab); an older server names none
            BrowserCard(browser: b) { if b.tab.isEmpty { app.router.open(.ownTab) } else { app.router.openLive(b.tab) } }
        case .cardNeeded(let n):
            CardNeededCard(item: n) { Haptics.tick(); model.sendCard(n.approvalId, cardId: n.card.id) }
        case .paymentAuto(let p):
            PaymentAutoRow(item: p)
        case .vault(let v):
            VaultCardRow(item: v, model: model)
        case .connect(let cn):
            ConnectCardRow(item: cn, model: model)
        case .shipping(let sh):
            ShippingFormCard(item: sh, model: model)
        case .secret(let sc):
            // a key, a login or a card the agent asked for: the value goes straight to the vault or the wallet, never the chat
            SecretEntryCard(request: sc.request, returnTo: .chat,
                            onSaved: { answer, result in
                                app.activity.status = WorkBadge.entryClosed(app.activity.status, kind: sc.request.kind)
                                model.secretSaved(sc.request, answer: answer, result: result)
                            },
                            onCancel: {
                                Haptics.tick()
                                app.activity.status = WorkBadge.entryClosed(app.activity.status, kind: sc.request.kind)
                                model.secretCancelled(sc.request)
                            },
                            onGone: { model.secretGone(sc.request) })
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.accent.opacity(markedEntry == sc.request.id ? 1 : 0), lineWidth: 2))
        case .locationAsk(let l):
            LocationAskCard(item: l, model: model)
        case .notice(_, let n):
            NoticeChip(text: ChatWords.notice(n))
        case .error(_, let e):
            let text = ChatWords.error(e)
            ErrorBubble(text: text, actions: actions) { a in act(item, text: text, a) }
        }
    }

    /// Where a briefing item opens.
    private func briefingTap(_ t: BriefingTap) {
        Haptics.tick()
        switch t {
        case .chatAt(let id): app.router.chatFocus = id
        case .waiting: app.router.open(.work(.activity))
        case .task(let id): app.router.open(.work(.task(id)))
        case .goals: app.router.go(.goals)
        case .event(let at):
            // the phone's calendar at that moment (seconds since 2001, as the Calendar app's own link counts them)
            if let u = URL(string: "calshow:\(Int(at - 978_307_200))") { openURL(u) }
        case .mail(let subject): model.setInput(L(R.string.briefing_mail_ask, subject)); composerFocused = true
        case .ask(let text): model.setInput(text); composerFocused = true
        }
    }

    private func act(_ item: ChatItem, text: String, _ a: MsgAction) {
        switch a {
        case .copy: Clipboard.copy(text)
        case .share: break   // ShareLink handles it in the menu
        case .select: composerFocused = false; sheet = .select(text)
        case .retry: model.retry(item.key)
        case .edit: model.setInput(text); composerFocused = true
        case .report: composerFocused = false; sheet = .report(item.key)
        }
    }
}

/// Reports whether the list shows its end (within 64 pt), from the scroll view's own geometry (iOS 18+; older systems use the
/// newest row's sentinel).
private struct BottomWatch: ViewModifier {
    let onChange: (Bool) -> Void

    func body(content: Content) -> some View {
        if #available(iOS 18.0, macOS 15.0, *) {
            content.onScrollGeometryChange(for: Bool.self, of: { g in
                // the visible rect spans the whole scroll view (its height is the container plus both insets); the part above the
                // bottom inset (the composer and its scrim, the tab bar) is what the reader sees. Scrolling to the newest row leaves
                // the list's own bottom padding and the scrim's fade just below that edge (about 55 pt): within 64 pt is the end.
                g.visibleRect.maxY - g.contentInsets.bottom >= g.contentSize.height - 64
            }, action: { _, at in onChange(at) })
        } else {
            content
        }
    }
}

/// What the chat does when the status poll changes: the waiting approvals changed (ask for the list), a payment step may be open
/// (while the agent works, or while one is open here), or a secure entry was asked for or answered elsewhere.
private struct ChatStatusFollow: ViewModifier {
    let model: ChatModel
    let status: AgentStatus?

    func body(content: Content) -> some View {
        content
            .onChange(of: ChatAsks.pollKey(status)) { old, new in
                if old != nil || new != nil { model.refreshAsks() }
            }
            .onChange(of: status) { _, s in
                if s?.busy == true || model.paymentSteps.contains(where: \.open) { model.refreshPaymentSteps() }
            }
            .onChange(of: status?.secretRequests) { old, new in
                if let new, SecretEntries.pollWants(before: old, now: new) { model.refreshSecrets() }
            }
            // a briefing card changed elsewhere: asked for again (the first poll too: a kept card may have changed while closed)
            .onChange(of: status?.briefingRev, initial: true) { _, new in
                if new != nil { model.refreshBriefings() }
            }
    }
}

/// A row made in this session rises a little and grows from about 92% as it appears (a soft spring).
private struct RiseIn: ViewModifier {
    let fromEnd: Bool
    @State private var shown: Bool

    init(active: Bool, fromEnd: Bool) {
        self.fromEnd = fromEnd
        _shown = State(initialValue: !active)
    }

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .scaleEffect(shown ? 1 : 0.92, anchor: fromEnd ? .bottomTrailing : .bottomLeading)
            .offset(y: shown ? 0 : 18)
            .onAppear { if !shown { withAnimation(.spring(response: 0.35, dampingFraction: 0.9)) { shown = true } } }
    }
}

/// The "↓" on the composer's top border that takes the list back to the newest message; the dot: the agent wrote more meanwhile.
private struct JumpPill: View {
    let unread: Bool
    let action: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button(action: action) {
            KeloIconView(.arrowDown, size: 20, tint: c.ink)
                .frame(width: ChatOverlayRules.jumpSize, height: ChatOverlayRules.jumpSize)
                .background(Circle().fill(c.card).shadow(color: c.scrim.opacity(0.18), radius: 6, y: 2))
                .overlay(Circle().stroke(c.line, lineWidth: 1))
                .overlay(alignment: .topTrailing) {
                    if unread { Circle().fill(c.accent).frame(width: 9, height: 9).overlay(Circle().stroke(c.card, lineWidth: 2)) }
                }
        }
        .buttonStyle(PressShrinkStyle())
        .accessibilityLabel(L(unread ? R.string.chat_jump_latest_new : R.string.chat_jump_latest))
        .accessibilityIdentifier("kelo.jump")
    }
}

/// The line under the messages while a reply is in progress: three dots, what is going on, and the seconds.
struct StatusLine: View {
    let phase: Phase
    @Environment(\.kelo) private var c

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { tl in
            let seconds = max(0, Int(tl.date.timeIntervalSince1970 - phase.since))
            HStack(spacing: 8) {
                Dots()
                Text(ChatWords.phase(phase, now: tl.date.timeIntervalSince1970)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2)
                Text(L(R.string.chat_seconds, seconds)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize()
            }
            .padding(.horizontal, 4).padding(.vertical, 4)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct Dots: View {
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce
    @State private var lit = false

    // A repeating fade of each dot (drawn only, no layout): a per-frame TimelineView here made the lazy list around it place
    // all its rows again on every frame, which held the main thread in a long chat.
    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<3, id: \.self) { i in
                Circle().fill(c.muted).frame(width: 6, height: 6)
                    .opacity(reduce ? 0.6 : (lit ? 1 : 0.25))
                    .animation(reduce ? nil : .easeInOut(duration: 0.6).repeatForever(autoreverses: true).delay(Double(i) * 0.15),
                               value: lit)
            }
        }
        .onAppear { lit = true }
        .onDisappear { lit = false }
        .accessibilityHidden(true)
    }
}

/// The scrim the composer floats on (Android chat/ComposerScrim.kt): transparent `fade` above the pill, the screen's background
/// from a little below the pill's top edge down to the bottom, so messages scrolling under the composer fade out.
struct ComposerScrim: View {
    /// What the list's end and the composer's top room leave of ChatOverlayRules.listGap (here: none)
    static let fade: CGFloat = ChatOverlayRules.scrimFade(composerTop: Composer.topRoom, listEnd: ChatPage.listEnd)
    let bg: Color

    var body: some View {
        GeometryReader { g in
            let solid = min(1, (Self.fade + 16) / max(g.size.height, 1))
            LinearGradient(stops: [.init(color: bg.opacity(0), location: 0), .init(color: bg, location: solid), .init(color: bg, location: 1)],
                           startPoint: .top, endPoint: .bottom)
        }
        .allowsHitTesting(false)
    }
}

// ---- the composer: one rounded box with the text on top and, under it, "+", the model chip, the mic and 보내기

struct Composer: View {
    /// The composer's own room above its card, at its sides, and its widest card (with the side room)
    static let topRoom: CGFloat = 6
    static let sideRoom: CGFloat = 12
    static let maxWidth: CGFloat = 760
    /// From the card's trailing edge to the "↓" button's: centres it over 보내기 (40-pt button, 6 pt in from the card's edge)
    static let jumpInset: CGFloat = 6 + 20 - ChatOverlayRules.jumpSize / 2
    /// The text's own room above its first line; a little more is added so the "↓" never covers the text (ChatOverlayRules)
    static let fieldTop: CGFloat = 12
    @Bindable var model: ChatModel
    var focused: Binding<Bool>
    let onAttach: () -> Void
    let onChip: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(spacing: 0) {
            ComposerField(text: Binding(get: { model.input }, set: { model.setInput($0) }), focused: focused,
                          placeholder: L(R.string.chat_input_placeholder),
                          onImages: { pictures in
                              // a picture pasted from the clipboard: the same way as one picked with "+"
                              for data in pictures.prefix(10) {
                                  model.attach(image: true, label: L(R.string.attach_photo)) { try PhotoPrep.prepare(data, display: nil) }
                              }
                          })
                .padding(.horizontal, 16).padding(.top, Self.fieldTop + ChatOverlayRules.fieldTopExtra(Self.fieldTop)).padding(.bottom, 4)
                .frame(minHeight: 44)
                .accessibilityIdentifier("kelo.composer")
            HStack(spacing: 2) {
                Button(action: onAttach) {
                    KeloIconView(.plus, size: 24, tint: c.muted).frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L(R.string.chat_attach))
                Spacer(minLength: 4)
                ModelChip(text: ChatWords.chip(app.profile, providers: model.providers, list: model.modelList), action: onChip)
                MicButton(text: Binding(get: { model.input }, set: { model.setInput($0) }))
                Button(action: { model.busy ? model.stop() : model.send(words: ChatWords.attachmentWords()) }) {
                    KeloIconView(model.busy ? .stop : .send, size: 20, tint: model.canSend ? c.bg : c.disabledInk)
                        .frame(width: 40, height: 40)
                        .background(Circle().fill(model.canSend ? c.ink : c.disabledFill))
                }
                .buttonStyle(PressShrinkStyle())
                .disabled(!model.canSend)
                .accessibilityLabel(L(model.busy ? R.string.chat_stop : R.string.chat_send))
                .accessibilityIdentifier("kelo.send")
            }
            .padding(.leading, 4).padding(.trailing, 6).padding(.bottom, 6)
        }
        .background(RoundedRectangle(cornerRadius: 24).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 24).stroke(focused.wrappedValue ? c.accent : c.line, lineWidth: 1))
        .padding(.horizontal, Self.sideRoom).padding(.top, Self.topRoom).padding(.bottom, 10)
        .frame(maxWidth: Self.maxWidth).frame(maxWidth: .infinity)
    }
}

/// The model chip inside the composer: "<model> <thinking level>"; opens the model chooser.
private struct ModelChip: View {
    let text: String
    let action: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button(action: action) {
            HStack(spacing: 2) {
                Text(text).font(.system(.footnote, weight: .medium)).foregroundStyle(c.ink).lineLimit(1).truncationMode(.middle)
                KeloIconView(.chevronRight, size: 14, tint: c.muted).rotationEffect(.degrees(90))
            }
            .padding(.leading, 10).padding(.trailing, 6)
            .frame(minHeight: 36)
            .frame(maxWidth: 220)
            .contentShape(Capsule())
        }
        .buttonStyle(PressShrinkStyle())
        .accessibilityLabel("\(L(R.string.chat_model_settings)): \(text)")
        .accessibilityIdentifier("kelo.modelChip")
    }
}
