import SwiftUI
import KeloCore
import KeloShared

/// One payment step that needs the user, at the bottom of the chat (Android chat/PaymentStepUi.kt PaymentStepCard): what is paid, then
/// what to do. An app approval shows the code large (tap to copy), the QR code from the page, the time left counting down and
/// [승인했어요] / [취소]; when the time runs out it asks whether to get a new code. A one-time code has a field for it. The PIN keypad
/// kind shows the page's keypad region live; the owner's taps on it go to the same spots of the page, with a dot per tap and the
/// page's own clear key. Ended steps fold into one line.
struct PaymentStepCard: View {
    let step: PaymentStep
    let model: ChatModel
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c

    var body: some View {
        CardBox {
            if !step.open && step.state != "finishing" {
                EndedLine(step: step)
            } else {
                HStack(alignment: .top, spacing: 8) {
                    KeloIconView(step.isPad ? .lock : .phone, size: 20, tint: c.accent).padding(.top, 2)
                    Text(L(title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityAddTraits(.isHeader)
                }
                orderBox
                if !step.instruction.isBlank {
                    Text(step.instruction).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                if step.state == "finishing" {
                    HStack(spacing: 8) {
                        KeloIconView(.hourglass, size: 18, tint: c.accent)
                        Text(L(R.string.pay_step_finishing)).font(KeloFont.body).foregroundStyle(c.ink)
                    }
                    .accessibilityElement(children: .combine)
                } else if step.isPad {
                    PinPadPart(step: step, model: model, openLive: openLive)
                } else {
                    TimelineView(.periodic(from: .now, by: 1)) { tl in
                        ApprovePart(step: step, now: Int64(tl.date.timeIntervalSince1970), model: model, openLive: openLive)
                    }
                }
            }
        }
    }

    private var title: StringKey {
        switch step.kind {
        case PaymentStep.kindPinPad: R.string.pay_step_title_pin
        case PaymentStep.kindOtp: R.string.pay_step_title_otp
        case PaymentStep.kindPasskey: R.string.pay_step_title_passkey
        default: R.string.pay_step_title_app
        }
    }

    private func openLive() {
        Haptics.tick()
        app.router.openLive(step.tab.isEmpty ? "owner" : step.tab, takeControl: true)
    }

    private var orderBox: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !step.site.isEmpty {
                HStack(spacing: 6) {
                    KeloIconView(.globe, size: 14, tint: c.muted)
                    Text(step.site).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1)
                }
            }
            let what = step.items.isEmpty ? step.summary : step.items.joined(separator: ", ")
            if !what.isEmpty { Text(what).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true) }
            HStack(alignment: .lastTextBaseline, spacing: 8) {
                Text(step.payWith).font(KeloFont.small).foregroundStyle(c.muted).frame(maxWidth: .infinity, alignment: .leading)
                if !step.amountText.isEmpty {
                    Text(step.amountText).font(.system(size: 18, weight: .bold)).foregroundStyle(c.ink).multilineTextAlignment(.trailing)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
        .accessibilityElement(children: .combine)
    }
}

/// Approve in another app, a one-time code or a passkey: the code, the QR picture, the countdown and the buttons.
private struct ApprovePart: View {
    let step: PaymentStep
    let now: Int64
    let model: ChatModel
    let openLive: () -> Void
    @Environment(\.kelo) private var c
    @State private var copied = false
    @State private var otp = ""

    var body: some View {
        let expired = PaymentStepRules.expired(step, nowS: now)
        let code = PaymentStepRules.codeText(step)
        VStack(alignment: .leading, spacing: 10) {
            if !code.isEmpty {
                VStack(spacing: 2) {
                    Button {
                        Clipboard.copy(step.code ?? "")
                        copied = true
                        Haptics.tick()
                    } label: {
                        Text(code).font(.system(size: 34, weight: .bold, design: .monospaced)).foregroundStyle(expired ? c.muted : c.ink)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 12).padding(.vertical, 4)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(code.map(String.init).joined(separator: " "))
                    .accessibilityHint(L(R.string.pay_step_copy))
                    MutedText(L(copied ? R.string.pay_step_copied : R.string.pay_step_tap_copy), small: true)
                }
                .frame(maxWidth: .infinity)
            }
            if let qr = step.qr, let data = BrowserImage.bytes(qr), let img = PlatformImage(data: data) {
                Image(platformImage: img).resizable().interpolation(.none).scaledToFit()
                    .frame(width: 136, height: 136).padding(6)
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.white))
                    .frame(maxWidth: .infinity)
                    .accessibilityLabel(L(R.string.pay_step_qr))
            }
            let left = PaymentStepRules.secondsLeft(step, nowS: now)
            if expired {
                Text(L(R.string.pay_step_expired)).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.danger)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    SecondaryButton(L(R.string.pay_step_cancel), fill: true) { Haptics.tick(); model.answerPaymentStep(step.stepId, "cancel") }
                    PrimaryButton(L(R.string.pay_step_new_code)) { Haptics.tick(); model.answerPaymentStep(step.stepId, "new_code") }
                }
            } else if step.state == "renewing" {
                MutedText(L(R.string.pay_step_renewing))
            } else if step.state == "checking" {
                MutedText(L(R.string.pay_step_checking))
            } else {
                if let left {
                    Text(L(R.string.pay_step_left, PaymentStepRules.clock(left))).font(KeloFont.small)
                        .foregroundStyle(left <= 30 ? c.danger : c.muted)
                    if let p = PaymentStepRules.progress(step, nowS: now) {
                        ProgressView(value: p).tint(left <= 30 ? c.danger : c.accent)
                    }
                } else {
                    MutedText(L(R.string.pay_step_left_unknown), small: true)
                }
                if step.needsCode {
                    KeloTextField(L(R.string.pay_step_code_hint), text: Binding(get: { otp }, set: {
                        otp = String($0.filter { $0.isLetter || $0.isNumber }.prefix(12))
                    }), keyboard: .number, maxLength: 12)
                    .accessibilityIdentifier("kelo.paystep.code")
                    PrimaryButton(L(R.string.pay_step_send_code), enabled: otp.count >= 4) {
                        Haptics.tick()
                        model.answerPaymentStep(step.stepId, "code", code: otp)
                        otp = ""
                    }
                }
                HStack(spacing: 10) {
                    SecondaryButton(L(R.string.pay_step_cancel), fill: true) { Haptics.tick(); model.answerPaymentStep(step.stepId, "cancel") }
                    if !step.needsCode {
                        PrimaryButton(L(R.string.pay_step_approved)) { Haptics.success(); model.answerPaymentStep(step.stepId, "approved") }
                            .accessibilityIdentifier("kelo.paystep.approved")
                    }
                }
            }
            LinkButton(L(R.string.pay_step_view), action: openLive)
        }
    }
}

