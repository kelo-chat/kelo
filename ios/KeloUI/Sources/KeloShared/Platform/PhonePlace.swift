import Foundation
import KeloCore
#if os(iOS)
import CoreLocation
#endif

/// This phone's location for the owner's Kelo server (weather.py "here"; Android device/PhonePlace.kt): the briefing's weather and
/// the agent's `my_location` tool for "near me" questions. Sent whenever the app is opened and location is allowed "while using the
/// app" (precise or approximate: the owner chooses in iOS's own dialog; temporary full accuracy is never asked), with its accuracy,
/// at most every `everySeconds`; never in the background. Only the newest one is kept, on the owner's own server. Asked for when the
/// briefing's weather is turned on, or from its "allow" line.
@MainActor public enum PhonePlace {
    static let atKey = "briefing.place.sent_at"
    public static let everySeconds: Double = 15 * 60

    /// Due to send: allowed, and the last one is old enough (or in the future: the clock moved).
    public static func due(granted: Bool, lastAt: Double, now: Double) -> Bool {
        granted && (now - lastAt >= everySeconds || now < lastAt)
    }

    /// The POST body: the position, its accuracy in metres when known, and the town's name, country and neighbourhood
    /// (sublocality, e.g. a Korean "dong") when the phone has them.
    public static func body(lat: Double, lon: Double, accuracy: Double?, name: String?, country: String?, sublocality: String? = nil) -> [String: JSON] {
        var body: [String: JSON] = ["lat": .number(lat), "lon": .number(lon)]
        if let accuracy, accuracy >= 0 { body["accuracy"] = .number(accuracy) }
        if let name, !name.isEmpty { body["name"] = .string(name) }
        if let country, !country.isEmpty { body["country_code"] = .string(country) }
        if let sublocality, !sublocality.isEmpty { body["sublocality"] = .string(sublocality) }
        return body
    }

    /// The neighbourhood of an address: its sublocality, unless that only repeats the town.
    public static func sublocalityOf(_ sub: String?, town: String) -> String {
        let s = (sub ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty || s == town ? "" : s
    }

    #if os(iOS)
    private static let finder = PlaceFinder()

    public static var granted: Bool {
        let s = CLLocationManager().authorizationStatus
        return s == .authorizedWhenInUse || s == .authorizedAlways
    }

    /// Location was refused: only iOS Settings can allow it now.
    public static var refused: Bool {
        let s = CLLocationManager().authorizationStatus
        return s == .denied || s == .restricted
    }

    /// Asks for location "while using the app" (iOS lets the owner pick precise or approximate); true when allowed.
    public static func ask() async -> Bool { await finder.ask() }

    /// This phone's place now (with permission): the position and, when the phone can name it, the town, country and
    /// neighbourhood. nil when no position came within the time limit.
    public static func read() async -> [String: JSON]? {
        guard granted, let loc = await finder.current() else { return nil }
        let mark = try? await CLGeocoder().reverseGeocodeLocation(loc).first
        let town = mark?.locality ?? mark?.subAdministrativeArea ?? mark?.administrativeArea ?? ""
        return body(lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
                    accuracy: loc.horizontalAccuracy >= 0 ? loc.horizontalAccuracy : nil, name: town, country: mark?.isoCountryCode,
                    sublocality: sublocalityOf(mark?.subLocality, town: town))
    }

    /// Sends the phone's location when it is due (or `force`, right after the owner allowed it).
    public static func maybeSend(api: KeloApi, force: Bool = false) async {
        let now = Date().timeIntervalSince1970
        let last = Double(Prefs.shared.string(atKey) ?? "") ?? 0
        guard force ? granted : due(granted: granted, lastAt: last, now: now) else { return }
        guard let body = await read() else { return }
        if (try? await api.request("POST", "/api/briefing/location", body: .object(body))) != nil {
            Prefs.shared.set(atKey, String(now))
        }
    }
    #else
    public static var granted: Bool { false }
    public static var refused: Bool { false }
    public static func ask() async -> Bool { false }
    public static func read() async -> [String: JSON]? { nil }
    public static func maybeSend(api: KeloApi, force: Bool = false) async {}
    #endif
}

#if os(iOS)
/// One location request at a time, with a time limit. The accuracy asked is about 100 m; when the owner chose "approximate" iOS
/// gives the reduced one instead, and that works the same.
@MainActor private final class PlaceFinder: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var asking: CheckedContinuation<Bool, Never>?
    private var locating: CheckedContinuation<CLLocation?, Never>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func ask() async -> Bool {
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways: return true
        case .notDetermined:
            return await withCheckedContinuation { c in
                asking = c
                manager.requestWhenInUseAuthorization()
            }
        default: return false
        }
    }

    func current() async -> CLLocation? {
        if let last = manager.location, Date().timeIntervalSince(last.timestamp) < PhonePlace.everySeconds { return last }
        guard locating == nil else { return nil }
        let task = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 10_000_000_000)
            self?.finish(nil)
        }
        defer { task.cancel() }
        return await withCheckedContinuation { c in
            locating = c
            manager.requestLocation()
        }
    }

    private func finish(_ loc: CLLocation?) {
        locating?.resume(returning: loc)
        locating = nil
    }

    nonisolated func locationManager(_ m: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let l = locations.last
        Task { @MainActor in self.finish(l) }
    }

    nonisolated func locationManager(_ m: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.finish(nil) }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        let s = m.authorizationStatus
        Task { @MainActor in
            guard s != .notDetermined, let c = self.asking else { return }
            self.asking = nil
            c.resume(returning: s == .authorizedWhenInUse || s == .authorizedAlways)
        }
    }
}
#endif
