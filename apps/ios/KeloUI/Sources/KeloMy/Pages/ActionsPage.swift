import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Kelo가 한 일 (Android my/ActionsPage.kt): what Kelo did outside, when, where and from which run (GET /api/actions).
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
                // Period chips
                HStack(spacing: 8) {
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
    @Environment(\.kelo) private var c

    var body: some View {
        let icon: KeloIcon = switch item.kind {
        case "send": .send
        case "change": .edit
        case "delete": .trash
        default: .spark
        }
        let time = item.ts > 0 ? MyFormat.clock(item.ts) : ""
        let subtitle = [item.sourceName, time].filter { !$0.isEmpty }.joined(separator: " · ")
        let tagText: String? = switch item.state {
        case "unknown": L(R.string.actions_state_unknown)
        case "failed": L(R.string.actions_state_failed)
        default: nil
        }
        let tagColor: Color = switch item.state {
        case "failed": c.danger
        case "unknown": c.warn
        default: c.muted
        }

        let content = HStack(spacing: 16) {
            KeloIconView(icon, size: 24, tint: c.muted)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.line)
                    .font(KeloFont.bodyLarge)
                    .foregroundStyle(c.ink)
                    .lineLimit(2)
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

        if let taskId = item.taskId {
            Button {
                onOpenTask(taskId)
            } label: {
                content
            }
            .buttonStyle(.plain)
        } else {
            content
        }
    }
}
