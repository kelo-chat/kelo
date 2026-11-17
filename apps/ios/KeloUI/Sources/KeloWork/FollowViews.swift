import SwiftUI
import KeloCore
import KeloShared

/// One row of "Kelo가 챙기는 일" (Android follow/FollowParts.kt FollowItemRow): the title, when, what Kelo does, and the prep states.
struct FollowItemRow: View {
    let item: FollowItem
    let onOpen: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let marker: RailMarker = switch item.state {
        case "stopped": .paused
        case "done": .done
        default: .live
        }
        RailItem(marker, label: item.startsAt > 0 ? WorkText.clock(item.startsAt) : nil) {
            EntryBox(action: onOpen) {
                HStack(alignment: .top, spacing: 8) {
                    Text(item.title).font(.system(.callout, weight: .bold)).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                    if item.state == "stopped" { StatusChip(L(R.string.follow_state_stopped), c.muted) }
                }
                if item.startsAt > 0 {
                    Text(WorkText.dayClock(item.startsAt)).font(KeloFont.small).foregroundStyle(c.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                let summary = FollowRules.planSummary(item)
                if !summary.isEmpty {
                    Text(summary).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                if !item.prep.isEmpty {
                    FlowLayout(spacing: 6, lineSpacing: 4) {
                        ForEach(Array(item.prep.enumerated()), id: \.offset) { _, p in
                            let state = FollowRules.prepStateText(p.state)
                            StatusChip(p.what.isEmpty ? state : "\(p.what): \(state)", prepTone(p.state, c))
                        }
                    }
                    .padding(.bottom, 4)
                }
            }
        }
    }
}

func prepTone(_ state: String, _ c: KeloColors) -> Color {
    switch state {
    case "running": c.warn
    case "done": c.success
    case "failed": c.danger
    default: c.muted
    }
}

/// The plan of one item with "그만 챙기기" (asks first) and "다시 계획하기".
struct FollowPlanSheet: View {
    let item: FollowItem
    let onClose: () -> Void
    let onStop: () -> Void
    let onReplan: () -> Void
    @State private var confirmStop = false
    @Environment(\.kelo) private var c

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SheetHeader(item.title, onClose: onClose)
                if item.startsAt > 0 {
                    Text(WorkText.dayClock(item.startsAt)).font(KeloFont.small).foregroundStyle(c.muted)
                }
                HStack(spacing: 6) {
                    StatusChip(FollowRules.itemStateText(item.state), item.state == "stopped" ? c.muted : c.accent)
                }

                if !item.checks.isEmpty {
                    section(L(R.string.follow_plan_checks)) {
                        ForEach(Array(item.checks.enumerated()), id: \.offset) { _, ch in
                            let fields = ch.fields.isEmpty ? ch.kind : ch.fields.joined(separator: ", ")
                            let desc = (!ch.from.isEmpty && !ch.until.isEmpty) ? "\(fields) (\(ch.from) ~ \(ch.until))" : fields
                            line("• \(desc)")
                            if let next = ch.nextAt, next > 0 {
                                Text(L(R.string.follow_next_check, WorkText.dayClock(next))).font(KeloFont.label).foregroundStyle(c.muted)
                                    .fixedSize(horizontal: false, vertical: true).padding(.leading, 10)
                            }
                        }
                    }
                }

                let preps = FollowRules.preps(item)
                if !preps.isEmpty {
                    section(L(R.string.follow_plan_prep)) {
                        ForEach(Array(preps.enumerated()), id: \.offset) { _, p in
                            HStack(alignment: .top, spacing: 8) {
                                line("• \(p.what)" + (p.when.isEmpty ? "" : " (\(p.when))"))
                                StatusChip(FollowRules.prepStateText(p.state), prepTone(p.state, c))
                            }
                        }
                    }
                }

                if let tell = item.plan?.tell, !tell.lead.isEmpty || tell.onChange {
                    section(L(R.string.follow_plan_tell)) {
                        ForEach(Array(tell.lead.enumerated()), id: \.offset) { _, offset in
                            line("• " + (FollowRules.leadText(offset, kind: item.kind) ?? offset))
                        }
                        if tell.onChange { line("• " + L(R.string.follow_plan_tell_change)) }
                    }
                }

                VStack(spacing: 8) {
                    if item.state != "stopped" {
                        SecondaryButton(L(R.string.follow_stop), fill: true) { confirmStop = true }
                    }
                    PrimaryButton(L(R.string.follow_replan)) { onReplan(); onClose() }
                }
                .padding(.top, 8)
            }
            .padding(.horizontal, 20).padding(.bottom, 28)
        }
        .alert(L(R.string.follow_stop_confirm), isPresented: $confirmStop) {
            Button(L(R.string.follow_stop_yes), role: .destructive) { onStop(); onClose() }
            Button(L(R.string.follow_keep_watching), role: .cancel) {}
        } message: {
            Text(L(R.string.follow_stop_confirm_detail))
        }
    }

    private func line(_ text: String) -> some View {
        Text(text).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(.footnote, weight: .bold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            content()
        }
    }
}
