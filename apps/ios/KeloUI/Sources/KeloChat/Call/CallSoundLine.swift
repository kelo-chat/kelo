import SwiftUI
import KeloCore
import KeloShared

/// The live sound line: the owner's mic while listening, Kelo's playback while speaking (the shape is CallSoundLineRules).
struct CallSoundLine: View {
    let trail: LevelTrail
    let phase: CallSteps.Phase

    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce

    private var active: Bool {
        phase == .listening || phase == .hearing || phase == .speaking
    }

    private var lineColor: Color {
        switch phase {
        case .speaking: return c.accent
        case .listening, .hearing: return c.ink
        default: return c.line
        }
    }

    var body: some View {
        TimelineView(.animation(paused: !active || reduce)) { timeline in
            Canvas { context, size in
                let levels = trail.snapshot()
                let t = reduce ? Float(0) : Float(timeline.date.timeIntervalSinceReferenceDate)
                // Kelo's device voice gives no levels: a quiet trail while speaking moves the line a little
                let synthetic = phase == .speaking && (levels.last ?? 0) < 0.01
                let pts = CallSoundLineRules.points(levels: levels, n: levels.count, count: 48, timeSec: t, synthetic: synthetic)
                let cy = size.height / 2
                let xs = pts.indices.map { CGFloat($0) * size.width / CGFloat(pts.count - 1) }
                let ys = pts.map { cy - CGFloat($0) * size.height * 0.45 }
                var path = Path()
                path.move(to: CGPoint(x: xs[0], y: ys[0]))
                for i in 1..<pts.count {
                    let mid = CGPoint(x: (xs[i - 1] + xs[i]) / 2, y: (ys[i - 1] + ys[i]) / 2)
                    path.addQuadCurve(to: mid, control: CGPoint(x: xs[i - 1], y: ys[i - 1]))
                }
                path.addLine(to: CGPoint(x: xs[xs.count - 1], y: ys[ys.count - 1]))
                context.stroke(path, with: .color(lineColor), style: StrokeStyle(lineWidth: 2, lineCap: .round))
            }
        }
        .frame(height: 48)
        .accessibilityHidden(true)
    }
}

/// This call's lines: the owner's words on the right, Kelo's reply on the left; the newest line stays in view.
struct CallTranscript: View {
    let lines: [CallLine]
    let agentName: String

    @Environment(\.kelo) private var c
    @Environment(\.accessibilityReduceMotion) private var reduce

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(lines) { line in
                        CallLineView(line: line, agentName: agentName).id(line.id)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
            .onChange(of: lines) { _, now in
                guard let last = now.last else { return }
                if reduce {
                    proxy.scrollTo(last.id, anchor: .bottom)
                } else {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }
}

/// One line of a call: the owner's words on the right, Kelo's on the left with its picture and name (the call screen, the chat's call card).
struct CallLineView: View {
    let line: CallLine
    let agentName: String

    @Environment(\.kelo) private var c

    var body: some View {
        if line.mine {
            HStack {
                Spacer(minLength: 48)
                Text(line.text)
                    .font(.body)
                    .foregroundColor(c.ink)
                    .padding(.horizontal, 15)
                    .padding(.vertical, 11)
                    .background(RoundedRectangle(cornerRadius: 20).fill(c.mine))
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("call.line.mine")
        } else {
            HStack(alignment: .top, spacing: 8) {
                AgentPicture(image: nil, size: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text(agentName)
                        .font(.caption)
                        .foregroundColor(c.muted)
                    Text(line.text)
                        .font(.body)
                        .foregroundColor(c.ink)
                        .padding(.horizontal, 15)
                        .padding(.vertical, 11)
                        .background(RoundedRectangle(cornerRadius: 20).fill(c.bubble))
                }
                Spacer(minLength: 48)
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("call.line.kelo")
        }
    }
}

/// The words typed into the call: sent as a call turn (answered by voice). Takes focus when it shows.
struct CallTypeBar: View {
    @Binding var text: String
    let onSend: (String) -> Void

    @Environment(\.kelo) private var c
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            TextField(L(R.string.call_type_hint), text: $text)
                .lineLimit(1)
                .focused($focused)
                .submitLabel(.send)
                .onSubmit(send)
                .accessibilityIdentifier("call.typeField")
            Button(action: send) {
                KeloIconView(.send, size: 20, tint: c.accent)
            }
            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityLabel(L(R.string.call_type_send))
            .accessibilityIdentifier("call.typeSend")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .onAppear { focused = true }
    }

    private func send() {
        let words = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return }
        onSend(words)
        text = ""
    }
}
