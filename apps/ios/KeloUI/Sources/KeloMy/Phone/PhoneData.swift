import SwiftUI
import Foundation
import EventKit
import Contacts
import KeloCore
import KeloShared

/// The state of one row while it syncs.
struct FeatureStatus: Equatable {
    var busy = false
    var lastAt: Double = 0
    var count = 0
    var failed = false
}

/// What the phone sync needs from the signed-in app: the API and this phone's name (the AppModel).
@MainActor protocol PhoneSource: AnyObject {
    var api: KeloApi? { get }
    var deviceName: String { get }
}

extension AppModel: PhoneSource {}

/// Reads the iPhone's calendar (EventKit) and contacts (Contacts) and keeps the user's server up to date with the same endpoints
/// the Android app uses (PUT /api/phone-calendar, PUT /api/phone/contacts; DELETE when turned off). Only when the owner turns a row
/// on here and iOS allows it. Sends on app open (at most every few minutes / hours), on a change in the phone's data while the app
/// runs, in background refreshes, and on 지금 보내기.
@MainActor @Observable final class PhoneDataSync {
    /// The app's one (the app root and the background refresh hand it on); views get it from the environment.
    static let root = PhoneDataSync()
    @ObservationIgnored private let prefs: Prefs

    init(prefs: Prefs = .shared) { self.prefs = prefs }

    var status: [PhoneFeature: FeatureStatus] = [:]
    @ObservationIgnored private let eventStore = EKEventStore()
    @ObservationIgnored private var observers: [NSObjectProtocol] = []

    // ---- the owner's switches (kept on this phone) ----------------------------------------------------------------------------------

    func on(_ f: PhoneFeature) -> Bool { prefs.bool("phone.on.\(f.rawValue)", default: false) }
    private func setOn(_ f: PhoneFeature, _ v: Bool) { prefs.set("phone.on.\(f.rawValue)", v) }
    private func wipePending(_ f: PhoneFeature) -> Bool { prefs.bool("phone.wipe.\(f.rawValue)", default: false) }
    private func setWipePending(_ f: PhoneFeature, _ v: Bool) { prefs.set("phone.wipe.\(f.rawValue)", v) }
    func lastAt(_ f: PhoneFeature) -> Double { Double(prefs.int("phone.last.\(f.rawValue)", default: 0)) }
    func lastCount(_ f: PhoneFeature) -> Int { prefs.int("phone.count.\(f.rawValue)", default: 0) }

    // ---- what iOS allows ----------------------------------------------------------------------------------------------------------------

    func permitted(_ f: PhoneFeature) -> Bool {
        switch f {
        case .calendar: EKEventStore.authorizationStatus(for: .event) == .fullAccess
        case .contacts: CNContactStore.authorizationStatus(for: .contacts) == .authorized
        case .dialer: true   // opening the phone app needs no permission (iOS asks before every call)
        }
    }

    /// The owner said no before: only iPhone Settings can change it now.
    func denied(_ f: PhoneFeature) -> Bool {
        switch f {
        case .calendar:
            let s = EKEventStore.authorizationStatus(for: .event)
            return s == .denied || s == .restricted || s == .writeOnly
        case .contacts:
            let s = CNContactStore.authorizationStatus(for: .contacts)
            return s == .denied || s == .restricted
        case .dialer: return false
        }
    }

    private func ask(_ f: PhoneFeature) async -> Bool {
        switch f {
        case .calendar: return (try? await eventStore.requestFullAccessToEvents()) ?? false
        case .contacts: return (try? await CNContactStore().requestAccess(for: .contacts)) ?? false
        case .dialer: return true
        }
    }

    // ---- on / off / send -----------------------------------------------------------------------------------------------------------------

    /// Turns a row on: asks iOS when needed, then sends at once. False when iOS said no.
    func turnOn(_ f: PhoneFeature, app: any PhoneSource) async -> Bool {
        if !permitted(f) {
            guard await ask(f) else { return false }
        }
        setOn(f, true)
        setWipePending(f, false)
        _ = await sync(f, app: app, force: true)
        return true
    }

    /// Turns a row off and asks the server to forget what this phone sent. False when the server could not be reached (it is
    /// asked again on the next sync).
    func turnOff(_ f: PhoneFeature, app: any PhoneSource) async -> Bool {
        setOn(f, false)
        prefs.set("phone.last.\(f.rawValue)", 0)
        prefs.set("phone.count.\(f.rawValue)", 0)
        status[f] = FeatureStatus()
        let ok: Bool
        if let api = app.api { ok = (try? await api.request("DELETE", f.path)) != nil } else { ok = false }
        setWipePending(f, !ok)
        return ok
    }

    func syncAll(app: AppModel?, force: Bool) async {
        guard let app else { return }
        for f in PhoneFeature.allCases { _ = await sync(f, app: app, force: force) }
    }

