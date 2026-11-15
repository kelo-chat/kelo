import ActivityKit
import AppIntents
import KeloLive
import SwiftUI
import WidgetKit

@main
struct KeloLiveBundle: WidgetBundle {
    var body: some Widget { ReplyActivityWidget() }
}

/// The app's accent colour (KeloUI's theme: light #4F52E0, dark #8C8FF7).
private func accent(_ scheme: ColorScheme) -> Color {
    scheme == .dark ? Color(red: 0x8C / 255, green: 0x8F / 255, blue: 0xF7 / 255) : Color(red: 0x4F / 255, green: 0x52 / 255, blue: 0xE0 / 255)
}

/// What the activity shows, opened from the sealed words (the key is in the shared keychain group).
private struct Shown {
    let step: String
    let site: String
    let state: String
    let n: Int

    init(_ context: ActivityViewContext<KeloLiveAttributes>) {
        if LiveKeys.accessGroup == nil {
            LiveKeys.accessGroup = Bundle.main.object(forInfoDictionaryKey: "KeloLiveKeychainGroup") as? String
        }
        let plain = LiveKeys.key(context.attributes.keyId).flatMap { LiveSeal.open(context.state.sealed, key: $0) }
        n = context.state.n
        state = plain?.state ?? "working"
        site = plain?.site ?? ""
        let words = Shown.stateWords(plain?.state ?? "working")
        step = (plain?.step.isEmpty == false ? plain?.step : nil) ?? words
    }

    var running: Bool { state == "working" || state == "waiting" }
    var done: Bool { state == "done" }
    var stateWords: String { Shown.stateWords(state) }
    var detail: String {
        let steps = String(format: String(localized: "live_steps"), n)
        return site.isEmpty ? steps : "\(site) · \(steps)"
    }

    static func stateWords(_ state: String) -> String {
        switch state {
        case "waiting": return String(localized: "live_waiting")
        case "done": return String(localized: "live_done")
        case "failed": return String(localized: "live_failed")
        case "stopped": return String(localized: "live_stopped")
        default: return String(localized: "live_working")
        }
    }
}

private struct Mark: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View { Image(systemName: "sparkles").foregroundStyle(accent(scheme)) }
}

private struct StopButton: View {
    let turnId: String
    var body: some View {
        Button(intent: StopReplyIntent(turnId: turnId)) {
            Label(String(localized: "live_stop"), systemImage: "stop.fill").font(.footnote.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
    }
}

private struct LockView: View {
    let context: ActivityViewContext<KeloLiveAttributes>
    var body: some View {
        let s = Shown(context)
        HStack(spacing: 12) {
            Mark().font(.title2)
            VStack(alignment: .leading, spacing: 2) {
                Text(s.step).font(.headline).lineLimit(2)
                Text(s.detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            if s.running { StopButton(turnId: context.attributes.turnId) }
            else { Text(s.stateWords).font(.footnote.weight(.semibold)).foregroundStyle(.secondary) }
        }
        .padding(16)
    }
}

struct ReplyActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: KeloLiveAttributes.self) { context in
            LockView(context: context)
        } dynamicIsland: { context in
            let s = Shown(context)
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { Mark() }
                DynamicIslandExpandedRegion(.center) { Text(s.step).font(.headline).lineLimit(2) }
                DynamicIslandExpandedRegion(.bottom) {
                    HStack {
                        Text(s.site.isEmpty ? s.detail : s.site).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        Spacer()
                        if s.running { StopButton(turnId: context.attributes.turnId) }
                        else { Text(s.stateWords).font(.footnote.weight(.semibold)) }
                    }
                }
            } compactLeading: {
                Mark()
            } compactTrailing: {
                if s.done { Image(systemName: "checkmark") } else { Text("\(s.n)") }
            } minimal: {
                Mark()
            }
            .keylineTint(accent(.dark))
        }
    }
}
