import Foundation
import Testing
@testable import KeloCore

@Suite struct FileUploaderTests {
    /// A directory of its own for the uploader's temp copies, and the server.
    struct Rig {
        let server: FakeUploadServer
        let uploader: FileUploader
        let dir: URL
        let api: KeloApi

        init(limits: String? = #"{"single_max": 10, "part_size": 10, "image_max": 1000}"#, configure: (FakeUploadServer) -> Void = { _ in }) {
            server = FakeUploadServer(limits: limits)
            configure(server)
            dir = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-upload-test-\(UUID().uuidString)")
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            api = KeloApi(baseUrl: "https://k", token: "t", transport: server, appHeader: "")
            uploader = FileUploader(api: api, limits: api.uploadLimits, backoff: [0, 0, 0], directory: dir)
        }

        var leftovers: [String] { ((try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []).filter { !$0.hasPrefix("source-") } }
        func cleanup() { try? FileManager.default.removeItem(at: dir) }

        func file(_ bytes: Data, name: String = "source-\(UUID().uuidString)") -> UploadSource {
            let u = dir.appendingPathComponent(name)
            try? bytes.write(to: u)
            return .file(u)
        }
    }

    static func bytes(_ n: Int) -> Data { Data((0..<n).map { UInt8(truncatingIfNeeded: $0 * 7 + 3) }) }

    @Test func aSmallFileGoesInOneRequest() async throws {
        let r = Rig(); defer { r.cleanup() }
        let path = try await r.uploader.upload(name: "a.pdf", source: .data(Self.bytes(5)), type: "application/pdf")
        #expect(path == "uploads/a.pdf")
        #expect(r.server.entries().filter { $0.hasPrefix("PUT") } == ["PUT /api/uploads/a.pdf"])
        #expect(r.server.count("POST") == 0)
    }

    @Test func noLimitOnThisRouteMeansOneRequestWhateverTheSize() async throws {
        let r = Rig(limits: #"{"single_max": null, "part_size": 10, "image_max": 1000}"#); defer { r.cleanup() }
        _ = try await r.uploader.upload(name: "big.mp4", source: r.file(Self.bytes(500)), type: "video/mp4")
        #expect(r.server.count("PUT /api/uploads/big.mp4") == 1)
        #expect(r.server.count("POST") == 0)
    }

    final class Seen: @unchecked Sendable {
        private let lock = NSLock(); private var items: [(Int64, Int64)] = []
        func add(_ a: Int64, _ b: Int64) { lock.withLock { items.append((a, b)) } }
        var all: [(Int64, Int64)] { lock.withLock { items } }
    }

    @Test func partsGoInOrderWithTheExactSizesThenFinish() async throws {
        let r = Rig(); defer { r.cleanup() }
        let data = Self.bytes(25)
        let seen = Seen()
        let path = try await r.uploader.upload(name: "v.mp4", source: r.file(data), type: "video/mp4") { seen.add($0, $1) }
        #expect(path == "uploads/x")
        #expect(r.server.entries() == ["GET /api/uploads/limits", "POST /api/uploads/v.mp4/parts", "PUT /api/uploads/parts/u1/0", "PUT /api/uploads/parts/u1/1",
                                       "PUT /api/uploads/parts/u1/2", "POST /api/uploads/parts/u1/finish"])
        #expect((0..<3).map { r.server.part($0)?.size ?? -1 } == [10, 10, 5])
        #expect(r.server.joined() == data)
        #expect(seen.all.first.map { [$0.0, $0.1] } == [0, 25] && seen.all.last.map { [$0.0, $0.1] } == [25, 25])
        #expect(zip(seen.all, seen.all.dropFirst()).allSatisfy { $0.0 <= $1.0 })
        #expect(r.leftovers.isEmpty, "the part copies are gone")
    }

    @Test func aPartThatFailsTwiceIsSentAgainAndTheFileStillArrivesWhole() async throws {
        let r = Rig { $0.putFault = { i, nth in i == 1 && nth <= 2 ? .status(503, "oops") : nil } }; defer { r.cleanup() }
        let data = Self.bytes(25)
        _ = try await r.uploader.upload(name: "v.mp4", source: r.file(data), type: "video/mp4")
        #expect(r.server.count("PUT /api/uploads/parts/u1/1") == 3)
        #expect(r.server.joined() == data)
        #expect(r.server.count("DELETE") == 0)
        #expect(r.leftovers.isEmpty)
    }

