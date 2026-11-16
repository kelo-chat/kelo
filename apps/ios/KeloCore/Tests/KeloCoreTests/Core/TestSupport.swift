import Foundation
@testable import KeloCore

/// A value shared between a test and the code it drives.
final class Box<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var v: T
    init(_ v: T) { self.v = v }
    var value: T {
        get { lock.withLock { v } }
        set { lock.withLock { v = newValue } }
    }
    func update(_ f: (inout T) -> Void) { lock.withLock { f(&v) } }
}

/// A scripted server for KeloApi: each request takes the next answer, and every request is kept. Nothing touches the network.
final class FakeTransport: HTTPTransport, @unchecked Sendable {
    enum Answer { case ok(Int, String), fail(Error) }
    let answers = Box<[Answer]>([])
    let requests = Box<[URLRequest]>([])

    init(_ answers: Answer...) { self.answers.value = answers }

    func next(_ r: URLRequest) throws -> (Int, Data) {
        requests.update { $0.append(r) }
        var a: Answer = .ok(200, "{}")
        answers.update { if !$0.isEmpty { a = $0.removeFirst() } }
        switch a {
        case .ok(let code, let body): return (code, Data(body.utf8))
        case .fail(let e): throw e
        }
    }

    func send(_ request: URLRequest) async throws -> (Int, Data) { try next(request) }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try next(request)
        return (code, AsyncThrowingStream { c in
            // in uneven pieces, as a network delivers them
            for b in data { c.yield(b) }
            c.finish()
        })
    }

    var last: URLRequest { requests.value.last! }
    var lastBody: String { String(decoding: last.httpBody ?? Data(), as: UTF8.self) }
}
