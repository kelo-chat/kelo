import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 공지 (Android my/NoticesArea.kt): one compact card per unread notice, above 계정과 기기. Nothing at all when there are none.
struct NoticesArea: View {
    let model: NoticesModel

    var body: some View {
        if !model.notices.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                SectionLabel(L(R.string.notices_title))
                VStack(spacing: 8) {
                    ForEach(model.notices) { n in
                        NoticeCard(notice: n) { model.open(n) }
                    }
                }
                .padding(.horizontal, 8)
            }
        }
    }
}

/// A notice in the list: its title and the first two lines of its body. An important one has an accent border and label.
private struct NoticeCard: View {
    let notice: OperatorNotice
    let onTap: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let important = NoticesRules.isImportant(notice)
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 4) {
                if important {
                    Text(L(R.string.notices_important)).font(KeloFont.small).foregroundStyle(c.accent)
                }
                Text(TokenText.fit(notice.title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                    .lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                if !notice.body.isEmpty {
                    Text(notice.body).font(KeloFont.body).foregroundStyle(c.muted)
                        .lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(important ? c.accent : c.line, lineWidth: important ? 2 : 1))
            .contentShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }
}

/// The whole notice in a sheet: its title, date and body, and the link as a button when it is a kelo.chat address.
struct NoticeSheet: View {
    let notice: OperatorNotice
    let onClose: () -> Void
    @Environment(\.kelo) private var c
    @Environment(\.openURL) private var openURL

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if NoticesRules.isImportant(notice) {
                    Text(L(R.string.notices_important)).font(KeloFont.small).foregroundStyle(c.accent)
                }
                Text(TokenText.fit(notice.title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if !notice.date.isEmpty { MutedText(notice.date, small: true) }
                Text(notice.body).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if let link = NoticesRules.safeLink(notice.link) {
                    PrimaryButton(L(R.string.notices_more)) { openURL(link) }
                }
                SecondaryButton(L(R.string.notices_close), fill: true, action: onClose)
            }
            .padding(20)
        }
    }
}
