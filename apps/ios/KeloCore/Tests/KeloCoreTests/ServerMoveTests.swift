import Foundation
import Testing
@testable import KeloCore

/// "Change address" in My: the same server (the same pinned KE2E key) moves; another server, an unreachable one and bad text do not.
struct ServerMoveTests {
    /// Answers GET /api/e2e/info per host.
    struct Hosts: HTTPTransport {
        var answers: [String: (Int, Data)]
        func send(_ request: URLRequest) async throws -> (Int, Data) {
            guard let a = answers[request.url?.host ?? ""] else { throw URLError(.cannotConnectToHost) }
            return a
        }
        func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { throw URLError(.badURL) }
    }

    static func info(_ key: KE2EServerKey) -> (Int, Data) {
        (200, Data(#"{"v": 1, "kid": "\#(key.kid)", "pk": "\#(key.base64url)", "fingerprint": "\#(key.fingerprint)"}"#.utf8))
    }

    static let keyA = TestServer().key
    static let keyB = TestServer().key
    static func session(pin: Bool = true) -> Session {
        Session(serverUrl: "https://old.example.com", token: "tok-secret-1", e2e: pin ? KE2EPin(key: keyA, verified: true) : nil)
    }

    @Test func theSameServerAtANewAddressMovesKeepingTokenAndKey() async {
        let t = Hosts(answers: ["new.example.com": Self.info(Self.keyA)])
        let r = await ServerMove.check("https://new.example.com/", from: Self.session(), transport: t, debug: false)
        #expect(r == .moved(Session(serverUrl: "https://new.example.com", token: "tok-secret-1", e2e: KE2EPin(key: Self.keyA, verified: true))))
    }

    @Test func aDifferentServerIsRefused() async {
        let t = Hosts(answers: ["other.example.com": Self.info(Self.keyB), "plain.example.com": (404, Data("{}".utf8))])
        #expect(await ServerMove.check("https://other.example.com", from: Self.session(), transport: t, debug: false) == .otherServer)
        #expect(await ServerMove.check("https://plain.example.com", from: Self.session(), transport: t, debug: false) == .otherServer)
    }

    @Test func anUnreachableAddressIsRefused() async {
        let t = Hosts(answers: [:])
        #expect(await ServerMove.check("https://gone.example.com", from: Self.session(), transport: t, debug: false) == .unreachable)
    }

    @Test func anOlderServerWithNoKeyHasNoButton() async {
        #expect(!ServerMove.canMove(Self.session(pin: false)))
        #expect(ServerMove.canMove(Self.session()))
        let t = Hosts(answers: ["new.example.com": Self.info(Self.keyA)])
        #expect(await ServerMove.check("https://new.example.com", from: Self.session(pin: false), transport: t, debug: false) == .notSupported)
    }

    @Test func badTextAndTheCurrentAddressAreRefusedBeforeAsking() async {
        let t = Hosts(answers: [:])
        #expect(await ServerMove.check("not an address", from: Self.session(), transport: t, debug: false) == .invalid)
        #expect(await ServerMove.check("http://new.example.com", from: Self.session(), transport: t, debug: false) == .invalid)
        #expect(await ServerMove.check("https://OLD.example.com/", from: Self.session(), transport: t, debug: false) == .sameAddress)
    }
}
