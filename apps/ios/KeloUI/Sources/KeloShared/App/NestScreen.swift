import Foundation
import KeloCore

// The screen a Nest surface sends (docs/nest-ui.md Part 1, "GET .../data?surface=..."; Android shell/NestScreenModel.kt), read into
// plain values the renderer lays out. Everything here is lenient: a field the app does not know is ignored, a part of an unknown
// type becomes one "update the app" row, and the limits of Part 2 are applied again (the server enforces them, the app never trusts
// that). Texts are plain text; nothing here reads HTML, Markdown or a link.

/// The limits the app keeps whatever the server sends.
public enum NestLimits {
    public static let maxComponents = 60     // in one screen, nested ones counted
    public static let maxCardDepth = 3
    public static let maxChildren = 20
    public static let maxRows = 100
    public static let maxPoints = 60
    public static let maxFields = 12
    public static let maxOptions = 30
    public static let maxText = 600
    public static let maxImage = 400
    public static let defaultImage = 160
}

/// A button's, form's, card's or row's action: what to POST to run it.
public struct NestAction: Equatable, Hashable, Sendable {
    public var id: String
    public var type = ""
    public var confirm = false
    public var label = ""
    /// The row's key and data id (a list row only): they say which row, the app never sends the row's values.
    public var item = ""
    public var data = ""
    /// Only for `open_url`.
    public var url = ""

    public init(id: String, type: String = "", confirm: Bool = false, label: String = "", item: String = "", data: String = "", url: String = "") {
        self.id = id; self.type = type; self.confirm = confirm; self.label = label; self.item = item; self.data = data; self.url = url
    }

    /// One request at a time per button: the key a running request is remembered by.
    public var key: String { "\(id)/\(item)" }
    /// A row action that removes something: shown as a trailing menu item and asked about first.
    public var removes: Bool { type == "store_delete" }
}

public struct NestRow: Equatable, Sendable {
    public var key: String, title: String, subtitle = "", trailing = "", icon = "", image = ""
    public var action: NestAction?
    public init(key: String, title: String, subtitle: String = "", trailing: String = "", icon: String = "", image: String = "", action: NestAction? = nil) {
        self.key = key; self.title = title; self.subtitle = subtitle; self.trailing = trailing; self.icon = icon; self.image = image; self.action = action
    }
}

public struct NestOption: Equatable, Sendable {
    public var value: String, label: String
    public init(value: String, label: String) { self.value = value; self.label = label }
}

public struct NestField: Equatable, Sendable {
    public var id: String, type: String, label: String
    public var required = false, placeholder = ""
    public var options: [NestOption] = []
    public var min: Double?, max: Double?, maxLength: Int?
    /// The default as the field holds it: a string, or "true" / "false" for a toggle.
    public var `default` = ""

    public init(id: String, type: String, label: String, required: Bool = false, placeholder: String = "", options: [NestOption] = [],
                min: Double? = nil, max: Double? = nil, maxLength: Int? = nil, default def: String = "") {
        self.id = id; self.type = type; self.label = label; self.required = required; self.placeholder = placeholder; self.options = options
        self.min = min; self.max = max; self.maxLength = maxLength; self.default = def
    }
}

public struct NestPoint: Equatable, Sendable {
    public var x: String, y: Double
    public init(x: String, y: Double) { self.x = x; self.y = y }
}

public indirect enum NestComponent: Equatable, Sendable {
    case header(text: String, subtitle: String)
    case text(String, style: String)
    case stat(label: String, value: String, unit: String, caption: String, icon: String)
    case list(rows: [NestRow], emptyText: String?, emptyIcon: String)
    case card(title: String, subtitle: String, children: [NestComponent], action: NestAction?)
    case chart(kind: String, label: String, points: [NestPoint])
    case image(src: String, alt: String, height: Int)
    case button(label: String, style: String, action: NestAction)
    case form(fields: [NestField], submitLabel: String, submit: NestAction)
    case empty(text: String, icon: String, action: NestAction?)
    /// A part of a type this app does not know (or one the server marked `unsupported`): one row. Blank text = the app's own words.
    case unsupported(text: String)
}

/// The screen: a `title` (the surface's), the `components` top to bottom, and the data `errors` to show once as a quiet notice.
public struct NestScreen: Equatable, Sendable {
    public var title = ""
    public var components: [NestComponent] = []
    public var errors: [String] = []
    public init(title: String = "", components: [NestComponent] = [], errors: [String] = []) {
        self.title = title; self.components = components; self.errors = errors
    }
}

public enum NestScreenParse {
    public static func screen(_ root: JSON) -> NestScreen {
        guard case .object = root else { return NestScreen() }
        let o = Lenient(root)
        var budget = Budget(left: NestLimits.maxComponents)
        let comps = parseList(o["components"], depth: 0, budget: &budget)
        var errors: [String] = []
        for e in o["errors"]?.arrayValue ?? [] {
            let text = Lenient(e).str("error")
            if !text.trimmingCharacters(in: .whitespaces).isEmpty, !errors.contains(text) { errors.append(text) }
        }
        return NestScreen(title: o.str("title"), components: comps, errors: errors)
    }

