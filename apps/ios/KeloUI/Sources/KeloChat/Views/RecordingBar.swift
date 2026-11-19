import SwiftUI
import KeloShared

/// The composer while the owner speaks (Android voice/RecordingBar.kt): one row in place of the field, inside the composer's card.
/// Left to right: ✕ (drop the recording), the clock, the live sound graph (or "글자로 바꾸는 중…" while the text comes back), ■ (the words
/// go into the field) and ↑ (sent, when Kelo is not replying). No placeholder, no "+", no model chip, no text labels.
struct RecordingBarView: View {
    let state: RecordingBarState
    let trail: LevelTrail
    let busy: Bool
    let onDiscard: () -> Void
    let onStop: () -> Void
    let onSend: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let sendOn = RecordingBarRules.sendEnabled(state.stage, busy: busy)
        let clock = RecordingClock.clock(state.elapsedMs)
        HStack(spacing: 8) {
            barButton(.close, size: 18, fill: c.neutralSoft, tint: c.ink, enabled: true,
                      label: L(R.string.voice_cancel_recording), id: "kelo.voice.discard", action: onDiscard)
            Text(clock)
                .font(KeloFont.body)
                .monospacedDigit()
                .foregroundStyle(state.endsInSeconds != nil ? c.dangerInk : c.muted)
                .frame(minWidth: 36, alignment: .leading)
                .accessibilityLabel(L(R.string.voice_elapsed, clock))
            if state.stage == .listening {
                Waveform(trail: trail)
                    .frame(maxWidth: .infinity)
                    .frame(height: 28)
                    .accessibilityHidden(true)
            } else {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small).tint(c.accent)
                    Text(L(R.string.voice_transcribing))
                        .font(KeloFont.small)
                        .foregroundStyle(c.muted)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            barButton(.stop, size: 16, fill: c.neutralSoft, tint: c.ink, enabled: state.stage == .listening,
                      label: L(R.string.voice_stop_recording), id: "kelo.voice.stop", action: onStop)
            barButton(.send, size: 20, fill: sendOn ? c.ink : c.disabledFill, tint: sendOn ? c.bg : c.disabledInk, enabled: sendOn,
                      label: L(R.string.voice_send_now), id: "kelo.voice.send", action: onSend)
        }
        .frame(height: 56)
        .padding(.horizontal, 8)
        .accessibilityElement(children: .contain)
        .accessibilityValue(L(state.stage == .listening ? R.string.voice_listening : R.string.voice_transcribing))
    }

    private func barButton(_ icon: KeloIcon, size: CGFloat, fill: Color, tint: Color, enabled: Bool, label: String, id: String,
                           action: @escaping () -> Void) -> some View {
        Button(action: action) {
            KeloIconView(icon, size: size, tint: tint)
                .frame(width: 40, height: 40)
                .background(Circle().fill(fill))
                .contentShape(Circle())
        }
        .buttonStyle(PressShrinkStyle())
        .disabled(!enabled)
        .accessibilityLabel(label)
        .accessibilityIdentifier(id)
    }
}

/// The levels as dots and bars, newest at the trailing edge. Pitch 5 pt, bars 3 pt wide, 3 to 24 pt tall (RecordingBarRules).
struct Waveform: View {
    let trail: LevelTrail
    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduce)) { _ in
            Canvas { ctx, size in
                let pitch = CGFloat(RecordingBarRules.dotDp + RecordingBarRules.gapDp)
                let width = CGFloat(RecordingBarRules.dotDp)
                let count = RecordingBarRules.visibleBars(widthDp: Float(size.width))
                for (k, level) in trail.snapshot().suffix(count).reversed().enumerated() {
                    let x = size.width - width - CGFloat(k) * pitch
                    guard x >= -width else { break }
                    let h = CGFloat(RecordingBarRules.height(level))
                    let rect = CGRect(x: x, y: (size.height - h) / 2, width: width, height: h)
                    let path = Path(roundedRect: rect, cornerRadius: 1.5)
                    ctx.fill(path, with: .color(c.muted))
                    // a louder bar is drawn over with the ink colour (the colour grows with the level)
                    ctx.fill(path, with: .color(c.ink.opacity(Double(level))))
                }
            }
        }
    }
}