/// The page's keypad region, live: asked for again while shown; a tap goes to the same spot of the page.
private struct PinPadPart: View {
    let step: PaymentStep
    let model: ChatModel
    let openLive: () -> Void
    @Environment(\.kelo) private var c
    @State private var image: PlatformImage?
    @State private var taps: Int?
    @State private var tick = 0
    @State private var problem = false

    var body: some View {
        let shownTaps = taps ?? step.taps
        let ratio = image.map { $0.size.width / max($0.size.height, 1) } ?? 1.3
        VStack(alignment: .leading, spacing: 10) {
            GeometryReader { g in
                ZStack {
                    RoundedRectangle(cornerRadius: 10).fill(c.bubble)
                    if let image {
                        Image(platformImage: image).resizable().frame(width: g.size.width, height: g.size.height)
                    } else {
                        MutedText(L(R.string.pay_step_pad_loading), small: true)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(c.line, lineWidth: 1))
                .contentShape(Rectangle())
                .onTapGesture(coordinateSpace: .local) { p in
                    guard let f = PaymentStepRules.tapFraction(x: p.x, y: p.y, w: g.size.width, h: g.size.height) else { return }
                    Haptics.tick()
                    Task {
                        if let n = await model.paymentPadTap(step.stepId, x: f.0, y: f.1) { taps = n }
                        tick += 1
                    }
                }
            }
            .aspectRatio(ratio, contentMode: .fit)
            .frame(maxWidth: 420)
            .accessibilityLabel(L(R.string.pay_step_pad))
            .accessibilityIdentifier("kelo.paystep.pad")
            HStack(spacing: 10) {
                let dots = PaymentStepRules.dots(shownTaps)
                Text(dots.isEmpty ? L(R.string.pay_step_pad_none) : dots).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityLabel("\(shownTaps)")
                if step.canClear {
                    SecondaryButton(L(R.string.pay_step_clear)) {
                        Task { if let n = await model.paymentPadClear(step.stepId) { taps = n }; tick += 1 }
                    }
                }
            }
            if problem && image == nil { ErrorText(L(R.string.pay_step_pad_problem), small: true) }
            MutedText(L(R.string.pay_step_pad_note), small: true)
            SecondaryButton(L(R.string.pay_step_cancel), fill: true) { Haptics.tick(); model.answerPaymentStep(step.stepId, "cancel") }
            LinkButton(L(R.string.pay_step_full_view), action: openLive)
        }
        .onChange(of: step.taps) { _, t in taps = t }
        .task(id: "\(step.stepId)#\(tick)") {
            while !Task.isCancelled {
                let bytes = await model.paymentPad(step.stepId)
                problem = bytes == nil
                if let bytes, let img = PlatformImage(data: bytes) { image = img }
                try? await Task.sleep(nanoseconds: 700_000_000)
            }
        }
    }
}

/// An ended step folded into one line.
private struct EndedLine: View {
    let step: PaymentStep
    @Environment(\.kelo) private var c

