import SwiftUI
import KeloCore
import KeloShared

/// A tool waiting for a yes or no (Android ApprovalCard). Once answered (or expired) the card stays as asked, without its
/// buttons, and a chip says how it ended. The raw arguments are never shown, only readable pairs or the server's sentence.
struct ApprovalCard: View {
    let approval: ChatItem.Approval
    let onAnswer: (Bool, Bool, Bool) -> Void
    let onKeepForSchedule: () -> Void
    @Environment(\.kelo) private var c
    @State private var rememberSite = false

    var body: some View {
        CardBox {
            // the setup card says it itself, at its end
            if approval.urgent && approval.open && approval.grant?.urgent != true { UrgentLine(missed: approval.missed) }
            if let payment = approval.payment {
                PurchaseApproval(approval: approval, payment: payment) { ok in onAnswer(ok, false, false) }
            } else if let grant = approval.grant {
                // the setup card of a "when X happens, do Y" task: its own layout (trigger, action, limits, the card's auto-pay limit)
                TaskGrantApproval(grant: grant, open: approval.open, trailing: {
                    if let o = Outcomes.of(approval) { OutcomeChip(outcome: o) }
                }, onAnswer: { ok in onAnswer(ok, false, false) })
            } else {
                HStack(alignment: .top, spacing: 8) {
                    Text(TurnEvent.ApprovalRequest.question(approval.ask) ?? L(R.string.chat_approval_ask,
                           AskWords.askWord(approval.shown, language: appLanguageCode()),
                           AskWords.objectParticle(approval.shown)))
                        .font(KeloFont.titleMedium).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let o = Outcomes.of(approval) { OutcomeChip(outcome: o) }
                }
                if let pre = approval.preapproval { PreapprovalFacts(pre) } else if let e = approval.editable {
                    // the message is shown in its own block, with Edit under it (the detail line is then not the message)
                    ApprovalFacts(lines: ApprovalEditRules.withoutMessage(approval.lines, e), detail: nil)
                    EditableMessageBlock(approvalId: approval.id, editable: e, enabled: approval.open)
                } else { ApprovalFacts(lines: approval.lines, detail: approval.detail) }
                if let mail = approval.mail { MailPreviewBlock(approvalId: approval.id, design: mail) }
                if !approval.forgetPlan.isEmpty { ForgetPlanList(planId: approval.forgetPlan) }
                if !approval.items.isEmpty { ApprovalItems(items: approval.items) }
                if approval.open {
                    if approval.canRemember && !approval.site.isEmpty {
                        RememberSiteCheck(site: approval.site, checked: rememberSite, enabled: true) { rememberSite = $0 }
                    }
                    if RecipientRules.canAlways(approval.offers) {
                        RecipientChoices(offers: approval.offers) { answer in onAnswer(answer.approve, false, answer == .always) }
                    } else {
                        HStack(spacing: 10) {
                            SecondaryButton(L(R.string.chat_approval_deny), fill: true) { onAnswer(false, false, false) }
                            PrimaryButton(L(R.string.chat_approval_allow)) { onAnswer(true, rememberSite, false) }
                        }
                    }
                    if approval.canRememberSchedule { LinkButton(L(R.string.approval_keep_for_schedule), action: onKeepForSchedule) }
                }
            }
        }
    }
}

/// An approval's readable pairs as a small two-column list, or its one sentence when there are none.
struct ApprovalFacts: View {
    let lines: [ApprovalLine]
    let detail: String?
    @Environment(\.kelo) private var c