    @discardableResult
    func sync(_ f: PhoneFeature, app: any PhoneSource, force: Bool) async -> Bool {
        guard let api = app.api else { return false }
        if !on(f) && wipePending(f) {
            if (try? await api.request("DELETE", f.path)) != nil { setWipePending(f, false) }
        }
        guard on(f), permitted(f) else { return false }
        let now = Date().timeIntervalSince1970
        if !force && !f.due(lastAt: lastAt(f), now: now) { return true }
        if status[f]?.busy == true { return true }
        status[f, default: FeatureStatus()].busy = true
        status[f]?.failed = false
        do {
            let count = try await send(f, api: api, device: app.deviceName)
            prefs.set("phone.last.\(f.rawValue)", Int(now))
            prefs.set("phone.count.\(f.rawValue)", count)
            status[f] = FeatureStatus(lastAt: now, count: count)
            return true
        } catch {
            status[f]?.busy = false
            status[f]?.failed = true
            return false
        }
    }

    private func send(_ f: PhoneFeature, api: KeloApi, device: String) async throws -> Int {
        switch f {
        case .calendar:
            let zone = TimeZone.current
            let window = PhoneCalendarRules.window(now: Date().timeIntervalSince1970, zone: zone)
            let rows = readEvents(window)
            var ids = idMap
            let events = PhoneCalendarRules.clean(rows, zone: zone) { ids.number($0) }
            let target = writeTarget()
            let write = target.map { (ids.number("cal:" + $0.calendarIdentifier), $0.title) }
            ids.keep(Set(rows.map(\.id)).union(writableCalendars().map { "cal:" + $0.calendarIdentifier }))
            idMap = ids
            try await api.request("PUT", f.path, body: PhoneCalendarRules.snapshot(events, window: window, zone: zone, deviceName: device, write: write))
            return events.count
        case .dialer:
            try await api.request("PUT", f.path, body: ["device_name": .string(String(device.prefix(40)))])
            return 0
        case .contacts:
            let rows = try await Task.detached(priority: .utility) { try PhoneReaders.contacts() }.value
            try await api.request("PUT", f.path, body: ContactRules.body(rows, device: device))
            return min(rows.count, ContactRules.maxContacts)
        }
    }

    private func readEvents(_ window: (Double, Double)) -> [PhoneEvent] {
        let pred = eventStore.predicateForEvents(withStart: Date(timeIntervalSince1970: window.0),
                                                 end: Date(timeIntervalSince1970: window.1), calendars: nil)
        return eventStore.events(matching: pred).map { e in
            let me = e.attendees?.first { $0.isCurrentUser }
            // iOS cannot tell a Google account from another one: the type says only "local" or nothing
            let local = e.calendar?.source.map { $0.sourceType == .local || $0.sourceType == .birthdays } ?? false
            return PhoneEvent(
                id: e.eventIdentifier ?? "", title: e.title ?? "",
                start: e.startDate.timeIntervalSince1970, end: e.endDate.timeIntervalSince1970, allDay: e.isAllDay,
                location: e.location ?? "", calendar: e.calendar?.title ?? "",
                declined: me?.participantStatus == .declined, cancelled: e.status == .canceled,
                attendees: e.attendees?.count ?? 0, notes: e.notes ?? "",
                account: e.calendar?.source?.title ?? "", accountType: local ? "local" : "")
        }
    }

    // ---- writing to the calendar (each change approved on a card first) -----------------------------------------------------------

    /// "일정 추가·수정도 허용": Kelo may add and change events in `writeCalendarId`. Off by default.
    var writeAllowed: Bool {
        get { prefs.bool("phone.cal.write", default: false) }
        set { prefs.set("phone.cal.write", newValue) }
    }

    /// The calendar Kelo writes to (EKCalendar.calendarIdentifier); nil = the phone's default one.
    var writeCalendarId: String? {
        get { prefs.string("phone.cal.write.calendar") }
        set { prefs.set("phone.cal.write.calendar", newValue) }
    }

    /// The calendars the owner may pick to write to (ones that can be changed), the default one first.
    func writableCalendars() -> [EKCalendar] {
        guard permitted(.calendar) else { return [] }
        let def = eventStore.defaultCalendarForNewEvents?.calendarIdentifier
        return eventStore.calendars(for: .event).filter(\.allowsContentModifications)
            .sorted { ($0.calendarIdentifier == def ? 0 : 1, $0.title) < ($1.calendarIdentifier == def ? 0 : 1, $1.title) }
    }

    /// Where Kelo writes: the picked calendar when it still exists, else the first writable one; nil = read-only.
    func writeTarget() -> EKCalendar? {
        guard on(.calendar), writeAllowed, permitted(.calendar) else { return nil }
        let cals = writableCalendars()
        return cals.first { $0.calendarIdentifier == writeCalendarId } ?? cals.first
    }

    private var idMap: PhoneIdMap {
        get {
            guard let d = prefs.string("phone.cal.ids")?.data(using: .utf8),
                  let o = try? JSONDecoder().decode([String: Int64].self, from: d) else { return PhoneIdMap() }
            return PhoneIdMap(numbers: o.filter { $0.key != "#next" }, next: o["#next"] ?? 1)
        }
        set {
            var o = newValue.numbers
            o["#next"] = newValue.next
            prefs.set("phone.cal.ids", (try? JSONEncoder().encode(o)).flatMap { String(data: $0, encoding: .utf8) })
        }
    }

