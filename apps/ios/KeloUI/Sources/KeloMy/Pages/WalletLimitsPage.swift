import SwiftUI
import KeloCore
import KeloShared

/// 자동결제 한도: the global pause, and per currency the limit for one payment and for a month with what was paid automatically this
/// month. Each can go back to the default; another currency can be added.
struct WalletLimitsPage: View {
    let paused: Bool
    let onBack: () -> Void
    @Environment(AppModel.self) private var app
    @State private var model: WalletLimitsModel
    @State private var added: Set<String> = []
    @State private var editing: String?
    @State private var adding = false

    init(api: KeloApi, wallet: WalletModel, paused: Bool, onBack: @escaping () -> Void) {
        self.paused = paused; self.onBack = onBack
        _model = State(initialValue: WalletLimitsModel(api: api, wallet: wallet))
    }

    private var isPaused: Bool? { model.isPaused }

    var body: some View {
        let homes = LimitRules.homeCurrencies(app: app.strings.locale, system: Locale.current)
        SubPage(L(R.string.wallet_limits_title), onBack: onBack) {
            MyCard {
                ToggleRow(L(R.string.wallet_pause_title), isOn: Binding(get: { isPaused ?? paused }, set: { model.setPaused($0) }))
                MutedText(L((isPaused ?? paused) ? R.string.wallet_pause_on_body : R.string.wallet_pause_off_body), small: true)
            }
            MutedText(L(R.string.wallet_limits_help), small: true)
            if let d = model.data {
                if !d.month.isEmpty { MutedText(L(R.string.wallet_limits_month, d.month, d.timezone), small: true) }
                let shown = LimitRules.visible(d.limits, homes: homes, added: added.union(editing.map { [$0] } ?? []))
                ForEach(shown, id: \.currency) { row in
                    WalletLimitCard(row: row, editing: editing == row.currency,
                                    onEdit: { editing = editing == row.currency ? nil : row.currency },
                                    onSave: { per, mon in model.save(row.currency, per, mon) { editing = nil; added.insert(row.currency) } },
                                    onReset: { model.reset(row.currency) { editing = nil; added.remove(row.currency) } })
                }
                // a currency being added that the server does not know yet
                if let cur = editing, !d.limits.contains(where: { $0.currency == cur }) {
                    WalletLimitCard(row: WalletLimit(currency: cur), editing: true, onEdit: { editing = nil },
                                    onSave: { per, mon in model.save(cur, per, mon) { editing = nil; added.insert(cur) } }, onReset: { editing = nil })
                }
                if !model.message.isEmpty { ErrorText(model.message, small: true) }
                MenuGroup { MenuRow(.plus, L(R.string.wallet_limits_add)) { adding = true } }
                    .keloSheet(isPresented: $adding, detents: [.medium, .large]) {
                        WalletCurrencySheet(options: LimitRules.addable(d.limits, shown: shown), onCancel: { adding = false }) { cur in
                            adding = false
                            added.insert(cur)
                            editing = cur
                        }
                    }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
    }
}

struct WalletLimitCard: View {
    let row: WalletLimit
    let editing: Bool
    let onEdit: () -> Void
    let onSave: (Double, Double) -> Void
    let onReset: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        MyCard(action: editing ? nil : onEdit) {
            HStack(spacing: 8) {
                Text(row.currency).font(KeloFont.titleSmall).foregroundStyle(c.ink).frame(maxWidth: .infinity, alignment: .leading)
                WalletTag(text: L(row.added ? R.string.wallet_limit_added : (row.custom ? R.string.wallet_limit_custom : R.string.wallet_limit_default)),
                          tone: row.custom || row.added ? .accent : .plain)
            }
            if row.monthly <= 0 && row.perPayment <= 0 && (row.custom || row.added) {
                MutedText(L(R.string.wallet_limit_never), small: true)
            } else {
                Text(L(R.string.wallet_limit_line, walletMoney(row.perPayment, row.currency), walletMoney(row.monthly, row.currency)))
                    .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                KeloBar(LimitRules.used(row.spentThisMonth, row.monthly), hot: row.spentThisMonth >= row.monthly && row.monthly > 0)
                MutedText(L(R.string.wallet_limit_spent, walletMoney(row.spentThisMonth, row.currency)), small: true)
            }
            if editing { WalletLimitEditor(row: row, onSave: onSave, onReset: onReset, onCancel: onEdit) }
        }
    }
}

struct WalletLimitEditor: View {
    let row: WalletLimit
    let onSave: (Double, Double) -> Void
    let onReset: () -> Void
    let onCancel: () -> Void
    @State private var per = ""
    @State private var mon = ""

    var body: some View {
        let perV = Money.parse(per, row.currency)
        let monV = Money.parse(mon, row.currency)
        let check = LimitRules.check(perV, monV)
        VStack(alignment: .leading, spacing: 10) {
            WalletField(label: L(R.string.wallet_limit_per, row.currency), placeholder: "", text: $per, keyboard: .number, maxLength: 16,
                        error: !per.isEmpty && perV == nil ? L(R.string.wallet_limit_bad_number) : nil)
            WalletField(label: L(R.string.wallet_limit_month, row.currency), placeholder: "", text: $mon, keyboard: .number, maxLength: 16,
                        error: !mon.isEmpty && monV == nil ? L(R.string.wallet_limit_bad_number)
                            : (check == .perAboveMonthly ? L(R.string.wallet_limit_per_above) : nil))
            MutedText(L(R.string.wallet_limit_zero_note), small: true)
            if let dp = row.defaultPerPayment {
                MutedText(L(R.string.wallet_limit_default_line, walletMoney(dp, row.currency), walletMoney(row.defaultMonthly ?? 0, row.currency)), small: true)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { buttons(perV, monV, check) }
                VStack(spacing: 8) { buttons(perV, monV, check) }
            }
            if row.custom {
                LinkButton(L(row.added ? R.string.wallet_limit_remove : R.string.wallet_limit_reset), danger: row.added, action: onReset)
            }
        }
        .onAppear {
            let known = row.custom || row.defaultPerPayment != nil
            per = known ? Money.plain(row.perPayment, row.currency) : ""
            mon = known ? Money.plain(row.monthly, row.currency) : ""
        }
    }

    @ViewBuilder private func buttons(_ perV: Double?, _ monV: Double?, _ check: LimitCheck) -> some View {
        SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
        PrimaryButton(L(R.string.save), enabled: check == .ok) { if let perV, let monV { onSave(perV, monV) } }
    }
}

/// Another currency for the limits: a 3-letter code typed or one of the known ones.
struct WalletCurrencySheet: View {
    let options: [String]
    let onCancel: () -> Void
    let onPick: (String) -> Void
    @State private var code = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(R.string.wallet_limits_add), onClose: onCancel)
                KeloTextField(L(R.string.wallet_currency_hint), text: $code, keyboard: .ascii, maxLength: 3)
                WalletWrap {
                    ForEach(Array(options.prefix(18)), id: \.self) { o in ChoiceChip(o, selected: o == code) { code = o } }
                }
                PrimaryButton(L(R.string.wallet_currency_add), enabled: Money.validCode(code)) { onPick(code) }
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .onChange(of: code) { _, v in
            let f = String(v.uppercased().filter { $0 >= "A" && $0 <= "Z" }.prefix(3))
            if f != v { code = f }
        }
    }
}
