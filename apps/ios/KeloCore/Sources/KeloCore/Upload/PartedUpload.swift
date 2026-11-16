import Foundation

/// What a file goes up from: bytes in memory (a photo made smaller), a file read from disk as it goes, or a stream (a provider that
/// gives no file; `size` -1 = not known). A stream is copied to a temp file first (which gives its size); the temp copy is deleted afterwards.
public enum UploadSource: Sendable {
    case data(Data)
    case file(URL)
    case stream(size: Int64, open: @Sendable () throws -> InputStream)
}

/// A part-upload the server opened.
struct PartUpload: Sendable { let id: String; let partSize: Int64; let parts: Int }

/// The server's part-upload routes (src/assistant/api/files.py), one call each. No retrying and no planning here.
struct PartCalls: Sendable {
    let api: KeloApi

    /// POST /api/uploads/{name}/parts: 413 photo too large, 507 no room, 409 three part-uploads open, 422 refused (all as `ApiError`).
    func start(name: String, size: Int64) async throws -> PartUpload {
        let a = try await api.request("POST", "/api/uploads/\(KeloApi.segment(name))/parts", body: ["size": .number(Double(size))])
        guard let id = a["upload_id"]?.stringValue, let part = a["part_size"]?.int64Value, let parts = a["parts"]?.intValue
        else { throw ApiError(status: ApiError.noResponse, detail: "unreadable answer") }
        return PartUpload(id: id, partSize: part, parts: parts)
    }

    /// PUT /api/uploads/parts/{id}/{index}: `body` is exactly this part. 422 wrong size, 404 unknown id, 507 no room.
    func put(id: String, index: Int, body: HTTPUploadBody, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        let r = try api.makeRequest("/api/uploads/parts/\(KeloApi.segment(id))/\(index)", method: "PUT")
        _ = try await api.upload(r, body: body, progress: progress)
    }

    /// GET /api/uploads/parts/{id}: the indices the server holds.
    func received(id: String) async throws -> Set<Int> {
        let a = try await api.request("GET", "/api/uploads/parts/\(KeloApi.segment(id))")
        return Set((a["received"]?.arrayValue ?? []).compactMap { $0.intValue })
    }

    /// POST …/finish: the stored path. 422 when a part is missing (the upload stays open).
    func finish(id: String) async throws -> String {
        try await api.request("POST", "/api/uploads/parts/\(KeloApi.segment(id))/finish")["path"]?.stringValue ?? ""
    }

    /// DELETE …: cancels the part-upload.
    func cancel(id: String) async throws {
        try await api.request("DELETE", "/api/uploads/parts/\(KeloApi.segment(id))")
    }
}

/// The file ended before its size said, or could not be read: not the connection's fault, so nothing is sent again.
struct UploadSourceError: Error {}

/// One part of a file as an upload body: bytes of a photo cut out, or (a file) the part copied to a temp file a chunk at a time, so no
/// part is ever held in memory whole. The temp copy is deleted by `done()`.
struct PartSlice {
    let body: HTTPUploadBody
    private let temp: URL?

    static let chunk = 1 << 20

    init(of source: UploadSource, offset: Int64, length: Int64, directory: URL) throws {
        switch source {
        case .data(let d):
            guard Int64(d.count) >= offset + length else { throw UploadSourceError() }
            body = .data(d.subdata(in: Int(offset)..<Int(offset + length))); temp = nil
        case .file(let url):
            let out = directory.appendingPathComponent("kelo-part-\(UUID().uuidString).tmp")
            do {
                try PartSlice.copy(url, offset: offset, length: length, to: out)
            } catch {
                try? FileManager.default.removeItem(at: out)
                throw UploadSourceError()
            }
            body = .file(out); temp = out
        case .stream:
            preconditionFailure("a stream is copied to a file before it is cut in parts")
        }
    }

    func done() { if let temp { try? FileManager.default.removeItem(at: temp) } }

    private static func copy(_ url: URL, offset: Int64, length: Int64, to out: URL) throws {
        guard FileManager.default.createFile(atPath: out.path, contents: nil) else { throw UploadSourceError() }
        let reader = try FileHandle(forReadingFrom: url), writer = try FileHandle(forWritingTo: out)
        defer { try? reader.close(); try? writer.close() }
        try reader.seek(toOffset: UInt64(offset))
        var left = length
        while left > 0 {
            guard let d = try reader.read(upToCount: Int(min(left, Int64(chunk)))), !d.isEmpty else { throw UploadSourceError() }
            try writer.write(contentsOf: d)
            left -= Int64(d.count)
        }
    }
}

/// One file sent in parts (POST start, PUT every part, POST finish). Each part is cut from the file when its turn comes: the file is never
/// held in memory, and a part that must be sent again is just cut again.
///
/// Failures: a part is tried again up to `backoff.count` times on a network error, a 5xx (not the 507 "no room"), a timeout or a proxy's
/// bare 404; after a broken connection the server is asked which parts it holds first (the part may have arrived). A 422 or 413 is never
/// tried again. When finish says a part is missing, those parts are sent once and finish is asked again. When anything fails or the Task
/// is cancelled the part-upload is deleted on the server (best effort), so it does not use up one of the three open ones.
///
/// Resuming after the app was closed is out of scope: the id is kept only in memory, and the server removes open part-uploads that were
/// left alone for 24 hours.
struct PartedUpload: Sendable {
    let calls: PartCalls
    /// The waits (ns) before the 1st, 2nd and 3rd new try of one part.
    let backoff: [UInt64]
    let directory: URL

