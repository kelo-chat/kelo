import Foundation
import KeloCore

// What the iPhone shares with the user's own server (Android device/DeviceRules.kt and phonecal/PhoneCalendarRules.kt), without
// the framework readers, so the payloads are tested. iOS allows only the calendar and the contacts of Android's rows: there is no
// API for text messages, the call log or other apps' notifications.

/// The rows this phone can share. Android has more (messages, call log, notifications, dialer, Health Connect); iOS has no public
/// way to read those, so they are not offered at all.
public enum PhoneFeature: String, CaseIterable, Sendable {
    /// `dialer`: no data; this phone may open its phone app with a number filled in after an approval (Android DIALER).
    case calendar, contacts, dialer

    public var path: String {
        switch self {
        case .calendar: "/api/phone-calendar"
        case .contacts: "/api/phone/contacts"
        case .dialer: "/api/phone/dialer"
        }
    }

    /// Coming back to the app sends again only when the last send is older than this (a change or 지금 보내기 always send).
    public var gapSeconds: Double {
        switch self {
        case .calendar: 5 * 60
        case .contacts: 6 * 3600
        case .dialer: 24 * 3600
        }
    }

    public func due(lastAt: Double, now: Double) -> Bool { lastAt <= 0 || now < lastAt || now - lastAt >= gapSeconds }
}

/// One occurrence read from the phone's calendar, before it is cleaned for the server.
public struct PhoneEvent: Equatable, Sendable {
    public var id: String
    public var title: String
    public var start: Double
    public var end: Double
    public var allDay: Bool
    public var location = ""
    public var calendar = ""
    public var declined = false
    public var cancelled = false
    public var attendees = 0
    /// the event's notes (what the calendar keeps as its description)
    public var notes = ""
    /// the account the calendar syncs from, and its type ("com.google"; "local" for none); empty when the phone does not say
    public var account = ""
    public var accountType = ""

    public init(id: String, title: String, start: Double, end: Double, allDay: Bool, location: String = "", calendar: String = "",
                declined: Bool = false, cancelled: Bool = false, attendees: Int = 0, notes: String = "", account: String = "",
                accountType: String = "") {
        self.id = id; self.title = title; self.start = start; self.end = end; self.allDay = allDay; self.location = location
        self.calendar = calendar; self.declined = declined; self.cancelled = cancelled; self.attendees = attendees
        self.notes = notes; self.account = account; self.accountType = accountType
    }
}

/// The window, which occurrences are left out, and the snapshot body of PUT /api/phone-calendar.
public enum PhoneCalendarRules {
    public static let daysBack = 7
    public static let daysAhead = 60
    public static let maxEvents = 3000

    /// [start, end) in seconds: from local midnight 7 days ago to local midnight 60 days ahead.
    public static func window(now: Double, zone: TimeZone) -> (Double, Double) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let today = cal.startOfDay(for: Date(timeIntervalSince1970: now))
        let a = cal.date(byAdding: .day, value: -daysBack, to: today)!
        let b = cal.date(byAdding: .day, value: daysAhead + 1, to: today)!
        return (a.timeIntervalSince1970, b.timeIntervalSince1970)
    }

    private static func day(_ seconds: Double, zone: TimeZone) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let c = cal.dateComponents([.year, .month, .day], from: Date(timeIntervalSince1970: seconds))
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// Declined and cancelled ones out, repeats out, oldest first; all-day events as the phone's own days. EventKit gives all-day
    /// events at local midnight (unlike Android's UTC), so their days are read in `zone`.
    /// `number`: the stable whole number the server knows an EventKit event by (`PhoneIdMap`); nil leaves the id out.
    public static func clean(_ rows: [PhoneEvent], zone: TimeZone, number: (String) -> Int64? = { _ in nil }) -> [JSON] {
        var seen = Set<String>()
        return rows.filter { !$0.declined && !$0.cancelled }
            .filter { seen.insert("\($0.id)|\($0.start)|\($0.title)").inserted }
            .sorted { $0.start < $1.start }
            .prefix(maxEvents)
            .map { e in
                var o: [String: JSON] = [:]
                // what a change or a deletion from Kelo names (the server keeps whole numbers only)
                if !e.id.isEmpty, let n = number(e.id) { o["id"] = .number(Double(n)) }
                o["title"] = .string(String(e.title.prefix(300)))
                o["all_day"] = .bool(e.allDay)
                if e.allDay {
                    o["day"] = .string(day(e.start, zone: zone))
                    o["last_day"] = .string(day(max(e.start, e.end - 1), zone: zone))
                }
                o["start"] = .number(e.start)
                o["end"] = .number(max(e.start, e.end))
                if !e.location.trimmingCharacters(in: .whitespaces).isEmpty { o["location"] = .string(String(e.location.prefix(300))) }
                if !e.calendar.trimmingCharacters(in: .whitespaces).isEmpty { o["calendar"] = .string(String(e.calendar.prefix(120))) }
                if e.attendees > 0 { o["attendees"] = .number(Double(e.attendees)) }
                let notes = e.notes.trimmingCharacters(in: .whitespacesAndNewlines)
                if !notes.isEmpty { o["notes"] = .string(String(notes.prefix(500))) }
                let account = e.account.trimmingCharacters(in: .whitespacesAndNewlines)
                if !account.isEmpty { o["account"] = .string(String(account.prefix(120))) }
                let accountType = e.accountType.trimmingCharacters(in: .whitespacesAndNewlines)
                if !accountType.isEmpty { o["account_type"] = .string(String(accountType.prefix(80))) }
                return .object(o)
            }
    }

    /// The whole body. `write`: the calendar Kelo may add to and change (its number, its name), only when the owner allowed
    /// writing; nil = read-only.
    public static func snapshot(_ events: [JSON], window: (Double, Double), zone: TimeZone, deviceName: String,
                                write: (Int64, String)? = nil) -> JSON {
        var o: [String: JSON] = [
            "device_name": .string(String(deviceName.prefix(40))),
            "can_write": .bool(write != nil),
            "timezone": .string(zone.identifier),
            "window_start": .number(window.0),
            "window_end": .number(window.1),
            "events": .array(events),
        ]
        if let write { o["write_calendar"] = .object(["id": .number(Double(write.0)), "name": .string(String(write.1.prefix(120)))]) }
        return .object(o)
    }
}

