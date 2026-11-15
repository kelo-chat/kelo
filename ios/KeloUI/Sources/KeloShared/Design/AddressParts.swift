import SwiftUI
import Observation
import KeloCore

// The address form both the wallet's shipping pages and the chat's address card use. Everything about a country comes from the
// server (GET /api/address/form/{CC}): the rows, their order, which are required, the lists to pick from and the postal pattern.

/// One address being entered: the countries, the current country's form and what was typed (memory only). The forms are kept for
/// the app's run per country and language, so switching back and forth does not ask again.
@MainActor @Observable
public final class AddressEditor {
    /// Every country, named in the app's language and sorted by that name.
    public private(set) var countries: [AddressCountry] = []
    public private(set) var form: AddressForm?
    public var draft: AddressDraft
    public private(set) var loading = false
    /// Loading the form did not work (the message to show), with a way to try again.
    public private(set) var failed: String?
    /// Problems show only after the first try to send (not while the user is still typing for the first time).
    public var showProblems = false
    /// The server's 422 detail after a send (shown under the form).
    public var serverError: String?
    /// The country the address had when editing began (nil for a new one): `AddressRules.body` clears rows of another country.
    public let savedCountry: String?

    @ObservationIgnored private var phoneShownFor = ""
    @ObservationIgnored private var started = false

    private static var keptCountries: [String: AddressCountries] = [:]
    private static var keptForms: [String: AddressForm] = [:]

    public init(draft: AddressDraft, savedCountry: String? = nil) {
        self.draft = draft
        self.savedCountry = savedCountry
    }

    public var problems: [String: AddressProblem] { form.map { AddressRules.problems($0, draft) } ?? [:] }
    public var shownProblems: [String: AddressProblem] { showProblems ? problems : [:] }

    private static var lang: String { AcceptLanguageValue.current }

    /// The countries (once) and the form of the draft's country; an empty country starts at the server's default one.
    public func start(_ api: KeloApi?) async {
        guard let api else { return }
        if !started {
            started = true
            await loadCountries(api)
        }
        if form?.country != draft.country || form == nil { await loadForm(api, country: draft.country) }
    }

    private func loadCountries(_ api: KeloApi) async {
        let key = Self.lang
        var list = Self.keptCountries[key]
        if list == nil, let fetched = try? await api.addressCountries() {
            Self.keptCountries[key] = fetched
            list = fetched
        }
        if let list {
            countries = AddressRules.shown(list.countries, locale: Strings.shared.locale)
            if draft.country.isEmpty { draft.country = list.defaultCountry.isEmpty ? (Locale.current.region?.identifier ?? "US") : list.defaultCountry }
        } else if draft.country.isEmpty {
            draft.country = Locale.current.region?.identifier ?? "US"
        }
    }

    /// Picks another country: its form loads; values of rows both forms have stay.
    public func setCountry(_ code: String, api: KeloApi?) async {
        let cc = code.uppercased()
        guard cc != draft.country || form == nil else { return }
        draft.country = cc
        serverError = nil
        guard let api else { return }
        await loadForm(api, country: cc)
    }

    public func retry(_ api: KeloApi?) async {
        guard let api else { return }
        if countries.isEmpty { await loadCountries(api) }
        await loadForm(api, country: draft.country)
    }

    private func loadForm(_ api: KeloApi, country: String) async {
        guard !country.isEmpty else { return }
        let key = "\(country)|\(Self.lang)"
        failed = nil
        if let kept = Self.keptForms[key] { apply(kept); return }
        loading = true
        defer { loading = false }
        do {
            let f = try await api.addressForm(country)
            Self.keptForms[key] = f
            guard draft.country == country else { return }   // another country was picked meanwhile
            apply(f)
        } catch is CancellationError {
        } catch {
            failed = L(R.string.shipping_form_failed)
        }
    }

    private func apply(_ f: AddressForm) {
        let previous = form
        form = f
        draft.values = previous == nil ? AddressRules.normalized(draft.values, for: f) : AddressRules.carry(draft.values, to: f)
        // a saved E.164 number shows without the calling code next to the code's chip (once, before the user changes it)
        if phoneShownFor.isEmpty {
            phoneShownFor = f.country
            draft.phone = AddressRules.phoneShown(draft.phone, callingCode: f.phone.callingCode)
        }
    }

    /// Marks the problems for display; true when the address can be sent.
    public func check() -> Bool {
        showProblems = true
        return form != nil && problems.isEmpty
    }

    /// The address as the server takes it (nil until the form is there).
    public func body() -> [String: JSON]? {
        guard let f = form else { return nil }
        return AddressRules.body(f, draft, savedCountry: savedCountry)
    }

    /// The country's name in the app's language.
    public func countryName(_ code: String) -> String {
        countries.first { $0.code == code }?.name ?? AddressRules.countryName(code, locale: Strings.shared.locale)
    }

