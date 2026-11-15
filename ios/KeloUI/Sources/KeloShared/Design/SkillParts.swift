import SwiftUI
import KeloCore

// Custom skills in the app (Android agent/SkillParts.kt): the skills waiting for the owner on 마이 → 기록, under the learned rules,
// and "Save as skill" under a finished chat task or background job. Shared by KeloMy (Records) and KeloWork (task and job pages).

/// The skills waiting for the owner on the Records page: loads GET /api/skills/proposals, answers them, and looks again every few
/// seconds while Kelo writes a draft. `onMessage` shows a short message (the page's toast).
public struct SkillReviewSection: View {
    @State private var model: SkillProposalsModel

    public init(api: @escaping () -> KeloApi?, onMessage: @escaping (String) -> Void) {
        _model = State(initialValue: SkillProposalsModel(api: api, say: onMessage))
    }

    public var body: some View {
        // a stack that is there even while nothing shows, so the load below runs
        VStack(alignment: .leading, spacing: 12) { SkillProposalsBlock(data: model.data, onAnswer: model.answer) }
            .frame(maxWidth: .infinity, alignment: .leading)
            .task(id: model.tick) { await model.follow() }
    }
}

/// The list itself: a heading, a line while a draft is being written, then one card per proposal. Nothing when the server has no
/// custom skills or nothing waits.
public struct SkillProposalsBlock: View {
    let data: SkillProposals?
    let onAnswer: (SkillProposal, Bool, @escaping (Bool) -> Void) -> Void
    @Environment(\.kelo) private var c

    public init(data: SkillProposals?, onAnswer: @escaping (SkillProposal, Bool, @escaping (Bool) -> Void) -> Void) {
        self.data = data; self.onAnswer = onAnswer
    }

    public var body: some View {
        if let data, data.available {
            let items = SkillRules.ordered(data.items)
            if !items.isEmpty || !data.drafting.isEmpty {
                Text(L(R.string.skill_review_title)).font(KeloFont.titleSmall).foregroundStyle(c.accent).padding(.top, 6)
                    .fixedSize(horizontal: false, vertical: true)
                MutedText(L(R.string.skill_review_about), small: true)
                if !data.drafting.isEmpty {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.mini).tint(c.muted)
                        Text(L(R.string.skill_review_drafting)).font(KeloFont.small).foregroundStyle(c.muted)
                            .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.horizontal, 4)
                    .accessibilityElement(children: .combine)
                }
                VStack(spacing: 8) {
                    ForEach(items) { p in SkillProposalCard(p: p, onAnswer: onAnswer).id("skill-\(p.id)") }
                }
            }
        }
    }
}

/// One proposal: what kind it is, the skill's name in words, what it is for, where it came from, a note when outside text reached
/// the run (or when its files changed outside the app), then the text as it will be saved or the changed lines, and the answer.
struct SkillProposalCard: View {
    let p: SkillProposal
    let onAnswer: (SkillProposal, Bool, @escaping (Bool) -> Void) -> Void
    @Environment(\.kelo) private var c
    @State private var all = false
    /// nil: not answered; true / false: approved / discarded (shown until the card leaves the list)
    @State private var decided: Bool?
    @State private var sending = false

