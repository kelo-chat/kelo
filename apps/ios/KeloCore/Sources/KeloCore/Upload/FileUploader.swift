import Foundation

/// The one way a file goes up, for the chat's tray and for My → Documents: asks the session's `UploadLimitsSource`, makes the
/// `UploadPlan` and sends it as one request or in parts (`PartedUpload`). Never holds the file in memory (a part is cut to a temp file).
/// A file over what the route allows is refused with `UploadTooLarge` before anything is sent; a server that said no throws `ApiError`
/// with the server's own words (507 storage full, 409 too many large uploads open, …).
public final class FileUploader: Sendable {
    private let api: KeloApi
    private let limits: UploadLimitsSource
    private let backoff: [UInt64]
    private let directory: URL

    /// The waits (ns) before the 1st, 2nd and 3rd new try of one part: 1, 2 and 4 seconds.
    public static let defaultBackoff: [UInt64] = [1_000_000_000, 2_000_000_000, 4_000_000_000]

    /// `backoff`: waits in nanoseconds before each new try of a part. `directory`: where temp copies go (the app's own temp directory).
    public init(api: KeloApi, limits: UploadLimitsSource, backoff: [UInt64] = FileUploader.defaultBackoff,
                directory: URL = FileManager.default.temporaryDirectory) {
        self.api = api; self.limits = limits; self.backoff = backoff; self.directory = directory
    }

    /// Uploads `source` as `name`; `progress` gets (sent, total). Returns the stored path the server reports.
    public func upload(name: String, source: UploadSource, type: String?,
                       progress: @escaping @Sendable (Int64, Int64) -> Void = { _, _ in }) async throws -> String {
        let l = await limits.get()
        let image = Self.isImage(name: name, type: type)
        var size = Self.size(of: source)
        var plan = UploadPlan.of(size: size, isImage: image, limits: l)
        var source = source
        var spooled: URL?
        defer { if let spooled { try? FileManager.default.removeItem(at: spooled) } }

        // a stream, or a file whose size cannot be read, is copied to a temp file: that gives its size and a file the transport can send
        // (a route with a limit and no parts says "too large" only after at most limit+1 bytes)
        var needsFile = false
        if case .stream = source { needsFile = true }
        if size < 0, plan != .single { needsFile = true }
        if needsFile, case .tooLarge = plan, size >= 0 { needsFile = false }
        if needsFile {
            let max: Int64? = { if case .tooLarge(let limit) = plan { return limit }; return nil }()
            let copy = try await Self.spool(source, max: max, directory: directory)
            spooled = copy
            source = .file(copy)
            size = Self.size(of: source)
            plan = UploadPlan.of(size: size, isImage: image, limits: l)
        }

        switch plan {
        case .single:
            switch source {
            case .data(let d): return try await api.upload(name: name, body: .data(d), contentType: type, progress: progress)
            case .file(let u): return try await api.upload(name: name, body: .file(u), contentType: type, progress: progress)
            case .stream: preconditionFailure("copied to a file above")
            }
        case .parts:
            return try await PartedUpload(calls: PartCalls(api: api), backoff: backoff, directory: directory)
                .run(name: name, size: size, source: source, progress: progress)
        case .tooLarge(let limit):
            throw UploadTooLarge(limit: limit)
        case .needsSize:
            preconditionFailure("the size is known here")
        }
    }

    /// A photo by its name's suffix (what the server goes by) or its type: photos have their own, smaller limit.
    static func isImage(name: String, type: String?) -> Bool {
        let ext = (name as NSString).pathExtension.lowercased()
        return ["jpg", "jpeg", "png", "webp", "heic", "heif"].contains(ext) || (type ?? "").hasPrefix("image/")
    }

    /// The bytes a source holds (-1: not known).
    static func size(of source: UploadSource) -> Int64 {
        switch source {
        case .data(let d): return Int64(d.count)
        case .file(let u): return (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? -1
        case .stream(let size, _): return size
        }
    }

    /// Copies a source to a new temp file, a chunk at a time. More than `max` bytes (when given) stops it with `UploadTooLarge`. The
    /// partial file is deleted on any failure.
    static func spool(_ source: UploadSource, max: Int64?, directory: URL) async throws -> URL {
        let input: InputStream
        switch source {
        case .stream(_, let open): input = try open()
        case .file(let u): guard let s = InputStream(url: u) else { throw UploadSourceError() }; input = s
        case .data(let d): input = InputStream(data: d)
        }
        let out = directory.appendingPathComponent("kelo-upload-\(UUID().uuidString).tmp")
        guard FileManager.default.createFile(atPath: out.path, contents: nil) else { throw UploadSourceError() }
        input.open()
        defer { input.close() }
        do {
            let writer = try FileHandle(forWritingTo: out)
            defer { try? writer.close() }
            var buffer = [UInt8](repeating: 0, count: PartSlice.chunk)
            var total: Int64 = 0
            while true {
                try Task.checkCancellation()
                let n = input.read(&buffer, maxLength: buffer.count)
                if n < 0 { throw input.streamError ?? UploadSourceError() }
                if n == 0 { break }
                total += Int64(n)
                if let max, total > max { throw UploadTooLarge(limit: max) }
                try writer.write(contentsOf: Data(buffer[0..<n]))
            }
        } catch {
            try? FileManager.default.removeItem(at: out)
            throw error
        }
        return out
    }
}
