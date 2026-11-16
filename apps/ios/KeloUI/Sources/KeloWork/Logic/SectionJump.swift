import Foundation

/// A jump to one section of the work timeline (a notification asked for it): it waits until that section's data has been answered (a
/// section still loading is not in the list yet, so a jump then would find nothing), then happens once (Android SectionJump).
enum SectionJump {
    /// The sections whose lists have been answered (found, or truly absent): true when the jump to `key` can be tried now.
    static func ready(key: String, tasks: Bool, perms: Bool, schedules: Bool, watchers: Bool) -> Bool {
        switch key {
        case WorkTimeline.asks: tasks && perms
        case WorkTimeline.upcoming: tasks && schedules
        case WorkTimeline.watch: tasks && watchers
        default: tasks
        }
    }
}