    /// Forget the kept forms (the app's language changed).
    public static func forget() { keptCountries = [:]; keptForms = [:] }
}

/// The words a problem shows under its row.
@MainActor public func addressProblemText(_ p: AddressProblem, picked: Bool = false) -> String {
    switch p {
    case .required: L(R.string.shipping_err_required)
    case .postal(let example): example.isEmpty ? L(R.string.shipping_err_format) : L(R.string.shipping_err_pattern, example)
    case .phone: L(R.string.shipping_err_phone)
    }
}

/// The address rows: the country, the form's fields in their order (one per row, full width), the phone with its calling code and
/// the delivery note. While the form loads: placeholders; if it cannot load: why, and try again.
public struct AddressFields: View {
    @Bindable var editor: AddressEditor
    let api: KeloApi?
    /// The chat card draws on a card background: fields then sit on the page colour.
    var onCard = false
    @Environment(\.kelo) private var c
    @State private var picking: Picking?

    enum Picking: Identifiable {
        case country, option(String)
        var id: String { switch self { case .country: "country"; case .option(let k): "option:\(k)" } }
    }

    public init(editor: AddressEditor, api: KeloApi?, onCard: Bool = false) {
        self.editor = editor; self.api = api; self.onCard = onCard
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            let form = editor.form
            let problems = editor.shownProblems
            AddressRow(form?.labels.country.nonEmpty ?? L(R.string.shipping_country)) {
                PickButton(text: editor.countryName(editor.draft.country), placeholder: false, onCard: onCard) { picking = .country }
                    .accessibilityLabel([form?.labels.country ?? "", editor.countryName(editor.draft.country)].filter { !$0.isEmpty }.joined(separator: ", "))
                    .accessibilityIdentifier("kelo.address.country")
            }
            if let form {
                ForEach(form.fields, id: \.key) { f in field(f, form: form, problem: problems[f.key]) }
                AddressRow(AddressRules.label(form.phone.label, required: form.phone.required, optional: form.labels.optional),
                           error: problems[AddressRules.phoneKey].map { addressProblemText($0) }) {
                    HStack(spacing: 8) {
                        if !form.phone.callingCode.isEmpty {
                            Text(form.phone.callingCode).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                                .padding(.horizontal, 12).padding(.vertical, 13)
                                .background(RoundedRectangle(cornerRadius: 12).fill(onCard ? c.bg : c.bubble))
                                .fixedSize()
                                .environment(\.layoutDirection, .leftToRight)
                                .accessibilityHidden(true)
                        }
                        KeloTextField("", text: $editor.draft.phone, keyboard: .phone, maxLength: 30)
                            .keloAutofill("tel-national")
                            .environment(\.layoutDirection, .leftToRight)
                            .accessibilityLabel(form.phone.callingCode.isEmpty ? form.phone.label : form.phone.label + ", " + L(R.string.shipping_calling_code, form.phone.callingCode))
                            .accessibilityIdentifier("kelo.address.phone")
                    }
                }
                AddressRow(AddressRules.label(form.note.label, required: form.note.required, optional: form.labels.optional)) {
                    KeloTextField("", text: $editor.draft.note, axis: .vertical, maxLength: AddressRules.maxNote)
                        .accessibilityIdentifier("kelo.address.note")
                }
            } else if let failed = editor.failed {
                ErrorText(failed, small: true)
                SecondaryButton(L(R.string.retry), icon: .retry, fill: true) { Task { await editor.retry(api) } }
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .keloSheet(item: $picking, detents: [.large]) { p in
            switch p {
            case .country:
                KeloPickSheet(title: editor.form?.labels.country.nonEmpty ?? L(R.string.shipping_country),
                              items: editor.countries.map { ($0.code, $0.name) }, selected: editor.draft.country,
                              searchLabel: R.string.shipping_country_search, noMatch: R.string.shipping_country_none,
                              onPick: { code in picking = nil; Task { await editor.setCountry(code, api: api) } },
                              onClose: { picking = nil })
            case .option(let key):
                if let f = editor.form?.fields.first(where: { $0.key == key }) {
                    KeloPickSheet(title: f.label, items: f.options.map { ($0.key, $0.name.isEmpty ? $0.key : $0.name) },
                                  selected: AddressRules.optionKey(f, editor.draft[key]),
                                  onPick: { k in picking = nil; editor.draft[key] = k },
                                  onClose: { picking = nil })
                }
            }
        }
    }

    @ViewBuilder private func field(_ f: AddressField, form: AddressForm, problem: AddressProblem?) -> some View {
        let picked = !f.options.isEmpty
        AddressRow(AddressRules.label(f, optional: form.labels.optional), error: problem.map { addressProblemText($0, picked: picked) }) {
            if picked {
                let name = AddressRules.optionName(f, editor.draft[f.key])
                PickButton(text: name ?? L(R.string.shipping_pick), placeholder: name == nil, onCard: onCard) { picking = .option(f.key) }
                    .accessibilityLabel(f.label + ", " + (name ?? L(R.string.shipping_pick)))
                    .accessibilityIdentifier("kelo.address.field.\(f.key)")
            } else {
                KeloTextField(f.example.flatMap { $0.isEmpty ? nil : L(R.string.shipping_example, $0) } ?? "",
                              text: Binding(get: { editor.draft[f.key] }, set: { editor.draft[f.key] = $0 }),
                              keyboard: f.numeric ? .number : (f.key == "postal_code" ? .ascii : .text), maxLength: AddressRules.maxField)
                    .keloAutofill(f.autocomplete)
                    .accessibilityLabel(f.label)
                    .accessibilityIdentifier("kelo.address.field.\(f.key)")
            }
        }
    }
}

/// A labelled row of the address form; `error` shows under it in red.
public struct AddressRow<Content: View>: View {
    let label: String
    let error: String?
    let content: Content
    @Environment(\.kelo) private var c

