import Foundation

/// The upload limits are asked once per `KeloApi`, and the app builds a new `KeloApi` on each sign-in or new pairing, so signing out
/// or pairing again starts over without anyone clearing anything.
final class UploadLimitsBox: @unchecked Sendable {
    private let lock = NSLock()
    private var source: UploadLimitsSource?

    func source(for api: KeloApi) -> UploadLimitsSource {
        lock.withLock {
            if let source { return source }
            let made = UploadLimitsSource(api: api)
            source = made
            return made
        }
    }
}

extension KeloApi {
    /// What the server takes in one upload on this session's route (asked once, see `UploadLimitsSource`).
    public var uploadLimits: UploadLimitsSource { uploadLimitsBox.source(for: self) }

    /// The one way a file goes up (chat tray, My → Documents): one request, or in parts where this route has a limit.
    public var uploader: FileUploader { FileUploader(api: self, limits: uploadLimits) }
}