    private struct Budget {
        var left: Int
        mutating func take() -> Bool { if left > 0 { left -= 1; return true }; return false }
    }

    private static func parseList(_ el: JSON?, depth: Int, budget: inout Budget) -> [NestComponent] {
        var out: [NestComponent] = []
        let items = el?.arrayValue ?? []
        for e in (depth == 0 ? items : Array(items.prefix(NestLimits.maxChildren))) {
            guard case .object = e else { continue }
            if !budget.take() { break }
            out.append(one(Lenient(e), depth: depth, budget: &budget))
        }
        return out
    }

    private static func text(_ o: Lenient, _ k: String) -> String { String(o.str(k).prefix(NestLimits.maxText)) }

    private static func one(_ c: Lenient, depth: Int, budget: inout Budget) -> NestComponent {
        switch c.str("type") {
        case "header": return .header(text: text(c, "text"), subtitle: text(c, "subtitle"))
        case "text":
            let s = c.str("style")
            return .text(text(c, "text"), style: ["body", "caption", "title", "label"].contains(s) ? s : "body")
        case "stat": return .stat(label: text(c, "label"), value: text(c, "value"), unit: text(c, "unit"), caption: text(c, "caption"), icon: c.str("icon"))
        case "list":
            var rows: [NestRow] = []
            for r in (c["rows"]?.arrayValue ?? []).prefix(NestLimits.maxRows) {
                guard case .object = r else { continue }
                let ro = Lenient(r)
                rows.append(NestRow(key: ro.str("key"), title: text(ro, "title"), subtitle: text(ro, "subtitle"), trailing: text(ro, "trailing"),
                                    icon: ro.str("icon"), image: ro.str("image"), action: action(ro["action"])))
            }
            let empty = c["empty"].map { Lenient($0) }
            let emptyText = empty.map { text($0, "text") }.flatMap { $0.isEmpty ? nil : $0 }
            return .list(rows: rows, emptyText: emptyText, emptyIcon: empty?.str("icon") ?? "")
        case "card":
            // cards nest at most 3 deep: a card in the fourth level is shown as an unsupported part
            if depth >= NestLimits.maxCardDepth { return .unsupported(text: "") }
            return .card(title: text(c, "title"), subtitle: text(c, "subtitle"), children: parseList(c["children"], depth: depth + 1, budget: &budget),
                         action: action(c["action"]))
        case "chart":
            var points: [NestPoint] = []
            for p in (c["points"]?.arrayValue ?? []).prefix(NestLimits.maxPoints) {
                guard case .object = p, let y = Lenient(p)["y"]?.doubleValue, y.isFinite else { continue }
                points.append(NestPoint(x: Lenient(p).str("x"), y: y))
            }
            return .chart(kind: c.str("kind") == "bar" ? "bar" : "line", label: text(c, "label"), points: points)
        case "image":
            let h = c.optInt("height").map { Swift.min(Swift.max($0, 40), NestLimits.maxImage) } ?? NestLimits.defaultImage
            return .image(src: c.str("src"), alt: text(c, "alt"), height: h)
        case "button":
            guard let a = action(c["action"]) else { return .unsupported(text: "") }
            let s = c.str("style")
            return .button(label: text(c, "label"), style: ["primary", "secondary", "danger"].contains(s) ? s : "primary", action: a)
        case "form": return form(c)
        case "empty": return .empty(text: text(c, "text"), icon: c.str("icon"), action: action(c["action"]))
        case "unsupported": return .unsupported(text: text(c, "text"))
        default: return .unsupported(text: "")
        }
    }

    private static let fieldTypes: Set<String> = ["text", "multiline", "number", "date", "time", "select", "toggle"]

    private static func form(_ c: Lenient) -> NestComponent {
        let submit = c["submit"].map { Lenient($0) }
        guard let submit, let act = action(submit["action"]) else { return .unsupported(text: "") }
        var fields: [NestField] = []
        for f in (c["fields"]?.arrayValue ?? []).prefix(NestLimits.maxFields) {
            guard case .object = f else { continue }
            let fo = Lenient(f)
            let id = fo.str("id")
            let type = fo.str("type")
            guard !id.isEmpty, fieldTypes.contains(type) else { continue }
            var options: [NestOption] = []
            for op in (fo["options"]?.arrayValue ?? []).prefix(NestLimits.maxOptions) {
                guard case .object = op else { continue }
                let oo = Lenient(op)
                options.append(NestOption(value: oo.str("value"), label: oo.str("label").isEmpty ? oo.str("value") : oo.str("label")))
            }
            fields.append(NestField(id: id, type: type, label: text(fo, "label"), required: fo.bool("required"), placeholder: text(fo, "placeholder"),
                                    options: options, min: fo.optDouble("min"), max: fo.optDouble("max"),
                                    maxLength: fo.optInt("max_length").flatMap { $0 > 0 ? $0 : nil }, default: fo.str("default")))
        }
        if fields.isEmpty { return .unsupported(text: "") }
        return .form(fields: fields, submitLabel: text(submit, "label"), submit: act)
    }

