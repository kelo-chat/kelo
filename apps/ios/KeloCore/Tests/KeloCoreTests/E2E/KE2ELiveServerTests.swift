import Foundation
import Testing
@testable import KeloCore

/// The app's KE2E client against a real Kelo server process, through a recording proxy (apps/ios/tools/e2e_local.py starts both, runs
/// this suite with KELO_E2E_LIVE=<settings file>, then checks the proxy's record: only /e2e, /e2e/ws and /api/e2e/info crossed,
/// and none of the secrets below). Skipped when the variable is not set.
enum LiveServer {
    static var settings: JSON? {
        guard let p = ProcessInfo.processInfo.environment["KELO_E2E_LIVE"], !p.isEmpty,
              let d = FileManager.default.contents(atPath: p) else { return nil }
        return try? JSON.parse(d)
    }
}

@Suite(.serialized, .enabled(if: LiveServer.settings != nil, "KELO_E2E_LIVE not set (run apps/ios/tools/e2e_local.py)"))
struct KE2ELiveServerTests {
    let s: JSON
    init() throws { s = try #require(LiveServer.settings) }

    func text(_ k: String) throws -> String { try #require(s[k]?.stringValue, "missing \(k)") }

    @Test func pairTalkUploadDownloadAndLiveView() async throws {
        // a session-free URLSession, as the app has
        let transport = URLSessionTransport()

        // 1. the pairing link carries the key: pinned verified, the pairing call already sealed
        let link = try #require(PairingLinks.parse(try text("link")))
        #expect(link.serverKey != nil)
        let session = try await PairingClient(transport: transport).pair(link, deviceName: "iPhone e2e test")
        let pin = try #require(session.e2e)
        let fingerprint = try text("fingerprint")
        #expect(pin.verified && pin.key.fingerprint == fingerprint)

        let versions = Box<[String?]>([])
        let state = KE2EState(pin: pin)
        let api = KeloApi(session: session, transport: KE2ETransport(base: transport, baseUrl: session.serverUrl, state: state),
                          onServerVersion: { v in versions.update { $0.append(v) } })

        // 2. an ordinary call
        let _: Profile = try await api.get("/api/profile")
        #expect(versions.value.last??.isEmpty == false)

        // 3. a streamed reply (server-sent events, frame by frame)
        var said = ""
        let reply = try text("reply")
        #expect(said.contains(reply))

        // 4. a file up (0x02 streamed request) and back (a whole answer, then a range as the video player asks)
        let content = Data(("%PDF-1.4\n% " + (try text("file_marker")) + "\n" + String(repeating: "k", count: 300_000)).utf8)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("ke2e-live-\(UUID().uuidString).pdf")
        try content.write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }
        let sent = Box<Int64>(0)
        _ = try await api.upload(name: "e2e-live.pdf", body: .file(file), contentType: "application/pdf", progress: { n, _ in sent.value = n })
        #expect(sent.value > Int64(content.count))
        let back = try await api.download("/api/uploads/e2e-live.pdf/file")
        #expect(back == content)
        var ranged = try api.makeRequest("/api/uploads/e2e-live.pdf/file?inline=1")
        ranged.setValue("bytes=10-19", forHTTPHeaderField: "Range")
        let part = try await api.sendReply(ranged)
        #expect(part.status == 206 && part.body == content.subdata(in: 10..<20))
        #expect(part.headers["content-range"]?.hasSuffix("/\(content.count)") == true)

        // 5. the live view through /e2e/ws: the sealed head opens (101), the server's sealed refusal of a bad ticket arrives as a
        // typed text message, then its close code
        let socket = LiveSocket(api: api, tabId: "owner", session: URLSession(configuration: .ephemeral))
        var messages: [LiveIn] = []
        var closed: Int?
        do {
            for try await ev in socket.connect(ticket: "not-a-ticket", quality: .wifi) { if case .message(let m) = ev { messages.append(m) } }
        } catch let c as LiveClosed { closed = c.code }
        #expect(closed == 4401)
        #expect(!messages.isEmpty)

        // the token for the record check (a throwaway token of this test server)
        if let out = s["out"]?.stringValue {
            try JSON.object(["token": .string(session.token)]).compactString.write(toFile: out, atomically: true, encoding: .utf8)
        }
    }
}
