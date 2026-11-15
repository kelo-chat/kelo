import Testing
import Foundation
import KeloCore
@testable import KeloShared
@testable import KeloWork

/// A server with two archived schedules; DELETE /api/schedules/archive/{id} removes one. Every request is kept.
private final class ArchiveTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var ids: [Int64] = [7, 8]
    private var log: [String] = []
    var deletes: [String] { lock.withLock { log.filter { $0.hasPrefix("DELETE ") } } }
    var left: [Int64] { lock.withLock { ids } }

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        let path = request.url!.path, method = request.httpMethod ?? "GET"
        lock.withLock { log.append("\(method) \(path)") }
        if method == "GET" && path == "/api/schedules/archive" {
            let items = lock.withLock { ids }.map { #"{"id":\#($0),"name":"s\#($0)","prompt":"p","kind":"cron","cron":"0 9 * * *","enabled":false}"# }
            return (200, Data(#"{"items":[\#(items.joined(separator: ","))],"keep_days":30}"#.utf8))
        }
        if method == "DELETE", path.hasPrefix("/api/schedules/archive/"), let id = Int64(path.split(separator: "/").last ?? "") {
            lock.withLock { ids.removeAll { $0 == id } }
            return (200, Data(#"{"ok":true}"#.utf8))
        }
        return (404, Data(#"{"detail":"no"}"#.utf8))
    }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try await send(request)
        return (code, AsyncThrowingStream { c in for b in data { c.yield(b) }; c.finish() })
    }
}

/// 보관한 예약: a swiped-away schedule must be deleted on the server even when the page is left before the undo message ends,
/// and must not come back when the archive is opened again.
@Suite(.serialized) struct ArchiveEraseTests {
    @MainActor private func until(_ cond: () -> Bool) async -> Bool {
        for _ in 0..<300 { if cond() { return true }; try? await Task.sleep(nanoseconds: 10_000_000) }
        return cond()
    }

    @MainActor @Test func leavingThePageAtOnceStillDeletes() async {
        Strings.shared.use("ko")
        let t = ArchiveTransport()
        let api = KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t)
        let m = WorkModel(api: { api })
        m.loadArchive()
        #expect(await until { m.archive.data?.items.count == 2 })
        m.deleteArchived(7)
        #expect(m.archive.data?.items.map(\.id) == [8])           // hidden at once
        #expect(t.deletes.isEmpty)                                // waits for the undo message
        // the reload while it waits keeps it hidden
        m.loadArchive()
        try? await Task.sleep(nanoseconds: 100_000_000)
        #expect(m.archive.data?.items.map(\.id) == [8])
        m.commitArchivedErases()                                  // the page is left within a second
        #expect(await until { t.deletes == ["DELETE /api/schedules/archive/7"] })
        #expect(m.toast == nil)                                   // no 실행 취소 left for a delete already sent
        m.loadArchive()                                           // back on the page
        #expect(await until { m.archive.data?.items.map(\.id) == [8] && !m.archive.loading })
        #expect(t.left == [8])
    }

    @MainActor @Test func undoKeepsItAndSendsNothing() async {
        Strings.shared.use("ko")
        let t = ArchiveTransport()
        let api = KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t)
        let m = WorkModel(api: { api })
        m.loadArchive()
        #expect(await until { m.archive.data?.items.count == 2 })
        m.deleteArchived(8)
        m.undoEraseArchived(8)
        m.commitArchivedErases()
        #expect(await until { m.archive.data?.items.count == 2 })
        try? await Task.sleep(nanoseconds: 200_000_000)
        #expect(t.deletes.isEmpty)
    }
}