/// One contact as read from the phone.
public struct ContactRow: Equatable, Sendable {
    public var name: String
    public var phones: [(number: String, label: String)]
    public var emails: [String]
    public var org = ""
    public var title = ""
    public var birthday = ""

    public init(name: String, phones: [(String, String)], emails: [String], org: String = "", title: String = "", birthday: String = "") {
        self.name = name; self.phones = phones.map { (number: $0.0, label: $0.1) }; self.emails = emails
        self.org = org; self.title = title; self.birthday = birthday
    }

    public static func == (a: ContactRow, b: ContactRow) -> Bool {
        a.name == b.name && a.emails == b.emails && a.org == b.org && a.title == b.title && a.birthday == b.birthday
            && a.phones.map(\.number) == b.phones.map(\.number) && a.phones.map(\.label) == b.phones.map(\.label)
    }
}

public enum ContactRules {
    public static let maxContacts = 10_000

    private static func digits(_ s: String) -> String { s.filter { $0.isASCII && $0.isNumber } }

    /// PUT /api/phone/contacts.
    public static func body(_ rows: [ContactRow], device: String) -> JSON {
        let items: [JSON] = rows.lazy
            .filter { !$0.name.trimmingCharacters(in: .whitespaces).isEmpty || !$0.phones.isEmpty || !$0.emails.isEmpty }
            .prefix(maxContacts)
            .map { c in
                var seenPhone = Set<String>()
                let phones = c.phones.filter { seenPhone.insert(digits($0.number)).inserted }.prefix(10)
                    .map { JSON.object(["number": .string($0.number), "label": .string($0.label)]) }
                var seenMail = Set<String>()
                let emails = c.emails.filter { seenMail.insert($0).inserted }.prefix(10).map { JSON.string($0) }
                var o: [String: JSON] = ["name": .string(String(c.name.prefix(120))), "phones": .array(Array(phones)), "emails": .array(Array(emails))]
                if !c.org.trimmingCharacters(in: .whitespaces).isEmpty { o["org"] = .string(String(c.org.prefix(120))) }
                if !c.title.trimmingCharacters(in: .whitespaces).isEmpty { o["title"] = .string(String(c.title.prefix(120))) }
                if let b = birthday(c.birthday) { o["birthday"] = .string(b) }
                return .object(o)
            }
        return .object(["device_name": .string(String(device.prefix(40))), "items": .array(items)])
    }