    public init(_ label: String, error: String? = nil, @ViewBuilder content: () -> Content) {
        self.label = label; self.error = error; self.content = content()
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(KeloFont.small).foregroundStyle(error == nil ? c.muted : c.danger)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityHidden(true)
            content
            if let error { ErrorText(error, small: true) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A field that opens a list (country, state): looks like a text field, with a chevron.
struct PickButton: View {
    let text: String
    let placeholder: Bool
    var onCard = false
    let action: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Text(text).font(KeloFont.bodyLarge).foregroundStyle(placeholder ? c.muted : c.ink)
                    .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 13, weight: .semibold)).foregroundStyle(c.muted)
            }
            .padding(.horizontal, 14).padding(.vertical, 13)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// A list to pick one from (countries, states, prefectures), with a search box when it is long.
public struct KeloPickSheet: View {
    let title: String
    let items: [(String, String)]
    let selected: String?
    let onPick: (String) -> Void
    let onClose: () -> Void
    /// The search field's words and the line when nothing matches (a country list says "country"; other lists the plain words).
    var searchLabel: StringKey = R.string.select_search
    var noMatch: StringKey = R.string.select_no_match
    @State private var query = ""
    @Environment(\.kelo) private var c

    public init(title: String, items: [(String, String)], selected: String?, searchLabel: StringKey = R.string.select_search,
                noMatch: StringKey = R.string.select_no_match, onPick: @escaping (String) -> Void, onClose: @escaping () -> Void) {
        self.title = title; self.items = items; self.selected = selected; self.onPick = onPick; self.onClose = onClose
        self.searchLabel = searchLabel; self.noMatch = noMatch
    }

    public var body: some View {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        let shown = q.isEmpty ? items : items.filter { $0.1.lowercased().contains(q) || $0.0.lowercased() == q }
        VStack(alignment: .leading, spacing: 8) {
            SheetHeader(title, onClose: onClose).padding(.horizontal, 20)
            if items.count > 12 {
                KeloTextField(L(searchLabel), text: $query, maxLength: 60)
                    .padding(.horizontal, 20)
                    .accessibilityIdentifier("kelo.pick.search")
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 0) {
                        if shown.isEmpty { MutedText(L(noMatch), small: true).padding(.vertical, 16) }
                        ForEach(shown, id: \.0) { item in
                            RadioRow(item.1, selected: item.0 == selected) { Haptics.tick(); onPick(item.0) }
                                .accessibilityIdentifier("kelo.pick.\(item.0)")
                                .id(item.0)
                            Hairline()
                        }
                    }
                    .padding(.horizontal, 20).padding(.bottom, 24)
                }
                .scrollDismissesKeyboard(.interactively)
                .onAppear { if let selected, q.isEmpty { proxy.scrollTo(selected, anchor: .center) } }
            }
        }
        .padding(.top, 8)
    }
}

extension String {
    var nonEmpty: String? { trimmingCharacters(in: .whitespaces).isEmpty ? nil : self }
}

public extension View {
    /// The system's AutoFill hint for an address row (the server's HTML autocomplete token); nothing on macOS.
    func keloAutofill(_ token: String) -> some View {
        #if os(iOS)
        let type: UITextContentType? = switch token {
        case "name": .name
        case "organization": .organizationName
        case "address-line1": .streetAddressLine1
        case "address-line2": .streetAddressLine2
        case "address-level1": .addressState
        case "address-level2": .addressCity
        case "address-level3": .sublocality
        case "postal-code": .postalCode
        case "tel", "tel-national": .telephoneNumber
        default: nil
        }
        return textContentType(type)
        #else
        return self
        #endif
    }
}