    static let discardTimeout: UInt64 = 5_000_000_000

    /// The stored path. `progress` gets (bytes of the file sent, size) and never goes backwards (a part sent again does not undo it).
    func run(name: String, size: Int64, source: UploadSource, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String {
        let upload = try await calls.start(name: name, size: size)
        do {
            return try await transfer(upload, size: size, source: source, progress: progress)
        } catch {
            await discard(upload.id)
            throw error
        }
    }

    private func transfer(_ upload: PartUpload, size: Int64, source: UploadSource, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String {
        let sizes = UploadPlan.partSizes(size: size, part: upload.partSize)
        guard sizes.count == upload.parts else { throw ApiError(status: ApiError.noResponse, detail: "the server cut the file in \(upload.parts) parts, not \(sizes.count)") }
        let high = Highest(size: size, report: progress)
        high.at(0)
        let held = Held()

        func send(_ i: Int) async throws {
            let start = Int64(i) * upload.partSize
            var attempt = 0
            while true {
                let slice = try PartSlice(of: source, offset: start, length: sizes[i], directory: directory)
                defer { slice.done() }
                do {
                    try await calls.put(id: upload.id, index: i, body: slice.body) { sent, _ in high.at(start + min(sent, sizes[i])) }
                    held.add(i)
                    high.at(start + sizes[i])
                    return
                } catch let e as ApiError {
                    guard PartedUpload.retryable(e), attempt < backoff.count else { throw e }
                    try await Task.sleep(nanoseconds: backoff[attempt]); attempt += 1
                    if e.isNetwork {
                        // the connection broke: the part may have arrived anyway
                        if let now = try? await calls.received(id: upload.id) { held.add(now) }
                        if held.has(i) { high.at(start + sizes[i]); return }
                    }
                }
            }
        }

        for i in sizes.indices { try await send(i) }
        return try await finish(upload.id, parts: sizes.indices, send: send)
    }

    /// Finish; when it says a part is missing (422), the parts the server does not hold are sent once and finish is asked again.
    private func finish(_ id: String, parts: Range<Int>, send: (Int) async throws -> Void) async throws -> String {
        do {
            return try await finishOnce(id)
        } catch let e as ApiError where e.status == 422 {
            guard let now = try? await calls.received(id: id) else { throw e }
            let missing = parts.filter { !now.contains($0) }
            if missing.isEmpty { throw e }
            for i in missing { try await send(i) }
            return try await finishOnce(id)
        }
    }

    /// POST finish, tried again on a transient failure (a finish that did arrive and is asked again answers 404: that error is then shown).
    private func finishOnce(_ id: String) async throws -> String {
        var attempt = 0
        while true {
            do {
                return try await calls.finish(id: id)
            } catch let e as ApiError {
                guard PartedUpload.retryable(e), attempt < backoff.count else { throw e }
                try await Task.sleep(nanoseconds: backoff[attempt]); attempt += 1
            }
        }
    }

    /// DELETE the part-upload after a failure or a cancel: at most `discardTimeout`, and what goes wrong is ignored. Runs in a Task of its
    /// own (a cancelled Task cannot make calls).
    private func discard(_ id: String) async {
        let calls = self.calls
        await Task.detached {
            await withTaskGroup(of: Void.self) { group in
                group.addTask { try? await calls.cancel(id: id) }
                group.addTask { try? await Task.sleep(nanoseconds: PartedUpload.discardTimeout) }
                await group.next()
                group.cancelAll()
            }
        }.value
    }

    /// Worth sending the same bytes again: no answer, a server error (not 507), a timeout or rate limit, or a 404 that carries no words of
    /// the server's (a proxy's). The server's own 404 ("unknown upload") has words: the upload is gone, nothing to retry.
    static func retryable(_ e: ApiError) -> Bool { e.isTransient || (e.status == 404 && e.detail == "HTTP 404") }
}

/// Progress that never goes backwards.
private final class Highest: @unchecked Sendable {
    private let lock = NSLock()
    private var high: Int64 = -1
    private let size: Int64
    private let report: @Sendable (Int64, Int64) -> Void
    init(size: Int64, report: @escaping @Sendable (Int64, Int64) -> Void) { self.size = size; self.report = report }
    func at(_ sent: Int64) {
        let go: Bool = lock.withLock { if sent > high { high = sent; return true }; return false }
        if go { report(sent, size) }
    }
}

/// The parts the server is known to hold.
private final class Held: @unchecked Sendable {
    private let lock = NSLock()
    private var set = Set<Int>()
    func add(_ i: Int) { lock.withLock { _ = set.insert(i) } }
    func add(_ more: Set<Int>) { lock.withLock { set.formUnion(more) } }
    func has(_ i: Int) -> Bool { lock.withLock { set.contains(i) } }
}