    @Test func aPartThatKeepsFailingGivesUpAfterThreeNewTriesAndCancelsTheUpload() async {
        let r = Rig { $0.putFault = { i, _ in i == 1 ? .status(500, "broken") : nil } }; defer { r.cleanup() }
        do { _ = try await r.uploader.upload(name: "v.mp4", source: r.file(Self.bytes(25)), type: "video/mp4"); Issue.record("no error") }
        catch let e as ApiError { #expect(e.status == 500) } catch { Issue.record("\(error)") }
        #expect(r.server.count("PUT /api/uploads/parts/u1/1") == 4)
        #expect(r.server.count("DELETE /api/uploads/parts/u1") == 1)
        #expect(r.leftovers.isEmpty)
    }

    @Test func aWrongSizeAnswerIsNeverSentAgain() async {
        let r = Rig { $0.putFault = { i, _ in i == 0 ? .status(422, "wrong size") : nil } }; defer { r.cleanup() }
        do { _ = try await r.uploader.upload(name: "v.mp4", source: r.file(Self.bytes(25)), type: "video/mp4"); Issue.record("no error") }
        catch let e as ApiError { #expect(e.status == 422 && e.detail == "wrong size") } catch { Issue.record("\(error)") }
        #expect(r.server.count("PUT /api/uploads/parts/u1/0") == 1)
    }

    @Test func aBrokenConnectionAsksWhatArrivedAndSkipsThePartsTheServerHolds() async throws {
        let r = Rig { $0.putFault = { i, nth in i == 1 && nth == 1 ? .dropAfterReceiving : nil } }; defer { r.cleanup() }
        let data = Self.bytes(25)
        _ = try await r.uploader.upload(name: "v.mp4", source: r.file(data), type: "video/mp4")
        #expect(r.server.count("PUT /api/uploads/parts/u1/1") == 1, "not sent a second time")
        #expect(r.server.count("GET /api/uploads/parts/u1") == 1)
        #expect(r.server.joined() == data)
    }

    @Test func aBrokenConnectionBeforeThePartArrivedSendsItAgain() async throws {
        let r = Rig { $0.putFault = { i, nth in i == 2 && nth == 1 ? .dropBeforeReceiving : nil } }; defer { r.cleanup() }
        let data = Self.bytes(25)
        _ = try await r.uploader.upload(name: "v.mp4", source: r.file(data), type: "video/mp4")
        #expect(r.server.count("PUT /api/uploads/parts/u1/2") == 2)
        #expect(r.server.count("GET /api/uploads/parts/u1") == 1)
        #expect(r.server.joined() == data)
    }

    @Test func aFinishThatSaysAPartIsMissingGetsItOnceMoreAndThenFinishesAgain() async throws {
        let r = Rig { $0.forgetAtFinish = 1 }; defer { r.cleanup() }
        let data = Self.bytes(25)
        let path = try await r.uploader.upload(name: "v.mp4", source: r.file(data), type: "video/mp4")
        #expect(path == "uploads/x")
        #expect(r.server.count("PUT /api/uploads/parts/u1/1") == 2)
        #expect(r.server.count("PUT /api/uploads/parts/u1/0") == 1)
        #expect(r.server.count("POST /api/uploads/parts/u1/finish") == 2)
        #expect(r.server.joined() == data)
    }

    @Test func finishItselfIsTriedAgainOnAServerError() async throws {
        let r = Rig { $0.finishFaults = 1 }; defer { r.cleanup() }
        let path = try await r.uploader.upload(name: "v.mp4", source: r.file(Self.bytes(25)), type: "video/mp4")
        #expect(path == "uploads/x")
        #expect(r.server.count("POST /api/uploads/parts/u1/finish") == 2)
    }

    @Test func cancellingDeletesThePartUpload() async throws {
        let r = Rig { $0.stallPart = 1 }; defer { r.cleanup() }   // part 1 never gets an answer
        let source = r.file(Self.bytes(25))
        let task = Task { try await r.uploader.upload(name: "v.mp4", source: source, type: "video/mp4") }
        for _ in 0..<500 where r.server.count("PUT /api/uploads/parts/u1/1") == 0 { try await Task.sleep(nanoseconds: 20_000_000) }
        task.cancel()
        _ = try? await task.value
        #expect(r.server.count("DELETE /api/uploads/parts/u1") == 1)
        #expect(r.leftovers.isEmpty)
    }

    @Test func noRoomOnTheServerShowsTheServersOwnWords() async {
        let r = Rig { $0.startAnswer = (507, #"{"detail":"There is not enough room on the server for this file."}"#) }; defer { r.cleanup() }
        do { _ = try await r.uploader.upload(name: "v.mp4", source: r.file(Self.bytes(25)), type: "video/mp4"); Issue.record("no error") }
        catch let e as ApiError { #expect(e.status == 507 && e.detail == "There is not enough room on the server for this file.") } catch { Issue.record("\(error)") }
        #expect(r.server.count("PUT") == 0)
    }

    @Test func noRoomInTheMiddleIsNotTriedAgain() async {
        let r = Rig { $0.putFault = { i, _ in i == 1 ? .status(507, "Storage is full") : nil } }; defer { r.cleanup() }
        do { _ = try await r.uploader.upload(name: "v.mp4", source: r.file(Self.bytes(25)), type: "video/mp4"); Issue.record("no error") }
        catch let e as ApiError { #expect(e.detail == "Storage is full") } catch { Issue.record("\(error)") }
        #expect(r.server.count("PUT /api/uploads/parts/u1/1") == 1)
        #expect(r.server.count("DELETE") == 1)
    }

    @Test func tooManyOpenLargeUploadsShowsTheServersWords() async {
        let r = Rig { $0.startAnswer = (409, #"{"detail":"Three large uploads are open. Wait for one to finish."}"#) }; defer { r.cleanup() }
        do { _ = try await r.uploader.upload(name: "v.mp4", source: r.file(Self.bytes(25)), type: "video/mp4"); Issue.record("no error") }
        catch let e as ApiError { #expect(e.status == 409 && e.detail == "Three large uploads are open. Wait for one to finish.") } catch { Issue.record("\(error)") }
    }

    @Test func anOldServerRefusesAFileOverItsOneRequestLimitOnThePhone() async {
        let r = Rig(limits: nil); defer { r.cleanup() }   // 404
        do {
            _ = try await r.uploader.upload(name: "big.mp4", source: .stream(size: 150_000_000, open: { throw UploadSourceError() }), type: "video/mp4")
            Issue.record("no error")
        } catch let e as UploadTooLarge { #expect(e.limit == 99_000_000) } catch { Issue.record("\(error)") }
        #expect(r.server.entries() == ["GET /api/uploads/limits"])
    }

    @Test func aPhotoOverTheImageLimitIsRefusedBeforeAnythingIsSent() async {
        let r = Rig(); defer { r.cleanup() }
        do { _ = try await r.uploader.upload(name: "p.jpg", source: .data(Data(count: 1001)), type: "image/jpeg"); Issue.record("no error") }
        catch let e as UploadTooLarge { #expect(e.limit == 1000) } catch { Issue.record("\(error)") }
        #expect(r.server.entries().count == 1)
    }

    @Test func aStreamOfUnknownSizeIsCopiedToATempFileForItsSizeAndTheCopyIsDeleted() async throws {
        let r = Rig(); defer { r.cleanup() }
        let data = Self.bytes(25)
        let during = Seen()
        let path = try await r.uploader.upload(name: "v.mp4", source: .stream(size: -1, open: { InputStream(data: data) }), type: "video/mp4") { _, _ in
            during.add(Int64(r.leftovers.filter { $0.hasPrefix("kelo-upload-") }.count), 0)
        }
        #expect(path == "uploads/x")
        #expect(r.server.joined() == data)
        #expect(r.server.started_() == 25)
        #expect(!during.all.isEmpty && during.all.allSatisfy { $0.0 == 1 }, "the copy exists while it goes up")
        #expect(r.leftovers.isEmpty, "and is gone afterwards")
    }

    @Test func aStreamOfUnknownSizeThatFailsAlsoLeavesNoCopy() async {
        let r = Rig { $0.startAnswer = (409, #"{"detail":"busy"}"#) }; defer { r.cleanup() }
        let data = Self.bytes(25)
        do { _ = try await r.uploader.upload(name: "v.mp4", source: .stream(size: -1, open: { InputStream(data: data) }), type: "video/mp4"); Issue.record("no error") }
        catch is ApiError {} catch { Issue.record("\(error)") }
        #expect(r.leftovers.isEmpty)
    }

    final class Generated: @unchecked Sendable {
        let size: Int64
        private let lock = NSLock(); private var _opened = 0, _biggest = 0, _served: Int64 = 0
        init(_ size: Int64) { self.size = size }
        var opened: Int { lock.withLock { _opened } }
        var biggestRead: Int { lock.withLock { _biggest } }
        var served: Int64 { lock.withLock { _served } }
        static func byte(_ pos: Int64) -> UInt8 { UInt8(truncatingIfNeeded: (pos &* 31) &+ (pos >> 8)) }

        func open() -> InputStream {
            lock.withLock { _opened += 1 }
            // a stream that makes its bytes as they are read (nothing is kept)
            let s = GeneratingStream(owner: self)
            return s
        }
        fileprivate func note(_ len: Int, _ n: Int64) { lock.withLock { _biggest = max(_biggest, len); _served += n } }
    }

    final class GeneratingStream: InputStream, @unchecked Sendable {
        private let owner: Generated
        private var at: Int64 = 0
        private var state: Stream.Status = .notOpen
        fileprivate init(owner: Generated) { self.owner = owner; super.init(data: Data()) }
        override func open() { state = .open }
        override func close() { state = .closed }
        override var streamStatus: Stream.Status { state }
        override var hasBytesAvailable: Bool { at < owner.size }
        override func read(_ buffer: UnsafeMutablePointer<UInt8>, maxLength len: Int) -> Int {
            if at >= owner.size { return 0 }
            let n = Int(min(Int64(len), owner.size - at))
            for i in 0..<n { buffer[i] = Generated.byte(at + Int64(i)) }
            at += Int64(n)
            owner.note(len, Int64(n))
            return n
        }
        override func getBuffer(_ buffer: UnsafeMutablePointer<UnsafeMutablePointer<UInt8>?>, length len: UnsafeMutablePointer<Int>) -> Bool { false }
    }

    @Test func aStreamOfUnknownSizeOnAnOldServerIsRefusedOnceItIsKnownToBeOver() async {
        let r = Rig(limits: nil); defer { r.cleanup() }
        let g = Generated(150_000_000)
        do { _ = try await r.uploader.upload(name: "v.mp4", source: .stream(size: -1, open: { g.open() }), type: "video/mp4"); Issue.record("no error") }
        catch let e as UploadTooLarge { #expect(e.limit == 99_000_000) } catch { Issue.record("\(error)") }
        #expect(g.served <= 99_000_000 + 1_048_576, "stopped soon after the limit, not after 150 MB (\(g.served))")
        #expect(r.leftovers.isEmpty)
    }

    @Test func aSmallStreamOfUnknownSizeOnAnOldServerStillGoesUp() async throws {
        let r = Rig(limits: nil); defer { r.cleanup() }
        let data = Self.bytes(7)
        let path = try await r.uploader.upload(name: "a.pdf", source: .stream(size: -1, open: { InputStream(data: data) }), type: "application/pdf")
        #expect(path == "uploads/a.pdf")
        #expect(r.server.count("PUT /api/uploads/a.pdf") == 1)
    }

    @Test func aBigFileIsNeverHeldInMemoryWhileItGoesUpInParts() async throws {
        let total: Int64 = 50_000_000
        let r = Rig(limits: #"{"single_max": 5000000, "part_size": 10000000, "image_max": 15000000}"#) { $0.partSize = 10_000_000; $0.keepBytes = false }
        defer { r.cleanup() }
        let g = Generated(total)
        let path = try await r.uploader.upload(name: "movie.mp4", source: .stream(size: total, open: { g.open() }), type: "video/mp4")
        #expect(path == "uploads/x")
        #expect(g.opened == 1, "read once, a chunk at a time, into a temp file")
        #expect(g.biggestRead <= 1_048_576, "no read asked for more than a chunk (\(g.biggestRead))")
        #expect(r.server.bodyKinds.allSatisfy { $0 == "file" }, "every part went up from a file, none from memory")
        for i in 0..<5 {
            var h = FakeUploadServer.fnvStart
            let from = Int64(i) * 10_000_000
            var at = from
            while at < from + 10_000_000 {
                let n = Int(min(65_536, from + 10_000_000 - at))
                FakeUploadServer.fnv(Data((0..<n).map { Generated.byte(at + Int64($0)) }), into: &h)
                at += Int64(n)
            }
            let got = r.server.part(i)
            #expect(got?.size == 10_000_000 && got?.hash == h, "part \(i)")
        }
        #expect(r.leftovers.isEmpty)
    }
}

@Suite struct UploadLimitsSourceTests {
    final class Answers: HTTPTransport, @unchecked Sendable {
        enum Answer { case ok(Int, String), drop }
        private let lock = NSLock(); private var queue: [Answer]; private(set) var calls = 0
        init(_ q: [Answer]) { queue = q }
        func send(_ request: URLRequest) async throws -> (Int, Data) { let r = try await exchange(request); return (r.status, r.body) }
        func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { throw URLError(.unsupportedURL) }
        func exchange(_ request: URLRequest) async throws -> HTTPReply {
            let a: Answer = lock.withLock { calls += 1; return queue.isEmpty ? .drop : queue.removeFirst() }
            switch a {
            case .ok(let code, let body): return HTTPReply(status: code, body: Data(body.utf8))
            case .drop: throw URLError(.networkConnectionLost)
            }
        }
    }

    func source(_ t: Answers) -> (UploadLimitsSource, KeloApi) {
        let api = KeloApi(baseUrl: "https://k", token: "t", transport: t, appHeader: "")
        return (api.uploadLimits, api)
    }

    @Test func theAnswerIsAskedOnceAndKept() async {
        let t = Answers([.ok(200, #"{"single_max":99000000,"part_size":90000000,"image_max":15000000}"#)])
        let (s, api) = source(t); withExtendedLifetime(api) {}
        let want = UploadLimits(singleMax: 99_000_000, partSize: 90_000_000, imageMax: 15_000_000)
        #expect(await s.get() == want)
        #expect(await s.get() == want)
        #expect(t.calls == 1)
    }

    @Test func anOldServerGivesLegacyAndIsNotAskedAgain() async {
        let t = Answers([.ok(404, #"{"detail":"Not Found"}"#)])
        let (s, api) = source(t); withExtendedLifetime(api) {}
        #expect(await s.get() == .legacy)
        #expect(await s.get() == .legacy)
        #expect(t.calls == 1)
    }

    @Test func anUnreadableAnswerGivesLegacyAndIsKept() async {
        let t = Answers([.ok(200, "<html>captive portal</html>")])
        let (s, api) = source(t); withExtendedLifetime(api) {}
        #expect(await s.get() == .legacy)
        #expect(await s.get() == .legacy)
        #expect(t.calls == 1)
    }

    @Test func aNetworkErrorGivesLegacyForThisUploadOnly() async {
        let t = Answers([.drop, .ok(200, #"{"single_max":null,"part_size":90000000,"image_max":15000000}"#)])
        let (s, api) = source(t); withExtendedLifetime(api) {}
        #expect(await s.get() == .legacy)
        #expect(await s.get() == UploadLimits(singleMax: nil, partSize: 90_000_000, imageMax: 15_000_000))
        #expect(t.calls == 2)
    }

    @Test func aServerErrorIsNotKeptEither() async {
        let t = Answers([.ok(503, ""), .ok(200, #"{"single_max":99000000,"part_size":90000000,"image_max":15000000}"#)])
        let (s, api) = source(t); withExtendedLifetime(api) {}
        #expect(await s.get() == .legacy)
        #expect(await s.get().partSize == 90_000_000)
    }

    @Test func eachApiAsksForItself() async {
        let t = Answers([.ok(200, #"{"single_max":null,"part_size":1,"image_max":2}"#), .ok(200, #"{"single_max":null,"part_size":3,"image_max":4}"#)])
        let a = KeloApi(baseUrl: "https://k", token: "t", transport: t, appHeader: "")
        let b = KeloApi(baseUrl: "https://k", token: "t", transport: t, appHeader: "")
        #expect(await a.uploadLimits.get().partSize == 1)
        #expect(await b.uploadLimits.get().partSize == 3)
        #expect(await a.uploadLimits.get().partSize == 1)
    }
}
