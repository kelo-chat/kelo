import Foundation

// Shipping addresses and the address forms (SHIPPING API in server.py). The form of each country comes from the server
// (GET /api/address/form/{CC}): the app has no per-country code, it renders the fields it is given, in their order, and checks
// only what the form says (required, the postal code's pattern). The server checks everything again.

/// What is wrong with one row of an address form (keys: the field keys and "phone").
public enum AddressProblem: Equatable, Sendable {
    /// Empty, or a picked value that is not one of the choices.
    case required
    /// Not a postal code of the country; `example` is the form's example ("" when it has none).
    case postal(example: String)
    /// Characters a phone number cannot have.
    case phone
}

/// An address being typed: the country, the form's values by field key (a picked field holds its option's key), the phone as typed
/// (the national number, or "+..." as given) and the delivery note.
public struct AddressDraft: Equatable, Sendable {
    public var country: String
    public var values: [String: String]
    public var phone: String
    public var note: String

    public init(country: String, values: [String: String] = [:], phone: String = "", note: String = "") {
        self.country = country.uppercased(); self.values = values; self.phone = phone; self.note = note
    }

    public subscript(key: String) -> String {
        get { values[key] ?? "" }
        set { values[key] = newValue }
    }

    /// A saved profile to edit; the phone is shown without the calling code once the form is known (`AddressRules.phoneShown`).
    public static func of(_ p: ShippingProfile) -> AddressDraft {
        var values: [String: String] = [:]
        for k in AddressRules.fieldKeys { let v = p.value(k); if !v.isEmpty { values[k] = v } }
        return AddressDraft(country: p.country, values: values, phone: p.phone, note: p.note)
    }

    /// The in-chat form's start: `prefill` holds field keys, "phone" and "note" (any of them may be missing).
    public static func prefilled(country: String, _ prefill: [String: String]) -> AddressDraft {
        var values: [String: String] = [:]
        for k in AddressRules.fieldKeys { if let v = prefill[k], !v.isEmpty { values[k] = v } }
        return AddressDraft(country: country, values: values, phone: prefill["phone"] ?? "", note: prefill["note"] ?? "")
    }
}

extension ShippingProfile {
    /// The value of one of the address's field keys ("" for an unknown key).
    public func value(_ key: String) -> String {
        switch key {
        case "name": name
        case "organization": organization
        case "address_line1": addressLine1
        case "address_line2": addressLine2
        case "dependent_locality": dependentLocality
        case "locality": locality
        case "admin_area": adminArea
        case "postal_code": postalCode
        case "sorting_code": sortingCode
        default: ""
        }
    }
}

public enum AddressRules {
    /// Every field key an address can have (address.FIELDS on the server).
    public static let fieldKeys = ["name", "organization", "address_line1", "address_line2", "dependent_locality", "locality", "admin_area",
                                   "postal_code", "sorting_code"]
    public static let phoneKey = "phone"
    /// Longest nickname and note the server takes (shipping.py MAX_LABEL, MAX_NOTE).
    public static let maxLabel = 40, maxNote = 200, maxField = 200

    /// The row's label: a field that is not required says so ("Address line 2 (optional)").
    public static func label(_ text: String, required: Bool, optional: String) -> String {
        let o = optional.trimmingCharacters(in: .whitespaces)
        return required || o.isEmpty ? text : "\(text) (\(o))"
    }

    public static func label(_ f: AddressField, optional: String) -> String { label(f.label, required: f.required, optional: optional) }

    /// Whether `value` fits the field's postal pattern (case-insensitive, the whole value). No pattern, or nothing typed: fine.
    public static func postalOK(_ f: AddressField, _ value: String) -> Bool {
        let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let p = f.pattern, !p.isEmpty, !v.isEmpty else { return true }
        guard let re = try? NSRegularExpression(pattern: p, options: [.caseInsensitive]) else { return true }
        let ns = v as NSString
        guard let m = re.firstMatch(in: v, options: [.anchored], range: NSRange(location: 0, length: ns.length)) else { return false }
        return m.range.location == 0 && m.range.length == ns.length
    }

