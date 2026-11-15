import SwiftUI
import KeloCore
import KeloShared

/// "오전 10:00 – 오후 10:00", or "오전 10:00 – 다음 날 오전 1:00" for a window past midnight (Android hoursSpanText).
@MainActor func hoursSpanText(_ start: String, _ end: String) -> String {
    let a = ContactHoursRules.minutes(start).map { MyFormat.clock($0 / 60, $0 % 60) } ?? start
    let b = ContactHoursRules.minutes(end).map { MyFormat.clock($0 / 60, $0 % 60) } ?? end
    return L(ContactHoursRules.pastMidnight(start, end) ? R.string.hours_span_next_day : R.string.hours_span, a, b)
}

/// The review row for learned message hours (마이 → 기록, and each day type on 마이 → 알림): what the owner's use shows, what is set
/// now, and "지금대로 두기" / "이 시간으로 바꾸기". `done`: the choice just made (true = changed), shown for a moment
/// (Android my/HoursSuggestionUi.kt).
struct HoursSuggestionCard: View {
    let suggestion: HoursSuggestion
    let done: Bool?
    let onAnswer: (Bool) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let s = suggestion
        let learned = hoursSpanText(s.wakeStart, s.wakeEnd)
        let weekend = s.dayType == ContactHoursRules.weekend
        VStack(alignment: .leading, spacing: 4) {
            Text(L(weekend ? R.string.hours_suggest_heading_weekend : R.string.hours_suggest_heading)).font(KeloFont.titleSmall)
                .foregroundStyle(c.accent).padding(.leading, 4).padding(.top, 6)
            VStack(alignment: .leading, spacing: 6) {
                Text(L(weekend ? R.string.hours_suggest_title_weekend : R.string.hours_suggest_title, learned)).font(KeloFont.bodyLarge.weight(.medium))
                    .foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                Text(P(R.plurals.hours_suggest_basis, s.activeDays, s.activeDays, hoursSpanText(s.currentStart, s.currentEnd)))
                    .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                if let done {
                    HStack(spacing: 8) {
                        KeloIconView(done ? .check : .minus, size: 18, tint: done ? c.accent : c.muted)
                        Text(done ? L(R.string.hours_suggest_accepted, learned) : L(R.string.hours_suggest_kept))
                            .font(KeloFont.body.weight(.medium)).foregroundStyle(c.ink).frame(maxWidth: .infinity, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    HStack(spacing: 8) {
                        SecondaryButton(L(R.string.hours_suggest_keep), fill: true) { Haptics.warning(); onAnswer(false) }
                        PrimaryButton(L(R.string.hours_suggest_accept)) { Haptics.success(); onAnswer(true) }
                    }
                }
            }
            .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.accent.opacity(0.08)))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.accent.opacity(0.45), lineWidth: 1))
        }
        .accessibilityIdentifier("kelo.hours.suggestion")
    }
}
