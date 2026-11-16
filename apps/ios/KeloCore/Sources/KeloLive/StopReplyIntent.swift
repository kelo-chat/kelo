#if canImport(ActivityKit) && os(iOS)
import AppIntents
import Foundation

/// The activity's Stop button. A LiveActivityIntent runs in the app's process (iOS starts the app in the background if needed), where
/// `LiveHooks.stop` is set at launch to the chat's stop (DELETE /api/turns/{id}).
public struct StopReplyIntent: LiveActivityIntent {
    public static let title: LocalizedStringResource = "Stop"
    @Parameter(title: "Turn") public var turnId: String
    public init() {}
    public init(turnId: String) { self.turnId = turnId }
    public func perform() async throws -> some IntentResult {
        await LiveHooks.stop?(turnId)
        return .result()
    }
}

public enum LiveHooks {
    @MainActor public static var stop: ((String) async -> Void)? = nil
}
#endif
