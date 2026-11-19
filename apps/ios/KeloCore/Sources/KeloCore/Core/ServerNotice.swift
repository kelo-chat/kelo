import Foundation

/// "Kelo server update available (x.y.z)" (Android update/ServerNotice.kt; docs/version-notices.md part 5). The server names a newer
/// release it lets this caller install (`X-Kelo-Server-Update`); the notice shows at most once a day, and not within an hour of the
/// app-update popup, and never over a full-screen notice (`covered`).
public enum ServerNoticeRules {
    /// Not within an hour of the app-update popup (two update popups in a row feel like nagging).
    public static let afterAppPopup: TimeInterval = 3600
    /// At most once in this many hours, whatever the answer was (a clock set back counts as due).
    public static let everyHours = 24

    public static func show(_ offered: String?, lastShown: Date?, appShown: Date?, now: Date, covered: Bool) -> Bool {
        guard AppNoticeRules.clean(offered) != nil, !covered else { return false }
        if let at = lastShown, !(now.timeIntervalSince(at) >= Double(everyHours) * 3600 || at > now) { return false }
        if let app = appShown, now >= app, now.timeIntervalSince(app) < afterAppPopup { return false }
        return true
    }
}
