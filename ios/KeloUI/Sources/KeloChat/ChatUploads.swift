import Foundation
import KeloCore
import KeloShared

/// A file going up, with progress. The real one is `KeloApi.uploader` (one request, or in parts where this route has a limit); tests use a fake.
public protocol ChatUploader: Sendable {
    func upload(api: KeloApi, name: String, source: UploadSource, type: String?,
                progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String
}

/// What gets uploaded for one pick.
public struct PreparedUpload: Sendable {
    public var name: String
    public var source: UploadSource
    public var type: String?
    public var length: Int64
    public var thumbnail: Data?
    /// A temporary copy to delete once the upload ended either way.
    public var temporary: URL?

    public init(name: String, source: UploadSource, type: String?, length: Int64, thumbnail: Data? = nil, temporary: URL? = nil) {
        self.name = name; self.source = source; self.type = type; self.length = length; self.thumbnail = thumbnail; self.temporary = temporary
    }

    func cleanup() { if let temporary { try? FileManager.default.removeItem(at: temporary) } }
}

/// The session's `FileUploader`: PUT /api/uploads/{name} through the API's transport (an upload task with progress; through KE2E a
/// streamed 0x02 request) or, where this route has a per-request limit, a part-upload; reports the bytes sent; a file goes up from disk
/// (never held whole in memory). Errors as KeloApi's: the server's detail, or "server not reached"; `UploadTooLarge` for a file over
/// what this route takes.
public struct URLSessionUploader: ChatUploader {
    public init() {}

    public func upload(api: KeloApi, name: String, source: UploadSource, type: String?,
                       progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String {
        try await api.uploader.upload(name: name, source: source, type: type, progress: progress)
    }
}