    /// The option key for a value that is a key or a name of one of the field's options (any case); nil when it is neither.
    public static func optionKey(_ f: AddressField, _ value: String) -> String? {
        let v = value.trimmingCharacters(in: .whitespaces)
        guard !v.isEmpty else { return nil }
        if let o = f.options.first(where: { $0.key == v }) { return o.key }
        let low = v.lowercased()
        return f.options.first { $0.key.lowercased() == low || $0.name.lowercased() == low }?.key
    }

    /// The shown name of a picked value ("CA" -> "California"); nil when nothing valid is picked.
    public static func optionName(_ f: AddressField, _ value: String) -> String? {
        guard let k = optionKey(f, value) else { return nil }
        let o = f.options.first { $0.key == k }
        return (o?.name.isEmpty ?? true) ? k : o?.name
    }

    /// Only digits, spaces, dashes, brackets, dots, slashes and a leading + (the server's rule).
    public static func phoneOK(_ phone: String) -> Bool {
        let t = phone.trimmingCharacters(in: .whitespaces)
        for (i, ch) in t.enumerated() {
            if ch == "+" { if i != 0 { return false }; continue }
            if ch.isASCII && (ch.isNumber || " -()./".contains(ch)) { continue }
            return false
        }
        return true
    }

    /// What is wrong, by row key; empty when the address can be sent.
    public static func problems(_ form: AddressForm, _ d: AddressDraft) -> [String: AddressProblem] {
        var out: [String: AddressProblem] = [:]
        for f in form.fields {
            let v = d[f.key].trimmingCharacters(in: .whitespacesAndNewlines)
            if !f.options.isEmpty {
                if optionKey(f, v) == nil && (f.required || !v.isEmpty) { out[f.key] = .required }
            } else if v.isEmpty {
                if f.required { out[f.key] = .required }
            } else if f.key == "postal_code" && !postalOK(f, v) {
                out[f.key] = .postal(example: f.example ?? "")
            }
        }
        let phone = d.phone.trimmingCharacters(in: .whitespaces)
        if phone.isEmpty { if form.phone.required { out[phoneKey] = .required } }
        else if !phoneOK(phone) || phone.filter(\.isNumber).count < 4 { out[phoneKey] = .phone }
        return out
    }

    /// Changing the country: the values of keys both forms have stay; a picked value stays only when the new list has it.
    public static func carry(_ values: [String: String], to form: AddressForm) -> [String: String] {
        var out: [String: String] = [:]
        for f in form.fields {
            guard let v = values[f.key], !v.isEmpty else { continue }
            if f.options.isEmpty { out[f.key] = v } else if let k = optionKey(f, v) { out[f.key] = k }
        }
        return out
    }

    /// A value given as an option's name (prefill "California") becomes its key, so the picker shows it picked.
    public static func normalized(_ values: [String: String], for form: AddressForm) -> [String: String] {
        var out = values
        for f in form.fields where !f.options.isEmpty {
            if let v = values[f.key], let k = optionKey(f, v) { out[f.key] = k }
        }
        return out
    }

    /// The phone as the field shows it next to the calling code: an E.164 number of that code without it ("+16502530000" with "+1"
    /// -> "6502530000"); anything else as it is.
    public static func phoneShown(_ phone: String, callingCode: String) -> String {
        let p = phone.trimmingCharacters(in: .whitespaces)
        let code = callingCode.trimmingCharacters(in: .whitespaces)
        guard p.hasPrefix("+"), code.hasPrefix("+"), code.count > 1 else { return p }
        let digits = String(p.dropFirst()).filter(\.isNumber)
        let codeDigits = String(code.dropFirst())
        guard p.dropFirst().allSatisfy(\.isNumber), digits.hasPrefix(codeDigits), digits.count > codeDigits.count else { return p }
        return String(digits.dropFirst(codeDigits.count))
    }