    /// "1990-05-08", "--05-08" (no year), or nil.
    public static func birthday(_ raw: String) -> String? {
        let s = raw.trimmingCharacters(in: .whitespaces)
        if s.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil { return s }
        if s.range(of: #"^--\d{2}-\d{2}$"#, options: .regularExpression) != nil { return s }
        if s.range(of: #"^\d{8}$"#, options: .regularExpression) != nil {
            let a = Array(s)
            return "\(String(a[0..<4]))-\(String(a[4..<6]))-\(String(a[6..<8]))"
        }
        return nil
    }

    /// A birthday from date parts (EventKit / Contacts give components; a missing year becomes "--MM-DD").
    public static func birthday(year: Int?, month: Int?, day: Int?) -> String {
        guard let month, let day else { return "" }
        if let year, year > 1 { return String(format: "%04d-%02d-%02d", year, month, day) }
        return String(format: "--%02d-%02d", month, day)
    }

    /// A phone label as Contacts stores it ("_$!<Mobile>!$_") in plain words.
    public static func label(_ raw: String?) -> String {
        guard var s = raw, !s.isEmpty else { return "" }
        if s.hasPrefix("_$!<") && s.hasSuffix(">!$_") { s = String(s.dropFirst(4).dropLast(4)) }
        return s.lowercased()
    }
}

/// EventKit names events and calendars with text; the server (like Android's calendar) names them with whole numbers. This keeps a
/// stable number per identifier on this phone, so "change event 12" from the server finds the same event again.
public struct PhoneIdMap: Equatable, Sendable {
    public var numbers: [String: Int64] = [:]
    public var next: Int64 = 1

    public init(numbers: [String: Int64] = [:], next: Int64 = 1) { self.numbers = numbers; self.next = next }

    /// The number for `identifier`, made on first use.
    public mutating func number(_ identifier: String) -> Int64 {
        if let n = numbers[identifier] { return n }
        let n = next
        next += 1
        numbers[identifier] = n
        return n
    }

    public func identifier(_ number: Int64) -> String? { numbers.first { $0.value == number }?.key }

    /// Keeps only the identifiers still in use (numbers are never given out again).
    public mutating func keep(_ identifiers: Set<String>) { numbers = numbers.filter { identifiers.contains($0.key) } }
}

/// Work the owner approved that only this phone can do (POST /api/phone/tasks/claim; Android device/DeviceTasks.kt): open the phone
/// app with a number filled in, or add, change or delete an event in the calendar the owner picked. An iPhone never sends texts.
public struct DeviceTask: Equatable, Sendable {
    public var id: Int64
    public var kind: String
    public var fields: [String: JSON]

    public init(id: Int64, kind: String, fields: [String: JSON] = [:]) { self.id = id; self.kind = kind; self.fields = fields }

    public func s(_ k: String) -> String { fields[k]?.text ?? "" }
    public func d(_ k: String) -> Double? { fields[k]?.doubleValue }
    public func l(_ k: String) -> Int64? { fields[k]?.int64Value }
    public func b(_ k: String) -> Bool { fields[k]?.boolValue == true }
    public func has(_ k: String) -> Bool { fields[k] != nil && fields[k] != .null }

    public static func parseList(_ e: JSON) -> [DeviceTask] {
        (e["items"]?.arrayValue ?? e.arrayValue ?? []).compactMap { item in
            guard let o = item.objectValue, let id = o["id"]?.int64Value else { return nil }
            return DeviceTask(id: id, kind: o["kind"]?.text ?? "", fields: o)
        }
    }
}

public enum DeviceTaskRules {
    /// Why this phone will not do `t` now (nil = go ahead). The phone checks again that the row is still on.
    public static func refuse(_ t: DeviceTask, on: (PhoneFeature) -> Bool, calWrite: Bool, now: Double) -> String? {
        if let exp = t.d("expires_at"), exp < now { return "expired" }
        switch t.kind {
        case "sms": return "this phone cannot send texts"
        case "dial":
            if !on(.dialer) { return "the dialer is turned off on the phone" }
            return digits(t.s("number")).count < 3 ? "no number" : nil
        case "cal_add", "cal_update", "cal_delete":
            return on(.calendar) && calWrite ? nil : "calendar changes are turned off on the phone"
        default: return "unknown task"
        }
    }

    public static func digits(_ s: String) -> String { s.filter(\.isNumber) }

    /// The tel: number (digits and a leading +, nothing that could be a code).
    public static func dialNumber(_ raw: String) -> String {
        (raw.trimmingCharacters(in: .whitespaces).hasPrefix("+") ? "+" : "") + digits(raw).filter { $0.isASCII }
    }

    /// Opening the phone app needs the app on screen; otherwise a notification opens it when tapped (as Android).
    public enum DialWay: Equatable, Sendable { case open, notify }
    public static func dialWay(visible: Bool) -> DialWay { visible ? .open : .notify }

    /// [start, end) in seconds of an all-day event from its first and last day ("2026-11-09") in `zone`.
    public static func allDay(first: String, last: String, zone: TimeZone) -> (Double, Double)? {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        func at(_ s: String) -> Date? {
            let p = s.split(separator: "-").compactMap { Int($0) }
            guard p.count == 3 else { return nil }
            return cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2]))
        }
        guard let a = at(first), let b = at(last.isEmpty ? first : last), let end = cal.date(byAdding: .day, value: 1, to: b) else { return nil }
        return (a.timeIntervalSince1970, end.timeIntervalSince1970)
    }
}
