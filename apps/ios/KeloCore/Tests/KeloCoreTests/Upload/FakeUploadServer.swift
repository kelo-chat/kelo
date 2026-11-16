import Foundation
@testable import KeloCore

/// The server's upload routes in memory (limits, single PUT, the five part routes), with switches to make things go wrong.
final class FakeUploadServer: HTTPTransport, @unchecked Sendable {
    enum Fault: Sendable {
        case status(Int, String?)
        case dropAfterReceiving
        case dropBeforeReceiving
    }

    private let lock = NSLock()
    /// the limits answer (a JSON text), or nil for an old server (404)
    var limits: String?
    private(set) var log: [String] = []
    private(set) var parts: [Int: (size: Int64, hash: UInt64)] = [:]
    private(set) var partBytes: [Int: Data] = [:]
    private(set) var singles: [Int64] = []
    private(set) var bodyKinds: [String] = []
    private(set) var started: Int64 = 0
    var keepBytes = true
    var startAnswer: (Int, String)?
    var putFault: @Sendable (Int, Int) -> Fault? = { _, _ in nil }
    var forgetAtFinish: Int?
    var finishFaults = 0
    var stallPart: Int?
    var partSize: Int64 = 10
    private var tries: [Int: Int] = [:]

    init(limits: String? = #"{"single_max": 10, "part_size": 10, "image_max": 1000}"#) { self.limits = limits }

    func count(_ prefix: String) -> Int { lock.withLock { log.filter { $0.hasPrefix(prefix) }.count } }
    func entries() -> [String] { lock.withLock { log } }
    func joined() -> Data { lock.withLock { parts.keys.sorted().reduce(Data()) { $0 + (partBytes[$1] ?? Data()) } } }
    func part(_ i: Int) -> (size: Int64, hash: UInt64)? { lock.withLock { parts[i] } }
    func started_() -> Int64 { lock.withLock { started } }

    static func fnv(_ data: Data, into h: inout UInt64) { for b in data { h = (h ^ UInt64(b)) &* 1_099_511_628_211 } }
    static let fnvStart: UInt64 = 14_695_981_039_346_656_037

    // ---- HTTPTransport
    func send(_ request: URLRequest) async throws -> (Int, Data) { let r = try await exchange(request); return (r.status, r.body) }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { throw URLError(.unsupportedURL) }

    func exchange(_ request: URLRequest) async throws -> HTTPReply {
        try await handle(request, body: request.httpBody.map { .data($0) } ?? .data(Data()))
    }

    func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)?) async throws -> HTTPReply {
        let n: Int64
        switch body {
        case .data(let d): n = Int64(d.count)
        case .file(let u): n = (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? 0
        }
        progress?(0, n)
        let reply = try await handle(request, body: body)
        progress?(n, n)
        return reply
    }

    private func json(_ code: Int, _ body: String) -> HTTPReply { HTTPReply(status: code, body: Data(body.utf8)) }

    private func read(_ body: HTTPUploadBody) throws -> (size: Int64, hash: UInt64, bytes: Data?, kind: String) {
        var h = Self.fnvStart
        switch body {
        case .data(let d): Self.fnv(d, into: &h); return (Int64(d.count), h, keepBytes ? d : nil, "data")
        case .file(let u):
            let r = try FileHandle(forReadingFrom: u); defer { try? r.close() }
            var total: Int64 = 0; var kept = Data()
            while let d = try r.read(upToCount: 1 << 20), !d.isEmpty { Self.fnv(d, into: &h); total += Int64(d.count); if keepBytes { kept.append(d) } }
            return (total, h, keepBytes ? kept : nil, "file")
        }
    }

    private func handle(_ r: URLRequest, body: HTTPUploadBody) async throws -> HTTPReply {
        let method = r.httpMethod ?? "GET"
        let path = r.url?.path(percentEncoded: true) ?? ""
        lock.withLock { log.append("\(method) \(path)") }
        if method == "GET", path == "/api/uploads/limits" { return limits.map { json(200, $0) } ?? json(404, #"{"detail":"Not Found"}"#) }
        if method == "POST", path.hasPrefix("/api/uploads/"), path.hasSuffix("/parts") {
            if let a = startAnswer { return json(a.0, a.1) }
            let text = String(decoding: r.httpBody ?? Data(), as: UTF8.self)
            let size = Int64(text.components(separatedBy: CharacterSet.decimalDigits.inverted).joined()) ?? 0
            lock.withLock { started = size }
            return json(200, #"{"upload_id":"u1","part_size":\#(partSize),"parts":\#((size + partSize - 1) / partSize)}"#)
        }
        if method == "PUT", path.hasPrefix("/api/uploads/parts/u1/") {
            let index = Int(path.split(separator: "/").last ?? "") ?? -1
            let nth: Int = lock.withLock { tries[index, default: 0] += 1; return tries[index]! }
            let got = try read(body)
            lock.withLock { bodyKinds.append(got.kind) }
            let total = lock.withLock { started }
            let count = (total + partSize - 1) / partSize
            let expected = Int64(index) < count - 1 ? partSize : total - partSize * Int64(index)
            switch putFault(index, nth) {
            case .status(let code, let detail)?: return json(code, detail.map { #"{"detail":"\#($0)"}"# } ?? "")
            case .dropBeforeReceiving?: throw URLError(.networkConnectionLost)
            case .dropAfterReceiving?: store(index, got); throw URLError(.networkConnectionLost)
            case nil: break
            }
            if lock.withLock({ stallPart }) == index { try await Task.sleep(nanoseconds: 60_000_000_000) }
            if got.size != expected { return json(422, #"{"detail":"part \#(index) must be \#(expected) bytes"}"#) }
            store(index, got)
            return json(200, #"{"ok":true,"received":\#(lock.withLock { parts.count })}"#)
        }
        if method == "GET", path == "/api/uploads/parts/u1" {
            let (have, total) = lock.withLock { (parts.keys.sorted(), started) }
            return json(200, #"{"name":"x","size":\#(total),"part_size":\#(partSize),"received":[\#(have.map(String.init).joined(separator: ","))]}"#)
        }
        if method == "POST", path == "/api/uploads/parts/u1/finish" {
            let faulty: Bool = lock.withLock { if finishFaults > 0 { finishFaults -= 1; return true }; return false }
            if faulty { return json(503, #"{"detail":"busy"}"#) }
            if let f = lock.withLock({ () -> Int? in defer { forgetAtFinish = nil }; return forgetAtFinish }) {
                lock.withLock { _ = parts.removeValue(forKey: f) }
                return json(422, #"{"detail":"part \#(f) is missing"}"#)
            }
            let (have, total) = lock.withLock { (Set(parts.keys), started) }
            let need = Int((total + partSize - 1) / partSize)
            if (0..<need).contains(where: { !have.contains($0) }) { return json(422, #"{"detail":"parts missing"}"#) }
            return json(200, #"{"path":"uploads/x","ok":true}"#)
        }
        if method == "DELETE", path == "/api/uploads/parts/u1" { return json(200, #"{"ok":true}"#) }
        if method == "PUT", path.hasPrefix("/api/uploads/") {
            let got = try read(body)
            lock.withLock { singles.append(got.size); bodyKinds.append(got.kind) }
            return json(200, #"{"path":"uploads/\#(path.split(separator: "/").last ?? "")","ok":true}"#)
        }
        return json(404, #"{"detail":"no route"}"#)
    }

    private func store(_ index: Int, _ got: (size: Int64, hash: UInt64, bytes: Data?, kind: String)) {
        lock.withLock { parts[index] = (got.size, got.hash); if let b = got.bytes { partBytes[index] = b } }
    }
}
