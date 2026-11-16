import Foundation
import KeloCore

/// GET/POST /api/google/beta (Android connectors/ConnectorModels.kt GoogleBetaState): this server's request to try Google sign-in while
/// it is closed for everyone. `status` is "none", "requested", "approved" or "declined"; `open` is true once new Google sign-ins can
/// start here (approved, or opened to all).
public struct GoogleBetaState: Codable, Equatable, Sendable {
    public var status: String
    public var email: String
    public var products: [String]
    public var open: Bool
    public var canRequest: Bool

    public init(status: String = "none", email: String = "", products: [String] = [], open: Bool = false, canRequest: Bool = false) {
        self.status = status; self.email = email; self.products = products; self.open = open; self.canRequest = canRequest
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        status = j.str("status", "none"); email = j.str("email"); products = j.strings("products"); open = j.bool("open")
        canRequest = j.bool("can_request")
    }
}

/// What the beta part of a Gmail / Google Calendar screen shows, and the check of its address (Android BetaRules).
public enum BetaRules {
    public enum Shown: Equatable { case request, requested, declined, opened, nothing }

    /// The product a connector asks for ("gmail" / "calendar"), or nil when it is not a Google sign-in connector.
    public static func product(_ connectorId: String) -> String? {
        switch connectorId {
        case "gmail": return "gmail"
        case "google_calendar": return "calendar"
        default: return nil
        }
    }

    public static func shown(_ s: GoogleBetaState?) -> Shown {
        guard let s else { return .nothing }
        if s.open { return .opened }
        switch s.status {
        case "requested": return .requested
        case "declined": return .declined
        case "approved": return .opened
        default: return s.canRequest ? .request : .nothing
        }
    }

    private static let pattern = "^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\\.[A-Za-z]{2,24}$"

    /// The address as it is sent (trimmed), or nil when it is not a plain email address (the same check as the server's).
    public static func email(_ typed: String) -> String? {
        let t = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.count <= 254, t.range(of: pattern, options: .regularExpression) != nil else { return nil }
        return t
    }
}

public extension KeloApi {
    func googleBeta() async throws -> GoogleBetaState { try await get("/api/google/beta") }

    func requestGoogleBeta(email: String, product: String, appVersion: String) async throws -> GoogleBetaState {
        try await post("/api/google/beta", body: ["email": .string(email), "products": .array([.string(product)]),
                                                  "app_version": .string(appVersion)], as: GoogleBetaState.self)
    }
}
