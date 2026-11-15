import Foundation

/// 마이 → 에이전트 → 말투 (Android my/ToneRules.kt): how the agent talks to the user, "friendly" (친근, informal forms such as Korean
/// 반말) or "polite" (공손, polite forms such as Korean 해요체; the server's default). The page reloads the profile when it opens; a
/// change the agent made in the chat ("반말로 해 줘" → set_tone) or another device made reaches the profile through
/// GET /api/agent/status (`synced`).
public enum ToneRules {
    public static let friendly = "friendly"
    public static let polite = "polite"
    public static let all = [friendly, polite]

    /// The choice shown as selected: anything but "friendly" (empty from an older server, unknown) reads as polite.
    public static func selected(_ profile: Profile) -> String { profile.tone == friendly ? friendly : polite }

    /// The polled status's tone when it differs from the profile on screen: the profile to show; nil = nothing to change.
    public static func synced(_ profile: Profile, polled: String?) -> Profile? {
        guard let polled, all.contains(polled), polled != selected(profile) else { return nil }
        var p = profile
        p.tone = polled
        return p
    }

    /// The page opened: the server's profile as it is now (the agent may have changed the tone in the chat).
    public static func reload(_ api: KeloApi) async throws -> Profile { try await api.profile() }

    /// A choice tapped: PUT /api/profile {"tone"}; the saved profile. The next reply already uses it.
    public static func save(_ api: KeloApi, tone: String) async throws -> Profile {
        precondition(all.contains(tone), "unknown tone \(tone)")
        return try await api.updateProfile(["tone": tone])
    }
}