    /// Runs one approved calendar change. (ok, error)
    func runCalendar(_ t: DeviceTask, zone: TimeZone = .current) -> (Bool, String) {
        guard let target = writeTarget() else { return (false, "no calendar on the phone can be changed") }
        var ids = idMap
        do {
            switch t.kind {
            case "cal_add":
                if t.l("calendar_id") != ids.number("cal:" + target.calendarIdentifier) {
                    return (false, "the calendar to write to changed on the phone; ask again")
                }
                let e = EKEvent(eventStore: eventStore)
                e.calendar = target
                fill(e, t, zone: zone)
                if e.startDate == nil { return (false, "no start time") }
                try eventStore.save(e, span: .thisEvent, commit: true)
            default:
                guard let n = t.l("event_id"), let ident = ids.identifier(n), let e = eventStore.event(withIdentifier: ident) else {
                    return (false, "the event is no longer in the phone calendar")
                }
                if e.hasRecurrenceRules { return (false, "it is a repeating event; change it in the calendar app") }
                if !(e.calendar?.allowsContentModifications ?? false) { return (false, "the calendar did not take the change (it may be read-only)") }
                if t.kind == "cal_delete" { try eventStore.remove(e, span: .thisEvent, commit: true) }
                else { fill(e, t, zone: zone); try eventStore.save(e, span: .thisEvent, commit: true) }
            }
        } catch {
            return (false, "the calendar refused the change")
        }
        idMap = ids
        return (true, "")
    }

    private func fill(_ e: EKEvent, _ t: DeviceTask, zone: TimeZone) {
        if t.has("title") { e.title = t.s("title") }
        if t.has("location") { e.location = t.s("location") }
        if t.has("description") { e.notes = t.s("description") }
        guard t.has("start") else { return }
        if t.b("all_day"), !t.s("day").isEmpty, let span = DeviceTaskRules.allDay(first: t.s("day"), last: t.s("last_day"), zone: zone) {
            e.isAllDay = true
            e.startDate = Date(timeIntervalSince1970: span.0)
            e.endDate = Date(timeIntervalSince1970: span.1 - 1)
        } else {
            let start = t.d("start") ?? 0
            e.isAllDay = false
            e.timeZone = TimeZone(identifier: t.s("timezone")) ?? zone
            e.startDate = Date(timeIntervalSince1970: start)
            e.endDate = Date(timeIntervalSince1970: max(start, t.d("end") ?? start))
        }
    }

    // ---- changes while the app runs -------------------------------------------------------------------------------------------------

    func watch(app: AppModel) {
        guard observers.isEmpty else { return }
        let nc = NotificationCenter.default
        observers.append(nc.addObserver(forName: .EKEventStoreChanged, object: eventStore, queue: .main) { [weak app, weak self] _ in
            Task { @MainActor in if let app, let self { await self.sync(.calendar, app: app, force: true) } }
        })
        observers.append(nc.addObserver(forName: .CNContactStoreDidChange, object: nil, queue: .main) { [weak app, weak self] _ in
            Task { @MainActor in if let app, let self { await self.sync(.contacts, app: app, force: true) } }
        })
    }

    func unwatch() {
        observers.forEach(NotificationCenter.default.removeObserver)
        observers = []
    }
}

/// The phone's address book, read off the main thread.
enum PhoneReaders {
    static func contacts() throws -> [ContactRow] {
        let keys: [CNKeyDescriptor] = [
            CNContactFormatter.descriptorForRequiredKeys(for: .fullName), CNContactPhoneNumbersKey as CNKeyDescriptor,
            CNContactEmailAddressesKey as CNKeyDescriptor, CNContactOrganizationNameKey as CNKeyDescriptor,
            CNContactJobTitleKey as CNKeyDescriptor, CNContactBirthdayKey as CNKeyDescriptor,
        ]
        var rows: [ContactRow] = []
        let req = CNContactFetchRequest(keysToFetch: keys)
        try CNContactStore().enumerateContacts(with: req) { c, stop in
            let name = CNContactFormatter.string(from: c, style: .fullName) ?? ""
            let phones = c.phoneNumbers.map { ($0.value.stringValue, ContactRules.label($0.label)) }
            let emails = c.emailAddresses.map { $0.value as String }
            let b = c.birthday
            rows.append(ContactRow(name: name, phones: phones, emails: emails, org: c.organizationName, title: c.jobTitle,
                                   birthday: ContactRules.birthday(year: b?.year, month: b?.month, day: b?.day)))
            if rows.count >= ContactRules.maxContacts { stop.pointee = true }
        }
        return rows
    }
}

private struct PhoneDataSyncKey: EnvironmentKey {
    @MainActor static let defaultValue = PhoneDataSync.root
}

extension EnvironmentValues {
    /// The phone-data sync (calendar, contacts, dialer) the My pages read; the app has one, a test or preview can give its own.
    var phoneDataSync: PhoneDataSync {
        get { self[PhoneDataSyncKey.self] }
        set { self[PhoneDataSyncKey.self] = newValue }
    }
}