    /// The address as the server takes it: {"country", the form's field keys, "phone", "note"}. Keys the form does not have are sent
    /// empty when the country is new or changed (`savedCountry`: the profile's country before editing; nil for a new address), so a
    /// region or a city of the old country never stays behind; for the same country they are left as they are.
    public static func body(_ form: AddressForm, _ d: AddressDraft, savedCountry: String? = nil) -> [String: JSON] {
        var out: [String: JSON] = ["country": .string((form.country.isEmpty ? d.country : form.country).uppercased())]
        let shown = Set(form.fields.map(\.key))
        for f in form.fields {
            var v = d[f.key].trimmingCharacters(in: .whitespacesAndNewlines)
            if !f.options.isEmpty { v = optionKey(f, v) ?? v }
            out[f.key] = .string(v)
        }
        let changed = savedCountry.map { $0.uppercased() != (out["country"]?.stringValue ?? "") } ?? true
        if changed {
            for k in fieldKeys where !shown.contains(k) { out[k] = .string("") }
        }
        out[phoneKey] = .string(d.phone.trimmingCharacters(in: .whitespaces))
        out["note"] = .string(String(d.note.trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxNote)))
        return out
    }

    /// The countries as the picker lists them: each named in `locale`'s language (the server's English name when the system has
    /// none), sorted by that name the way `locale` sorts.
    public static func shown(_ list: [AddressCountry], locale: Locale) -> [AddressCountry] {
        var seen = Set<String>()
        let named = list.compactMap { c -> AddressCountry? in
            guard !c.code.isEmpty, seen.insert(c.code).inserted else { return nil }
            return AddressCountry(code: c.code, name: countryName(c.code, english: c.name, locale: locale))
        }
        return named.sorted { $0.name.compare($1.name, options: [.caseInsensitive, .diacriticInsensitive], range: nil, locale: locale) == .orderedAscending }
    }

    /// One country's name in `locale`'s language; the English name (or the code) when the system does not know it.
    public static func countryName(_ code: String, english: String = "", locale: Locale) -> String {
        let unknown = locale.localizedString(forRegionCode: "ZZ")   // "Unknown Region": the system does not know the code
        if let n = locale.localizedString(forRegionCode: code), !n.isEmpty, n != code, code == "ZZ" || n != unknown { return n }
        return english.isEmpty ? code : english
    }

    /// An address's text on one line ("서울특별시 강남구 테헤란로 152, 10층, 06236").
    public static func oneLine(_ formatted: String) -> String {
        formatted.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            .joined(separator: ", ")
    }

    /// The nickname a new address starts with: the given one, else the app's word for home.
    public static func startLabel(_ given: String, fallback: String) -> String {
        let g = given.trimmingCharacters(in: .whitespaces)
        return g.isEmpty ? fallback : String(g.prefix(maxLabel))
    }
}

/// The in-chat address form's answer (POST /api/shipping-forms/{form_id}).
public enum ShippingFormRules {
    public static let payCard = "card", paySite = "site"

    /// {"address": {...}, "save": bool, "label": nickname (only when saved), "payment": "card" | "site" | null}.
    public static func body(address: [String: JSON], save: Bool, label: String, payment: String?) -> JSON {
        var out: [String: JSON] = ["address": .object(address), "save": .bool(save), "payment": payment.map { .string($0) } ?? .null]
        let l = label.trimmingCharacters(in: .whitespaces)
        if save { out["label"] = .string(String(l.prefix(AddressRules.maxLabel))) }
        return .object(out)
    }

    /// Whether a form can still be answered at `now` (unix seconds): not past `expiresAt`.
    public static func open(expiresAt: Double?, now: Double) -> Bool {
        guard let e = expiresAt, e > 0 else { return true }
        return now < e
    }

    /// The folded card's text after a 200: the nickname (when saved) and the address on one line.
    public static func doneLine(label: String?, oneLine: String) -> String {
        let l = (label ?? "").trimmingCharacters(in: .whitespaces)
        if l.isEmpty { return oneLine }
        return oneLine.isEmpty ? l : "\(l) · \(oneLine)"
    }
}

/// The order card's lines (confirm_purchase).
public enum OrderRules {
    /// "Pay with": the card's nickname and its last four ("생활비 카드 ••4242"); the brand when it has no nickname.
    public static func payWith(_ card: CardBrief) -> String {
        let d = CardMask.last4(card.last4)
        let b = CardBrand.of(card.brand)
        let name = card.label.trimmingCharacters(in: .whitespaces).isEmpty ? (b.displayName ?? b.shortName) : card.label
        return d.isEmpty ? name : "\(name) \(CardMask.dots)\(d)"
    }

    /// "Ship to" first line: nickname · name · phone, the parts that are known.
    public static func shipHead(_ s: OrderShipping) -> String {
        [s.label, s.name, s.phone].map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// "× 2" only for more than one.
    public static func quantity(_ item: OrderItem) -> String? { item.quantity > 1 ? "× \(item.quantity)" : nil }
}