    var body: some View {
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, l in
                    // the whole thing, as it will be sent (e.g. a text message): the label above, every word below
                    if l.block {
                        VStack(alignment: .leading, spacing: 2) {
                            if !l.label.isEmpty { Text(l.label).font(KeloFont.small).foregroundStyle(c.muted) }
                            CappedScrollText(l.value)
                        }
                    } else {
                        ViewThatFits(in: .horizontal) {
                            HStack(alignment: .firstTextBaseline, spacing: 12) {
                                if !l.label.isEmpty { Text(l.label).font(KeloFont.small).foregroundStyle(c.muted).frame(minWidth: 56, alignment: .leading) }
                                Text(TokenText.fit(l.value, max: 28)).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            VStack(alignment: .leading, spacing: 2) {
                                if !l.label.isEmpty { Text(l.label).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
                                Text(TokenText.fit(l.value, max: 28)).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2)
                            }
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
        } else if let detail, !detail.trimmingCharacters(in: .whitespaces).isEmpty {
            Text(detail).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// How an approval ended, as a small chip in the card's corner.
struct OutcomeChip: View {
    let outcome: Outcome
    var cardSent = false
    @Environment(\.kelo) private var c

    var body: some View {
        let (word, tone): (String, Color) = switch outcome {
        case .allowed: (L(cardSent ? R.string.chat_outcome_card_sent : R.string.chat_outcome_allowed), c.success)
        case .denied: (L(R.string.chat_outcome_denied), c.danger)
        case .expired: (L(R.string.chat_outcome_expired), c.muted)
        }
        Text(word).font(.system(.caption2, weight: .semibold)).foregroundStyle(tone)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(RoundedRectangle(cornerRadius: 6).fill(tone.opacity(0.12)))
            .fixedSize()
    }
}

/// The approval card of confirm_purchase, the one final card of an order: where, what (items, options, how many), where it goes,
/// which card, the coupon, the total, and Allow / Deny. The page picture (the browser card) stays above it as before.
struct PurchaseApproval: View {
    let approval: ChatItem.Approval
    let payment: PaymentInfo
    let onAnswer: (Bool) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let working = approval.send == .working
        HStack(spacing: 8) {
            Text(L(R.string.chat_purchase_question)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            if let o = Outcomes.of(approval) { OutcomeChip(outcome: o, cardSent: approval.needsCard && approval.send == .sent) }
        }
        // everything at once: the site, the items (or the summary), where it goes, the card, the coupon and the total
        OrderDetails(payment: payment, card: approval.card)
        if let card = approval.card {
            // why it asks (any card, also one saved to ask each time), as the server words it
            if !card.reasonText.isEmpty { MutedText(L(R.string.chat_purchase_not_auto, card.reasonText), small: true) }
            if card.needsCard { MutedText(L(R.string.chat_purchase_needs_card), small: true) }
        }
        if !approval.open {
            if case .failed(let e) = approval.send { ErrorText(ChatWords.cardSendError(e), small: true) }
        } else {
            CardSendLine(send: approval.send, canDeny: true)
            HStack(spacing: 10) {
                SecondaryButton(L(R.string.chat_approval_deny), enabled: !working, fill: true) { onAnswer(false) }
                PrimaryButton(L(working ? R.string.chat_card_sending : R.string.chat_approval_allow), enabled: !working) { onAnswer(true) }
            }
        }
    }
}

/// How sending the card went, when there is something to say.
struct CardSendLine: View {
    let send: CardSend
    let canDeny: Bool

    var body: some View {
        switch send {
        case .working: MutedText(L(R.string.chat_card_sending_hint), small: true)
        case .failed(let e):
            ErrorText(e == .cancelled && !canDeny ? L(R.string.chat_card_error_cancelled_retry) : ChatWords.cardSendError(e), small: true)
        default: EmptyView()
        }
    }
}

/// An allowed payment waits for this phone's card: a countdown and "확인하고 카드 보내기".
struct CardNeededCard: View {
    let item: ChatItem.CardNeeded
    let onSend: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { tl in
            let left = max(0, item.expiresAt - Int64(tl.date.timeIntervalSince1970))
            CardBox {
                if item.send == .sent {
                    ResultLine(icon: .check, text: L(R.string.chat_card_sent, item.amountText))
                } else if item.ended || left <= 0 {
                    ResultLine(icon: .clock, text: L(R.string.chat_card_needed_over, item.amountText), muted: true)
                } else {
                    Text(L(R.string.chat_card_needed_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(L(R.string.chat_card_needed_body, item.card.label, item.site, item.amountText)).font(KeloFont.body)
                        .foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    MutedText(L(R.string.chat_card_needed_left, CardCountdown.text(left)), small: true)
                    CardSendLine(send: item.send, canDeny: false)
                    PrimaryButton(L(item.send == .working ? R.string.chat_card_sending : R.string.chat_card_needed_send),
                                  enabled: item.send != .working, action: onSend)
                }
            }
        }
    }
}

/// "자동결제했어요 · site · 24,000원 · Visa ••4242": a payment the auto-pay rules let through.
struct PaymentAutoRow: View {
    let item: ChatItem.PaymentAuto
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            KeloIconView(.card, size: 18, tint: c.success)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.cardLabel.isEmpty ? L(R.string.chat_payment_auto_no_card, item.site, item.amountText)
                     : L(R.string.chat_payment_auto, item.site, item.amountText, item.cardLabel))
                    .font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                // covered in advance (an order approved earlier, or the schedule's allow)
                if let cover = item.cover {
                    Text(L(cover.bySchedule ? R.string.chat_payment_cover_schedule : R.string.chat_payment_cover_order))
                        .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                // where the order goes, when the server said
                if let ship = item.order?.shipping {
                    let place = [ship.label, AddressRules.oneLine(ship.formatted)].filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                        .joined(separator: " · ")
                    if !place.isEmpty {
                        Text(L(R.string.shipping_ask_done, place)).font(KeloFont.small).foregroundStyle(c.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
    }
}

/// The agent's browser tab as one compact row: a tile with the site's letter, the site and what the agent is doing, and 보기 (the
/// tab live). Under it the last few pictures as a filmstrip (tap one for full screen).
struct BrowserCard: View {
    let browser: ChatItem.Browser
    let onOpenLive: () -> Void
    @Environment(\.kelo) private var c
    @State private var full: String?

    var body: some View {
        // the phone card is this card for the Android device: a phone icon, "Phone", portrait pictures
        let site = browser.device ? L(R.string.chat_phone_title)
            : (browser.host.isEmpty ? (browser.title.isEmpty ? L(R.string.chat_browser_title) : browser.title) : browser.host)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                let hsl = ConnectorList.tileHSL(hue: ConnectorList.hue(browser.device ? browser.tab : (browser.host.isEmpty ? browser.id : browser.host)), dark: c.dark)
                Group {
                    if browser.device { KeloIconView(.phone, size: 18, tint: KeloColors.tileInk) }
                    else {
                        Text(SiteTile.letter(host: browser.host, title: browser.title))
                            .font(.system(size: 15, weight: .bold)).foregroundStyle(KeloColors.tileInk)
                    }
                }
                    .frame(width: 32, height: 32)
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color(hue: hsl.0 / 360, saturation: hsl.1, brightness: hslBrightness(hsl))))
                VStack(alignment: .leading, spacing: 1) {
                    Text(TokenText.fit(site)).font(KeloFont.button).foregroundStyle(c.ink).lineLimit(1)
                    // the newest step; once the tab went through several sites in this reply, how many
                    let step = browser.status.isEmpty ? browser.title : browser.status
                    Text(!browser.device && browser.hosts.count > 1 ? L(R.string.chat_browser_step_sites, step, browser.hosts.count) : step)
                        .font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button(L(R.string.chat_browser_view_short), action: onOpenLive)
                    .font(KeloFont.button).foregroundStyle(c.accent).buttonStyle(.plain)
            }
            let strip = browser.recent.isEmpty ? [browser.image].compactMap { $0 } : browser.recent
            if !strip.isEmpty {
                HStack(spacing: 6) {
                    ForEach(strip, id: \.self) { url in
                        Button { full = url } label: {
                            DataImage(dataUrl: url)
                                .frame(width: browser.device ? 72 * PhoneShape.aspect(width: browser.width, height: browser.height) : 72,
                                       height: browser.device ? 72 : 48)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                                .overlay(RoundedRectangle(cornerRadius: 6).stroke(c.line, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(L(R.string.chat_browser_view))
                    }
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        .padding(.trailing, 32)
        .keloFullScreen(isPresented: Binding(get: { full != nil }, set: { if !$0 { full = nil } })) {
            ImageViewer(dataUrl: full ?? "", title: browser.device ? site : (browser.title.isEmpty ? browser.host : browser.title)) { full = nil }
        }
    }

    /// HSL lightness to HSB brightness for the tile colour.
    private func hslBrightness(_ hsl: (Double, Double, Double)) -> Double {
        let l = hsl.2, s = hsl.1
        return l + s * min(l, 1 - l)
    }
}

/// A picture from a data: URL, decoded once (nothing while unreadable).
struct DataImage: View {
    let dataUrl: String
    @Environment(\.kelo) private var c

    var body: some View {
        if let data = BrowserImage.bytes(dataUrl), let img = PlatformImage(data: data) {
            Image(platformImage: img).resizable().scaledToFill().frame(maxHeight: .infinity, alignment: .top)
        } else {
            Rectangle().fill(c.bubble)
        }
    }
}

/// A picture on the whole screen; pinch to zoom, × to close.
private struct ImageViewer: View {
    let dataUrl: String
    let title: String
    let onClose: () -> Void
    @Environment(\.kelo) private var c
    @State private var scale: CGFloat = 1
    @State private var base: CGFloat = 1

    var body: some View {
        ZStack(alignment: .top) {
            c.media.ignoresSafeArea()
            if let data = BrowserImage.bytes(dataUrl), let img = PlatformImage(data: data) {
                Image(platformImage: img).resizable().scaledToFit()
                    .scaleEffect(scale)
                    .gesture(MagnificationGesture().onChanged { scale = min(max(base * $0, 1), 5) }.onEnded { _ in base = scale })
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityLabel(title)
            }
            HStack {
                Text(title).font(KeloFont.titleSmall).foregroundStyle(c.onMedia).lineLimit(1)
                Spacer()
                Button(action: onClose) { KeloIconView(.close, size: 24, tint: c.onMedia).frame(width: 44, height: 44) }
                    .buttonStyle(.plain).accessibilityLabel(L(R.string.close))
            }
            .padding(8)
        }
    }
}

/// The secure form for a login or a key; it folds into one line once the agent was told, or when a newer request replaced it.
struct VaultCardRow: View {
    let item: ChatItem.Vault
    let model: ChatModel

    var body: some View {
        switch item.state {
        case .open:
            SecureCard(name: item.name, onAdded: { model.entryToast = $0 }, onNotify: { text in
                model.settleCard(item.key, .done)
                model.send(text)
            })
        case .done: CardBox { ResultLine(icon: .shield, text: L(R.string.chat_vault_done)) }
        case .cancelled: CardBox { ResultLine(icon: .close, text: L(R.string.chat_card_cancelled), muted: true) }
        case .expired, .closed: CardBox { ResultLine(icon: .clock, text: L(R.string.chat_card_expired), muted: true) }
        }
    }
}

/// The agent asked the user to connect a service (connect_service). The connect panels live in the Connectors tab: the card opens
/// it at that connector, and "에이전트에게 알리기" tells the agent once it is connected.
struct ConnectCardRow: View {
    let item: ChatItem.Connect
    let model: ChatModel
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var watcher: ConnectCardModel?
    @State private var recheck = 0
    private var connector: Connector? { watcher?.connector }
    @Environment(\.keloConnectPanel) private var panel

    var body: some View {
        CardBox {
            switch item.state {
            case .open:
                let name = connector?.name.isEmpty == false ? connector!.name : item.service
                HStack(spacing: 8) {
                    if let icon = panel?.icon { icon(item.service, 28) } else { KeloIconView(.plug, size: 20, tint: c.ink) }
                    Text(L(R.string.ios_chat_connect_title, name)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let panel {
                    // the connector's own connect panel, right here in the chat (Android ConnectCard)
                    panel.make(item.service) { recheck += 1 }
                    if connector?.status == "connected" {
                        PrimaryButton(L(R.string.notify_agent)) {
                            model.settleCard(item.key, .done)
                            model.send(connector?.connect == "mail" ? L(R.string.mail_notify) : L(R.string.chat_connect_notify, name))
                        }
                    }
                } else if connector?.status == "connected" {
                    MutedText(L(R.string.connector_connected_name, name), small: true)
                    PrimaryButton(L(R.string.notify_agent)) {
                        model.settleCard(item.key, .done)
                        model.send(L(R.string.chat_connect_notify, name))
                    }
                } else {
                    MutedText(L(R.string.ios_chat_connect_body), small: true)
                    PrimaryButton(L(R.string.ios_chat_connect_open)) { app.router.openConnector(item.service) }
                    SecondaryButton(L(R.string.notify_agent), fill: true) {
                        model.settleCard(item.key, .done)
                        model.send(L(R.string.chat_connect_notify, name))
                    }
                }
                LinkButton(L(R.string.cancel)) { model.settleCard(item.key, .cancelled) }
            case .done: ResultLine(icon: .check, text: L(R.string.chat_connect_done))
            case .cancelled: ResultLine(icon: .close, text: L(R.string.chat_card_cancelled), muted: true)
            case .expired, .closed: ResultLine(icon: .clock, text: L(R.string.chat_card_expired), muted: true)
            }
        }
        .task(id: "\(item.state == .open)#\(recheck)") {
            // the connector's name and whether it is connected yet (again when the user comes back from Connectors)
            guard item.state == .open, let api = app.api else { return }
            let w = watcher ?? ConnectCardModel(api: api, service: item.service)
            watcher = w
            await w.watch()
        }
    }
}

/// "Saved in the Keychain": the key's name, where it is used, and the way to the Keychain (behind its own lock).
struct KeySavedCard: View {
    let item: ChatItem.KeySaved
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c

    var body: some View {
        CardBox {
            HStack(spacing: 8) {
                KeloIconView(.key, size: 20, tint: c.accent)
                Text(L(R.string.keysaved_title))
                    .font(KeloFont.titleMedium)
                    .foregroundStyle(c.ink)
            }
            Text(item.name)
                .font(KeloFont.body)
                .foregroundStyle(c.ink)
            if !item.hosts.isEmpty {
                MutedText(L(R.string.keysaved_hosts, item.hosts.joined(separator: ", ")), small: true)
            }
            SecondaryButton(L(R.string.keysaved_open), fill: true) {
                app.router.openMyPage(.vault)
            }
        }
        .accessibilityIdentifier("kelo.keySaved")
    }
}
