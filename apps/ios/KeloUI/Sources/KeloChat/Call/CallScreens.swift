import SwiftUI
import KeloCore
import KeloShared

// MARK: - Call Control Button

public struct CallControl: View {
    let icon: KeloIcon
    let size: CGFloat
    let bg: Color
    let tint: Color
    let label: String
    let accessibilityTag: String
    let isToggle: Bool
    let action: () -> Void

    @Environment(\.kelo) private var c

    public init(
        icon: KeloIcon,
        size: CGFloat = 60,
        bg: Color,
        tint: Color,
        label: String,
        accessibilityTag: String,
        isToggle: Bool = false,
        action: @escaping () -> Void
    ) {
        self.icon = icon
        self.size = size
        self.bg = bg
        self.tint = tint
        self.label = label
        self.accessibilityTag = accessibilityTag
        self.isToggle = isToggle
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                ZStack {
                    Circle()
                        .fill(bg)
                        .frame(width: size, height: size)
                    KeloIconView(icon, size: size * 0.44, tint: tint)
                }
                Text(label)
                    .font(.caption)
                    .foregroundColor(c.muted)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(maxWidth: size + 20)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityIdentifier(accessibilityTag)
        .frame(minWidth: 48, minHeight: 48)
    }
}

// MARK: - Incoming Call Screen

public struct IncomingCallView: View {
    let session: CallSession
    let agentName: String
    let photo: Image?

    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce

    public init(session: CallSession, agentName: String, photo: Image? = nil) {
        self.session = session
        self.agentName = agentName
        self.photo = photo
    }

    public var body: some View {
        ZStack {
            c.bg.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                // Avatar 120pt with 2 soft pulsing rings
                ZStack {
                    if !reduce {
                        TimelineView(.animation(minimumInterval: 1.0 / 30)) { tl in
                            let t = tl.date.timeIntervalSinceReferenceDate
                            let phase = (t.truncatingRemainder(dividingBy: 1.6)) / 1.6
                            Circle()
                                .fill(c.accent.opacity(0.18 * (1.0 - phase)))
                                .frame(width: 120 + CGFloat(phase * 40), height: 120 + CGFloat(phase * 40))
                            Circle()
                                .fill(c.accent.opacity(0.08 * (1.0 - phase)))
                                .frame(width: 120 + CGFloat(phase * 70), height: 120 + CGFloat(phase * 70))
                        }
                    }
                    Circle()
                        .fill(c.card)
                        .frame(width: 120, height: 120)
                        .overlay(
                            Group {
                                if let photo {
                                    photo
                                        .resizable()
                                        .scaledToFill()
                                        .clipShape(Circle())
                                } else {
                                    KeloCharacterView(.idle)
                                        .frame(width: 88, height: 88)
                                }
                            }
                        )
                }
                .frame(width: 190, height: 190)

                Spacer().frame(height: 16)

                Text(agentName)
                    .font(.title3.weight(.semibold))
                    .foregroundColor(c.ink)
                    .lineLimit(1)

                Spacer().frame(height: 6)

                Text(L(R.string.call_incoming_line))
                    .font(.subheadline)
                    .foregroundColor(c.muted)
                    .multilineTextAlignment(.center)

                if !session.topic.isEmpty {
                    Spacer().frame(height: 4)
                    Text(session.topic)
                        .font(.subheadline)
                        .foregroundColor(c.ink)
                        .multilineTextAlignment(.center)
                        .lineLimit(3)
                        .padding(.horizontal, 24)
                }

                Spacer()

                HStack {
                    Spacer()
                    CallControl(
                        icon: .handsetDown,
                        size: 72,
                        bg: c.danger,
                        tint: c.onDanger,
                        label: L(R.string.call_decline),
                        accessibilityTag: "call.decline",
                        action: { session.decline() }
                    )
                    Spacer()
                    CallControl(
                        icon: .handset,
                        size: 72,
                        bg: c.success,
                        tint: c.onSuccess,
                        label: L(R.string.call_accept),
                        accessibilityTag: "call.accept",
                        action: { session.accept() }
                    )
                    Spacer()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 40)
            }
            .padding(.horizontal, 24)
        }
        .accessibilityIdentifier("call.incoming")
    }
}

// MARK: - In Call Screen

public struct InCallView: View {
    let session: CallSession
    let agentName: String
    let onMinimize: () -> Void

    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce

    public init(session: CallSession, agentName: String, onMinimize: @escaping () -> Void) {
        self.session = session
        self.agentName = agentName
        self.onMinimize = onMinimize
    }

    private var stateLineText: String {
        if session.ui.muted {
            return L(R.string.call_muted)
        }
        switch session.ui.phase {
        case .connecting, .preparing:
            return L(R.string.call_connecting)
        case .listening, .hearing:
            return L(R.string.call_listening)
        case .transcribing, .waiting:
            return L(R.string.call_thinking)
        case .speaking:
            return L(R.string.call_speaking)
        case .ended:
            return L(R.string.call_hang_up)
        default:
            return L(R.string.call_connecting)
        }
    }

    public var body: some View {
        ZStack {
            c.bg.ignoresSafeArea()

            VStack(spacing: 0) {
                // Top header with minimize button
                HStack {
                    Button(action: onMinimize) {
                        HStack(spacing: 4) {
                            KeloIconView(.chevronDown, size: 16, tint: c.muted)
                            Text(L(R.string.call_card_open))
                                .font(.subheadline)
                                .foregroundColor(c.muted)
                        }
                        .padding(8)
                    }
                    .accessibilityLabel(L(R.string.call_card_open))

                    Spacer()
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)

                Spacer()

                // Avatar 136pt with speaking ring
                ZStack {
                    if session.ui.phase == .speaking && !reduce {
                        let level = Double(session.trail.snapshot().last ?? 0)
                        let ringWidth = 2.0 + level * 6.0
                        Circle()
                            .stroke(c.accent, lineWidth: CGFloat(ringWidth))
                            .frame(width: 136 + CGFloat(ringWidth), height: 136 + CGFloat(ringWidth))
                    }

                    Circle()
                        .fill(c.card)
                        .frame(width: 136, height: 136)
                        .overlay(
                            KeloCharacterView(CallRules.mood(for: session.ui.phase))
                                .frame(width: 104, height: 104)
                        )
                }
                .frame(width: 160, height: 160)
                .contentShape(Circle())
                .onTapGesture {
                    if session.ui.phase == .speaking {
                        session.stopVoice()
                    }
                }

                Spacer().frame(height: 16)

                Text(agentName)
                    .font(.title3.weight(.semibold))
                    .foregroundColor(c.ink)
                    .lineLimit(1)

                Spacer().frame(height: 4)

                Text(stateLineText)
                    .font(.subheadline)
                    .foregroundColor(c.muted)

                Spacer().frame(height: 2)

                Text(CallRules.clock(elapsedMs: session.ui.elapsedMs))
                    .font(.footnote)
                    .monospacedDigit()
                    .foregroundColor(c.muted)

                Spacer().frame(height: 20)

                // Waveform
                Waveform(trail: session.trail)
                    .frame(maxWidth: 280, minHeight: 40, maxHeight: 40)
                    .padding(.horizontal, 24)

                // Offline alert line
                if session.ui.offline {
                    Spacer().frame(height: 12)
                    Text(L(R.string.call_offline))
                        .font(.footnote)
                        .foregroundColor(c.dangerInk)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }

                // Voice Line prompt
                if session.ui.voiceLine {
                    Spacer().frame(height: 12)
                    HStack(spacing: 8) {
                        Text(L(R.string.call_voice_better))
                            .font(.footnote)
                            .foregroundColor(c.ink)
                            .lineLimit(1)
                        Button(L(R.string.call_voice_get)) {
                            session.getBetterVoice()
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundColor(c.accent)

                        Button(action: { session.dismissVoiceLine() }) {
                            KeloIconView(.close, size: 14, tint: c.muted)
                        }
                        .accessibilityLabel(L(R.string.call_voice_line_close))
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(
                        RoundedRectangle(cornerRadius: 12)
                            .fill(c.neutralSoft)
                    )
                }

                // Card waiting pill
                if session.ui.cardWaiting {
                    Spacer().frame(height: 12)
                    HStack(spacing: 8) {
                        Text(L(R.string.call_card_waiting))
                            .font(.footnote)
                            .foregroundColor(c.infoInk)
                        Button(L(R.string.call_card_open)) {
                            onMinimize()
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundColor(c.infoInk)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(
                        Capsule()
                            .fill(c.infoSoft)
                    )
                }

                Spacer()

                // Bottom Call Controls
                HStack(spacing: 28) {
                    CallControl(
                        icon: session.ui.muted ? .micOff : .mic,
                        size: 60,
                        bg: session.ui.muted ? c.ink : c.neutralSoft,
                        tint: session.ui.muted ? c.bg : c.ink,
                        label: session.ui.muted ? L(R.string.call_unmute) : L(R.string.call_mute),
                        accessibilityTag: "call.mute",
                        isToggle: true,
                        action: { session.setMuted(!session.ui.muted) }
                    )

                    CallControl(
                        icon: .speaker,
                        size: 60,
                        bg: session.ui.speakerOn ? c.neutralSoft : c.card,
                        tint: c.ink,
                        label: L(R.string.call_speaker),
                        accessibilityTag: "call.speaker",
                        isToggle: true,
                        action: { session.setSpeaker(!session.ui.speakerOn) }
                    )

                    CallControl(
                        icon: .handsetDown,
                        size: 72,
                        bg: c.danger,
                        tint: c.onDanger,
                        label: L(R.string.call_hang_up),
                        accessibilityTag: "call.hangup",
                        action: { session.hangUp() }
                    )
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 40)
            }
        }
        .accessibilityIdentifier("call.active")
    }
}

// MARK: - Minimized Call Pill

public struct CallPill: View {
    let session: CallSession
    let onExpand: () -> Void

    @Environment(\.kelo) private var c

    public init(session: CallSession, onExpand: @escaping () -> Void) {
        self.session = session
        self.onExpand = onExpand
    }

    public var body: some View {
        Button(action: onExpand) {
            HStack(spacing: 8) {
                KeloIconView(.handset, size: 14, tint: c.success)
                Text(L(R.string.call_back_to_call, CallRules.clock(elapsedMs: session.ui.elapsedMs)))
                    .font(.footnote.weight(.medium))
                    .monospacedDigit()
                    .foregroundColor(c.ink)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(
                Capsule()
                    .fill(c.card)
                    .overlay(Capsule().stroke(c.line, lineWidth: 1))
                    .shadow(color: Color.black.opacity(0.12), radius: 8, x: 0, y: 3)
            )
        }
        .accessibilityIdentifier("call.pill")
    }
}

// MARK: - Call Bubble Mark

public struct CallMark: View {
    @Environment(\.kelo) private var c

    public init() {}

    public var body: some View {
        HStack(spacing: 4) {
            KeloIconView(.handset, size: 14, tint: c.muted)
            Text(L(R.string.call_mark))
                .font(.caption2)
                .foregroundColor(c.muted)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Full Call Overlay

public struct CallOverlay: View {
    @Bindable var calls: CallCenter
    let agentName: String

    @Environment(\.kelo) private var c

    public init(calls: CallCenter, agentName: String = "Kelo") {
        self.calls = calls
        self.agentName = agentName
    }

    public var body: some View {
        if let session = calls.session, session.ui.phase != .ended {
            if calls.isMinimized {
                VStack {
                    CallPill(session: session) {
                        calls.minimized(false)
                    }
                    .padding(.top, 56)
                    Spacer()
                }
                .transition(.move(edge: .top).combined(with: .opacity))
            } else if session.ui.phase == .ringing {
                IncomingCallView(session: session, agentName: agentName)
                    .transition(.opacity)
            } else {
                InCallView(session: session, agentName: agentName) {
                    calls.minimized(true)
                }
                .transition(.opacity)
                .alert(
                    L(R.string.call_needs_speech_model),
                    isPresented: Binding(
                        get: { session.ui.need == .sttQuestion },
                        set: { _ in }
                    )
                ) {
                    Button(L(R.string.call_voice_offer_ok)) {
                        session.answerSttQuestion(ok: true)
                    }
                    Button(L(R.string.call_voice_offer_later), role: .cancel) {
                        session.answerSttQuestion(ok: false)
                    }
                }
                .alert(
                    L(R.string.call_voice_offer_title),
                    isPresented: Binding(
                        get: { session.ui.need == .voiceQuestion },
                        set: { _ in }
                    )
                ) {
                    Button(L(R.string.call_voice_offer_ok)) {
                        session.answerVoiceQuestion(ok: true)
                    }
                    Button(L(R.string.call_voice_offer_later), role: .cancel) {
                        session.answerVoiceQuestion(ok: false)
                    }
                } message: {
                    Text(L(R.string.call_voice_offer, "15 MB"))
                }
            }
        }
    }
}
