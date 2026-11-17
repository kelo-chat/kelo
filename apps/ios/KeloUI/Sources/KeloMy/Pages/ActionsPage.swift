import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Kelo가 한 일 (Android my/ActionsPage.kt): what Kelo did outside, when, where and from which run (GET /api/did).
struct ActionsPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: ActionsModel

    init(api: KeloApi) {
        _model = State(initialValue: ActionsModel(api: api))
    }

    var body: some View {
        SubPage(
            L(R.string.actions_title),
            onBack: { dismiss() },
            actions: {
                Button {
                    Task { await model.refresh() }
                } label: {
                    KeloIconView(.retry, size: 20, tint: c.muted)
                }
                .accessibilityLabel(L(R.string.refresh))
            }
        ) {
            VStack(alignment: .leading, spacing: 8) {
                // Filter chips: all / send & change / browser / tasks (they wrap on a narrow screen and in long languages)
                WalletWrap(spacing: 8) {
                    ForEach(DidFilter.allCases, id: \.self) { f in
                        ChoiceChip(L(f.labelKey), selected: model.filter == f) {
                            model.setFilter(f)
                        }
                    }
                }
                .padding(.bottom, 2)

                // Period chips
                WalletWrap(spacing: 8) {
                    ForEach(ActionPeriod.allCases, id: \.self) { p in
                        ChoiceChip(L(p.labelKey), selected: model.period == p) {
                            Task { await model.setPeriod(p) }
                        }
                    }
                }
                .padding(.bottom, 4)

                if model.loading && model.items.isEmpty {
                    SkeletonRows(rows: 3)
                } else if model.items.isEmpty {
                    EmptyNote(L(R.string.actions_empty))
                } else {
                    ForEach(model.groups, id: \.label) { group in
                        SectionLabel(MyFormat.dayLabel(group.label))
                        ForEach(group.items, id: \.id) { item in
                            ActionRow(item: item, onOpenTask: { taskId in
                                app.router.open(.work(.task(taskId)))
                            }, onOpenChat: {
                                app.router.open(.chat)
                            })
                        }
                    }
                }

                // Plain words about outside text: one muted sentence at the bottom
                Text(L(R.string.actions_outside_text_note))
                    .font(KeloFont.small)
                    .foregroundStyle(c.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 16)
                    .padding(.bottom, 8)
            }
        }
        .refreshable {
            await model.refresh()
        }
        .task {
            await model.load()
        }
    }
}

private struct ActionRow: View {
    let item: ActionItem
    let onOpenTask: (Int64) -> Void
    let onOpenChat: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let icon: KeloIcon = switch item.normalizedKind {
        case "browser": .globe
        case "reply": .chat
        case "task": .work
        case "action":
            switch item.kind {
            case "change": .edit
            case "delete": .trash
            default: .send
            }
        default: .spark
        }
        let time = item.timestamp > 0 ? MyFormat.clock(item.timestamp) : ""
        let origin: String = {
            if let s = item.site, !s.isEmpty { return s }
            if let s = item.connector, !s.isEmpty { return s }
            if !item.sourceName.isEmpty { return item.sourceName }
            switch item.source {
            case "schedule": return L(R.string.agent_source_schedule)
            case "job": return L(R.string.work_source_job)
            case "chat": return L(R.string.tab_chat)
            default: return ""
            }
        }()
        let subtitle = [origin, time].filter { !$0.isEmpty }.joined(separator: " · ")
        let outcome = item.displayOutcome
        let tagText: String? = switch outcome {
        case "unknown": L(R.string.actions_state_unknown)
        case "failed": L(R.string.actions_state_failed)
        case "stopped", "interrupted": L(R.string.agent_task_stopped)
        default: nil
        }
        let tagColor: Color = switch outcome {
        case "failed": c.danger
        case "unknown": c.warn
        default: c.muted
        }

        let content = HStack(spacing: 16) {
            KeloIconView(icon, size: 24, tint: c.muted)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.displayTitle)
                    .font(KeloFont.bodyLarge)
                    .foregroundStyle(c.ink)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                if !item.detail.isEmpty && item.detail != item.displayTitle {
                    Text(item.detail)
                        .font(KeloFont.small)
                        .foregroundStyle(c.muted)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(KeloFont.small)
                        .foregroundStyle(c.muted)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if let tagText {
                Text(tagText)
                    .font(KeloFont.label)
                    .fontWeight(.medium)
                    .foregroundStyle(tagColor)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 6).fill(tagColor.opacity(0.15)))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .contentShape(Rectangle())

        if item.kind == "reply" {
            Button(action: onOpenChat) { content }.buttonStyle(.plain)
        } else if let taskId = item.taskId {
            Button { onOpenTask(taskId) } label: { content }.buttonStyle(.plain)
        } else if item.source == "chat" {
            Button(action: onOpenChat) { content }.buttonStyle(.plain)
        } else {
            content
        }
    }
}
