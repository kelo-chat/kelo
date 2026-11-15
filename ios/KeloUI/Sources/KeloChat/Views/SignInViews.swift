import SwiftUI
import KeloCore
import KeloShared

/// 신고하기: tell the Kelo developer about a reply that was offensive, harmful, wrong or exposed someone's private details. The
/// sheet says exactly what will be sent before anything is (Android ReportSheet.kt).
struct ReportSheet: View {
    let reply: String
    let asked: String?
    let draft: ReportDraft
    let onDraft: (ReportDraft) -> Void
    let onSent: () -> Void
    let onClose: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var d: ReportDraft?
    @State private var model: ReportModel?

    var body: some View {
        let cur = d ?? draft
        let busy = model?.busy ?? false
        let err = model?.err ?? ""
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.report_title)) { onDraft(cur); onClose() }
                MutedText(L(R.string.report_intro), small: true)
                Text(L(R.string.report_reason_label)).font(KeloFont.button).foregroundStyle(c.ink)
                VStack(spacing: 0) {
                    ForEach(ReportReason.allCases, id: \.self) { r in
                        RadioRow(reasonText(r), selected: cur.reason == r) { var x = cur; x.reason = r; d = x }
                    }
                }
                KeloTextField(L(R.string.report_comment_hint), text: Binding(get: { cur.comment }, set: { var x = cur; x.comment = $0; d = x }),
                              axis: .vertical, maxLength: ReportRules.maxComment)
                if asked != nil {
                    Toggle(isOn: Binding(get: { cur.includeAsked }, set: { var x = cur; x.includeAsked = $0; d = x })) {
                        Text(L(R.string.report_include_asked)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    .tint(c.accent)
                }
                // exactly what leaves the phone, following the switch above
                VStack(alignment: .leading, spacing: 4) {
                    Text(L(R.string.report_sent_title)).font(.system(.footnote, weight: .semibold)).foregroundStyle(c.ink)
                    ForEach(sentLines(cur), id: \.self) { line in
                        Text(L(R.string.report_bullet, line)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    Text(L(R.string.report_sent_not)).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
                if !err.isEmpty { ErrorText(err, small: true) }
                PrimaryButton(L(busy ? R.string.report_sending : R.string.report_send), enabled: !busy && ReportRules.canSend(cur)) { send(cur) }
            }
            .padding(.horizontal, 20).padding(.bottom, 16)
        }
        .interactiveDismissDisabled(busy)
        .onDisappear { if !busy { onDraft(d ?? draft) } }
    }

    private func reasonText(_ r: ReportReason) -> String {
        switch r {
        case .harmful: L(R.string.report_reason_harmful)
        case .inaccurate: L(R.string.report_reason_inaccurate)
        case .privacy: L(R.string.report_reason_privacy)
        case .other: L(R.string.report_reason_other)
        }
    }

    private func sentLines(_ cur: ReportDraft) -> [String] {
        var l = [L(R.string.report_sent_reply)]
        if asked != nil && cur.includeAsked { l.append(L(R.string.report_sent_asked)) }
        l += [L(R.string.report_sent_reason), L(R.string.report_sent_model), L(R.string.report_sent_version)]
        return l
    }

    private func send(_ cur: ReportDraft) {
        if model == nil, let api = app.api { model = ReportModel(api: api) }
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        model?.send(cur, reply: reply, asked: asked, version: version, sent: onSent, failed: onDraft)   // kept: the next try starts from here
    }
}
