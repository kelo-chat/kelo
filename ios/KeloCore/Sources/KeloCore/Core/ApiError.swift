import Foundation

/// A request to the Kelo server that did not succeed (Android core/ApiError.kt).
///
/// `status` is the HTTP status, or `ApiError.noResponse` (0) when the server could not be reached at all (no network, wrong address,
/// TLS failure, App Transport Security refusing plain http) or answered something that is not JSON. `detail` is the server's
/// `{"detail": "..."}` text when it sent one as a string (or that object's "message"), otherwise "HTTP <status>". `detailJson` is the
/// server's `detail` when it is an object (e.g. the 409 `secret_in_message` answer the chat reads with `SecretGuard.parse`); nil
/// otherwise. It is left out of `description`.
public struct ApiError: Error, Equatable, Sendable, CustomStringConvertible {
    public static let noResponse = 0

    public let status: Int
    public let detail: String
    public let detailJson: [String: JSON]?
    /// The answer's top-level `code` (spec 5.10: `423 {"code": "locked"}`, `409 {"code": "e2e_key_changed"}`), else the `code` of an
    /// object `detail`; nil when there is none.
    public let code: String?

    public init(status: Int, detail: String, detailJson: [String: JSON]? = nil, code: String? = nil) {
        self.status = status
        self.detail = detail
        self.detailJson = detailJson
        self.code = code
    }

    public var isNetwork: Bool { status == ApiError.noResponse }
    public var isUnauthorized: Bool { status == 401 }
    /// Worth trying again (a network failure, a server error, a timeout or a rate limit). Not a 507: storage is full (the cap, or the
    /// server's own disk, `"code": "disk_full"`), which trying again does not fix; its words say what to do.
    public var isTransient: Bool { isNetwork || (status >= 500 && status != ApiError.insufficientStorage) || status == 408 || status == 429 }
    /// Storage is full: the cap, or the Kelo server's own disk (`detail` says which, in the user's language).
    public var isStorageFull: Bool { status == ApiError.insufficientStorage }
    public static let insufficientStorage = 507

    public var description: String { "ApiError(\(status), \(detail))" }

    /// The error of a non-2xx answer: the string `detail` of a JSON body (or its object's "message"), else "HTTP <code>".
    public static func from(status: Int, body: Data) -> ApiError {
        let json = try? JSON.parse(body)
        let raw = json?["detail"]
        let detail = raw?.stringValue ?? raw?["message"]?.stringValue ?? "HTTP \(status)"
        return ApiError(status: status, detail: detail, detailJson: raw?.objectValue,
                        code: json?["code"]?.stringValue ?? raw?["code"]?.stringValue)
    }

    /// The server is locked (Keyguard: no master key in memory) and the call needed a secret: show the unlock prompt.
    public var isLocked: Bool { status == 423 && code == "locked" }
    /// The server's KE2E key changed: pair again.
    public var isKeyChanged: Bool { code == KE2E.keyChangedCode }
}