    var body: some View {
        let (icon, words, muted): (KeloIcon, StringKey, Bool) = switch step.state {
        case "approved": (.check, R.string.pay_step_done_approved, false)
        case "done": (.check, R.string.pay_step_done, false)
        case "cancelled": (.close, R.string.pay_step_done_cancelled, true)
        case "timeout": (.clock, R.string.pay_step_done_timeout, true)
        default: (.clock, R.string.pay_step_done_closed, true)
        }
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            KeloIconView(icon, size: 16, tint: muted ? c.muted : c.success)
            Text(L(words, step.site.isEmpty ? "-" : step.site)).font(KeloFont.small).foregroundStyle(muted ? c.muted : c.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A payment's PIN waits for this phone: "확인하고 보내기" (Face ID), the time left, or how it went.
struct PinNeedCard: View {
    let row: PinNeedRow
    let onSend: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let need = row.need
        TimelineView(.periodic(from: .now, by: 1)) { tl in
            let left = max(0, need.expiresAt - Int64(tl.date.timeIntervalSince1970))
            CardBox {
                if row.send == .sent {
                    line(.check, L(R.string.pin_need_sent, need.site), muted: false)
                } else if left <= 0 {
                    line(.clock, L(R.string.pin_need_over, need.site), muted: true)
                } else {
                    Text(L(R.string.pin_need_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    Text(L(R.string.pin_need_body, need.pinLabel.isEmpty ? need.site : need.pinLabel, need.site, need.amountText))
                        .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    MutedText(L(R.string.chat_card_needed_left, PaymentStepRules.clock(left)), small: true)
                    if case .failed(let e) = row.send { ErrorText(ChatWords.cardSendError(e), small: true) }
                    PrimaryButton(L(row.send == .working ? R.string.chat_card_sending : R.string.pin_need_send), enabled: row.send != .working, action: onSend)
                        .accessibilityIdentifier("kelo.pin.release")
                }
            }
        }
    }

    private func line(_ icon: KeloIcon, _ text: String, muted: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            KeloIconView(icon, size: 16, tint: muted ? c.muted : c.success)
            Text(text).font(KeloFont.small).foregroundStyle(muted ? c.muted : c.ink).fixedSize(horizontal: false, vertical: true)
        }
    }
}
