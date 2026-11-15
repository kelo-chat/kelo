/// The character's moods, in the order of the Android app's KeloMood and of the SVGs in android/design/character
/// (kelo_<mood>.svg). Which one shows is decided by `AgentActivityRules.mood`.
public enum KeloMood: String, CaseIterable, Sendable, Codable {
    case idle, working, browser, approval, thinking, done, error, offline, sleeping, listening, reading
}
