import SwiftUI
import KeloCore

/// The message an approval will send, with "Edit" under it (Android EditableMessageBlock). Saving the sheet keeps the text for the Allow
/// (ApprovalDrafts); the card then shows it with "You changed the message".
public struct EditableMessageBlock: View {
    let approvalId: String
    let editable: EditableMessage
    let enabled: Bool
    @State private var draft: String?
    @State private var editing = false
    @Environment(\.kelo) private var c

    public init(approvalId: String, editable: EditableMessage, enabled: Bool) {
        self.approvalId = approvalId; self.editable = editable; self.enabled = enabled
        _draft = State(initialValue: ApprovalDrafts.get(approvalId))
    }

    public var body: some View {
        let text = ApprovalEditRules.shown(editable, draft: draft)
        VStack(alignment: .leading, spacing: 6) {
            CappedScrollText(text)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 10).fill(c.bubble))
            HStack(spacing: 8) {
                if ApprovalEditRules.toSend(editable, draft: draft) != nil {
                    Text(L(R.string.approval_edited_note)).font(KeloFont.label).foregroundStyle(c.muted)
                }
                Spacer(minLength: 0)
                if enabled { LinkButton(L(R.string.approval_edit_button)) { editing = true } }
            }
        }
        .sheet(isPresented: $editing) {
            EditMessageSheet(initial: text, max: editable.max) { saved in
                ApprovalDrafts.set(approvalId, saved)
                draft = saved
                editing = false
            } onCancel: { editing = false }
            .presentationDetents([.medium, .large])
        }
    }
}

/// The sheet: the text, a counter when the service has a limit, and the two refusals (empty, too long) next to Save.
public struct EditMessageSheet: View {
    let initial: String
    let max: Int?
    let onSave: (String) -> Void
    let onCancel: () -> Void
    @State private var message: String
    @State private var tried = false
    @Environment(\.kelo) private var c

    public init(initial: String, max: Int?, onSave: @escaping (String) -> Void, onCancel: @escaping () -> Void) {
        self.initial = initial; self.max = max; self.onSave = onSave; self.onCancel = onCancel
        _message = State(initialValue: initial)
    }

    public var body: some View {
        let problem = ApprovalEditRules.problem(message, max: max)
        NavigationStack {
            VStack(alignment: .leading, spacing: 10) {
                KeloTextField(L(R.string.approval_edit_title), text: $message, axis: .vertical)
                HStack(spacing: 8) {
                    if let problem, tried || problem == .tooLong {
                        Text(L(problem == .empty ? R.string.approval_edit_empty : R.string.approval_edit_too_long))
                            .font(KeloFont.small).foregroundStyle(c.danger)
                    }
                    Spacer(minLength: 0)
                    if let counter = ApprovalEditRules.counter(message, max: max) {
                        Text(L(R.string.approval_edit_count, counter.count, counter.max))
                            .font(KeloFont.small).foregroundStyle(counter.count > counter.max ? c.danger : c.muted)
                    }
                }
                PrimaryButton(L(R.string.approval_edit_save)) {
                    tried = true
                    if problem == nil { onSave(message.trimmingCharacters(in: .whitespacesAndNewlines)) }
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .navigationTitle(L(R.string.approval_edit_title))
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(L(R.string.cancel), action: onCancel) } }
        }
    }
}