    private var edited: Bool { p.kind == SkillProposal.edited }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L(p.kind == SkillProposal.change ? R.string.skill_review_kind_change
                   : edited ? R.string.skill_review_kind_edited : R.string.skill_review_kind_new))
                .font(KeloFont.caption).fontWeight(.semibold).foregroundStyle(c.accent).fixedSize(horizontal: false, vertical: true)
            Text(SkillRules.name(p)).font(KeloFont.bodyLarge).fontWeight(.medium).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
            if !p.description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text(p.description).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            Text(origin).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            if edited {
                note(L(p.neverApproved ? R.string.skill_review_edited_new_note : R.string.skill_review_edited_note))
            } else if p.outsideText {
                note(L(R.string.skill_review_outside_note))
            }
            if p.secretsRemoved > 0 {
                Text(L(R.string.skill_review_secrets_removed)).font(KeloFont.small).foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            content
            let more = SkillRules.showsDiff(p) ? SkillRules.hasMore(p.diff) : SkillRules.hasMore(SkillRules.textLines(p.text))
            if more {
                LinkButton(L(all ? R.string.skill_review_show_less : R.string.skill_review_show_all)) { all.toggle() }
            }
            answerRow.padding(.top, 2)
        }
        .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.accent.opacity(0.08)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.accent.opacity(0.45), lineWidth: 1))
    }

    /// "끝난 작업에서 만든 초안 · <reason>"; a change on disk has no reason line.
    private var origin: String {
        let from: String
        switch p.origin.type {
        case "draft": from = L(R.string.skill_review_from_draft)
        case "disk": from = L(R.string.skill_review_from_disk)
        default: from = L(R.string.skill_review_from_kelo)
        }
        let reason = p.reason.trimmingCharacters(in: .whitespacesAndNewlines)
        return !reason.isEmpty && p.origin.type != "disk" ? "\(from) · \(reason)" : from
    }

    /// The text as it will be saved, or the lines a change adds and removes (with their counts).
    private var content: some View {
        VStack(alignment: .leading, spacing: 1) {
            if SkillRules.showsDiff(p) {
                let n = SkillRules.counts(p)
                Text(L(R.string.skill_review_diff_counts, n.added, n.removed)).font(KeloFont.label).foregroundStyle(c.muted)
                    .padding(.bottom, 4).fixedSize(horizontal: false, vertical: true)
                ForEach(Array(SkillRules.preview(p.diff, all: all).enumerated()), id: \.offset) { _, l in SkillDiffRow(line: l) }
            } else {
                Text(SkillRules.preview(SkillRules.textLines(p.text), all: all).joined(separator: "\n"))
                    .font(.footnote).foregroundStyle(c.ink)
                    .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(c.line, lineWidth: 1))
    }

    @ViewBuilder private var answerRow: some View {
        if let choice = decided {
            HStack(spacing: 8) {
                KeloIconView(choice ? .check : .minus, size: 18, tint: choice ? c.accent : c.muted)
                Text(L(choice ? R.string.skill_review_approved : R.string.skill_review_discarded))
                    .font(KeloFont.body).fontWeight(.medium).foregroundStyle(c.ink)
                    .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                if sending { ProgressView().controlSize(.small).tint(c.muted) }
            }
            .padding(.vertical, 6)
        } else {
            HStack(spacing: 8) {
                SecondaryButton(L(edited && !p.neverApproved ? R.string.skill_review_put_back : R.string.skill_review_discard),
                                enabled: !sending, fill: true) { send(false) }
                PrimaryButton(L(R.string.skill_review_approve), enabled: !sending) { send(true) }
            }
        }
    }

    private func send(_ approve: Bool) {
        guard !sending else { return }
        if approve { Haptics.success() } else { Haptics.tick() }
        sending = true
        decided = approve
        onAnswer(p, approve) { ok in
            sending = false
            if !ok { decided = nil }
        }
    }

    private func note(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            KeloIconView(.info, size: 16, tint: c.infoInk).padding(.top, 1)
            Text(text).font(KeloFont.small).foregroundStyle(c.infoInk)
                .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 8).fill(c.infoSoft))
        .accessibilityElement(children: .combine)
    }
}

/// One line of a change: "+" on a soft green band, "−" on a soft red band, a gap as "⋯", a file name in bold.
struct SkillDiffRow: View {
    let line: SkillDiffLine
    @Environment(\.kelo) private var c

    var body: some View {
        switch line.op {
        case "gap":
            Text("⋯").font(KeloFont.small).foregroundStyle(c.muted).padding(.vertical, 2).accessibilityHidden(true)
        case "file":
            Text(line.text).font(KeloFont.caption).fontWeight(.bold).foregroundStyle(c.ink).padding(.top, 4).padding(.bottom, 2)
                .fixedSize(horizontal: false, vertical: true)
        default:
            let add = line.op == "add", del = line.op == "del"
            let tint = add ? c.successInk : del ? c.dangerInk : c.muted
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                Text(add ? "+" : del ? "\u{2212}" : " ").font(.system(.footnote, design: .monospaced)).foregroundStyle(tint)
                    .frame(width: 14, alignment: .leading)
                Text(line.text).font(.footnote).foregroundStyle(tint)
                    .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 4).padding(.vertical, 1)
            .background(RoundedRectangle(cornerRadius: 4).fill(add ? c.successSoft : del ? c.dangerSoft : Color.clear))
            .accessibilityElement(children: .combine)
        }
    }
}

/// "Save as skill" under a finished chat task (`taskId`) or background job (`workerId`): Kelo drafts a skill from the run and the
/// draft waits for approval on 마이 → 기록. Shows "Kelo가 스킬 초안을 쓰는 중…" (disabled) while that run's draft is being written.
/// `onMessage` shows a short message (the page's toast).
public struct SaveAsSkillSection: View {
    @State private var model: SaveAsSkillModel

    public init(api: @escaping () -> KeloApi?, taskId: Int64? = nil, workerId: String? = nil, onMessage: @escaping (String) -> Void) {
        _model = State(initialValue: SaveAsSkillModel(taskId: taskId, workerId: workerId, api: api, say: onMessage))
    }

    public var body: some View {
        SaveAsSkillButton(drafting: model.drafting) { model.save() }
            .task(id: model.tick) { await model.follow() }
    }
}

/// The button and the line under it.
public struct SaveAsSkillButton: View {
    let drafting: Bool
    let onClick: () -> Void
    @Environment(\.kelo) private var c

    public init(drafting: Bool, onClick: @escaping () -> Void) { self.drafting = drafting; self.onClick = onClick }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            SecondaryButton(L(drafting ? R.string.skill_review_drafting : R.string.skill_save), enabled: !drafting, fill: true,
                            action: onClick)
                .accessibilityIdentifier("kelo.skill.save")
            Text(L(R.string.skill_save_about, L(R.string.tab_my), L(R.string.my_records_title))).font(KeloFont.small).foregroundStyle(c.muted)
                .padding(.horizontal, 4).frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
        }
    }
}