    public static func action(_ el: JSON?) -> NestAction? {
        guard let el, case .object = el else { return nil }
        let a = Lenient(el)
        let id = a.str("id")
        if id.isEmpty { return nil }
        return NestAction(id: id, type: a.str("type"), confirm: a.bool("confirm"), label: String(a.str("label").prefix(NestLimits.maxText)),
                          item: a.str("item"), data: a.str("data"), url: a.str("url"))
    }
}

/// The server's answer to POST .../actions/{id} (HTTP 200); an error is an `ApiError`.
public enum NestActionResult: Equatable, Sendable {
    case done(message: String, refresh: Bool)
    /// `sends`: what goes out, as plain "key: value" pairs.
    case confirm(token: String, title: String, detail: String, sends: [NestSend], expiresIn: Int)
    case open(url: String)
    case started(message: String, turnId: String)
    case unknown

    public static func parse(_ root: JSON) -> NestActionResult {
        guard case .object = root else { return .unknown }
        let o = Lenient(root)
        switch o.str("status") {
        case "done": return .done(message: o.str("message"), refresh: o.bool("refresh"))
        case "confirm":
            let sends = (o["sends"]?.objectValue ?? [:]).sorted { $0.key < $1.key }.map { NestSend(key: $0.key, value: flat($0.value)) }
            return .confirm(token: o.str("token"), title: o.str("title"), detail: o.str("detail"), sends: sends, expiresIn: o.int("expires_in"))
        case "open": return .open(url: o.str("url"))
        case "started": return .started(message: o.str("message"), turnId: o.str("turn_id"))
        default: return .unknown
        }
    }

    /// A value of `sends` as a plain line: text as it is, a number or true/false as written, a list or object as short JSON.
    private static func flat(_ v: JSON) -> String {
        let s: String
        switch v {
        case .null: s = ""
        case .array, .object: s = v.compactString
        default: s = v.text ?? ""
        }
        return String(s.prefix(NestLimits.maxText))
    }
}

public struct NestSend: Equatable, Sendable {
    public var key: String, value: String
    public init(key: String, value: String) { self.key = key; self.value = value }
}

/// Reading and checking what a form holds, before it is sent.
public enum NestFormRules {
    /// The ids of required fields with no value (a toggle always has one), and of numbers that are not numbers or out of range.
    public static func missing(_ fields: [NestField], _ values: [String: String]) -> [String] {
        let empty = fields.filter { $0.required && $0.type != "toggle" && (values[$0.id] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }.map(\.id)
        let bad = fields.filter { f in
            guard f.type == "number" else { return false }
            let t = (values[f.id] ?? "").trimmingCharacters(in: .whitespaces)
            return !t.isEmpty && !numberOk(f, t)
        }.map(\.id)
        return empty + bad
    }

    private static func numberOk(_ f: NestField, _ text: String) -> Bool {
        guard let n = Double(text.replacingOccurrences(of: ",", with: ".")) else { return false }
        return (f.min == nil || n >= f.min!) && (f.max == nil || n <= f.max!)
    }

    /// The values to send: number as a number, toggle as true / false, everything else as the text; empty optional fields are left out.
    public static func payload(_ fields: [NestField], _ values: [String: String]) -> [String: JSON] {
        var m: [String: JSON] = [:]
        for f in fields {
            let raw = values[f.id] ?? f.default
            switch f.type {
            case "toggle": m[f.id] = .bool(raw == "true")
            case "number":
                if let n = Double(raw.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")) { m[f.id] = .number(n) }
            default:
                if !raw.isEmpty { m[f.id] = .string(String(raw.prefix(f.maxLength ?? NestLimits.maxText))) }
            }
        }
        return m
    }

    /// What a field holds before the reader touches it.
    public static func initial(_ fields: [NestField]) -> [String: String] {
        var m: [String: String] = [:]
        for f in fields {
            if f.type == "toggle" { m[f.id] = f.default == "true" ? "true" : "false" }
            else if f.type == "select", f.default.isEmpty, f.required { m[f.id] = f.options.first?.value ?? "" }
            else { m[f.id] = f.default }
        }
        return m
    }
}

/// A system browser may open an https (or http) address only; nothing else leaves the app from a screen.
public func nestOpenable(_ url: String) -> Bool {
    let u = url.trimmingCharacters(in: .whitespaces).lowercased()
    return (u.hasPrefix("https://") || u.hasPrefix("http://")) && !u.contains(" ")
}
