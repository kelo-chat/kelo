import Foundation

/// "A new Kelo app (x.y.z) works best with your server" (docs/version-notices.md, part 3). The server names the newest released app
/// that fits it for this app's platform and channel (`X-Kelo-App-Update`); the app shows a banner once per version and remembers a
/// dismissed version on this device (Android update/AppNotice.kt).
public enum AppNoticeRules {
    public enum Action: Equatable, Sendable { case store, updater }

    /// Kelo's page in the App Store (the "Open the store" button of the banner).
    public static func storeURL(appStoreId: String?) -> URL {
        let id = (appStoreId ?? "").trimmingCharacters(in: .whitespaces)
        let known = !id.isEmpty && id.allSatisfy(\.isNumber)
        let text = known ? "itms-apps://apps.apple.com/app/id\(id)"
                         : "itms-apps://search.itunes.apple.com/WebObjects/MZSearch.woa/wa/search?media=software&term=Kelo"
        return URL(string: text)!
    }

    /// The header's value trimmed; nil for none or blank.
    public static func clean(_ text: String?) -> String? {
        let t = (text ?? "").trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : t
    }

    /// The banner shows: a version is named and it was not dismissed here.
    public static func show(_ offered: String?, dismissed: Set<String>) -> Bool {
        guard let o = clean(offered) else { return false }
        return !dismissed.contains(o)
    }

    /// The popup shows at most once in this many hours (owner 2026-11-16), whatever the answer was.
    public static let popupEveryHours = 24
    /// Coming back to the foreground after this long away asks the server again (the popup can then show).
    public static let recheckAfter: TimeInterval = 3600

    /// The popup shows: the server names a newer app (nil = this app already runs the newest) and it was not shown here in the
    /// last 24 hours. A newer version than the one last shown does not skip the wait.
    public static func popup(_ offered: String?, lastShown: Date?, now: Date) -> Bool {
        guard clean(offered) != nil else { return false }
        guard let at = lastShown else { return true }
        return now.timeIntervalSince(at) >= Double(popupEveryHours) * 3600 || at > now
    }

    /// Back in the foreground after `away` seconds: ask the server about a newer app again.
    public static func recheck(away: TimeInterval) -> Bool { away > recheckAfter }

    public static func action(channel: String) -> Action { ["play", "appstore", "msstore"].contains(channel) ? .store : .updater }

    /// The remembered versions as one string (a version never contains a comma) and back.
    public static func encode(_ set: Set<String>) -> String { set.sorted().joined(separator: ",") }
    public static func decode(_ text: String?) -> Set<String> {
        Set((text ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
    }
}

/// The dismissed versions of one device.
public struct AppNoticeStore: Sendable {
    let load: @Sendable () -> String?
    let save: @Sendable (String) -> Void
    let loadShown: @Sendable () -> String?
    let saveShown: @Sendable (String) -> Void

    public init(load: @escaping @Sendable () -> String?, save: @escaping @Sendable (String) -> Void,
                loadShown: @escaping @Sendable () -> String? = { nil }, saveShown: @escaping @Sendable (String) -> Void = { _ in }) {
        self.load = load; self.save = save; self.loadShown = loadShown; self.saveShown = saveShown
    }

    /// In UserDefaults (this device only).
    public static let defaults = AppNoticeStore(
        load: { UserDefaults.standard.string(forKey: "kelo.appNotice.seen") },
        save: { UserDefaults.standard.set($0, forKey: "kelo.appNotice.seen") },
        loadShown: { UserDefaults.standard.string(forKey: "kelo.appNotice.popupShown") },
        saveShown: { UserDefaults.standard.set($0, forKey: "kelo.appNotice.popupShown") })

    public func dismissed() -> Set<String> { AppNoticeRules.decode(load()) }
    public func add(_ version: String) -> Set<String> {
        let next = dismissed().union([version])
        save(AppNoticeRules.encode(next))
        return next
    }

    public func lastShown() -> Date? { loadShown().flatMap(TimeInterval.init).map(Date.init(timeIntervalSince1970:)) }
    public func shown(now: Date) { saveShown(String(Int(now.timeIntervalSince1970))) }
}
