import Foundation

/// How a "change server address" attempt ended (Android core/Sessions.kt AddressMove).
public enum AddressMove: Equatable, Sendable {
    case moved(Session)
    case sameAddress, invalid, unreachable, otherServer, notSupported
}

/// My -> Server address -> Change address: the same Kelo server at another address. The server is identified by its KE2E key: the new
/// address must answer GET /api/e2e/info (plaintext, no sign-in) with exactly the key this phone pinned at pairing. Only then the
/// session moves, keeping the device token and the key (nothing is paired again). Another key, or no key, is another server.
/// A phone without a pinned key (an older server) cannot tell servers apart: `canMove` is false and the button is hidden.
public enum ServerMove {
    public static func canMove(_ session: Session?) -> Bool { session?.e2e != nil }

    public static func check(_ raw: String, from session: Session, transport: HTTPTransport, debug: Bool = ServerAddress.isDebugBuild,
                             appHeader: String = Versioning.currentAppHeader) async -> AddressMove {
        guard let pin = session.e2e else { return .notSupported }
        guard let address = ServerAddress.normalize(raw), ServerAddress.isAllowed(address, debug: debug) else { return .invalid }
        var current = session.serverUrl
        while current.hasSuffix("/") { current.removeLast() }
        if address.lowercased() == current.lowercased() { return .sameAddress }
        guard let url = URL(string: address + KE2E.infoPath) else { return .invalid }
        var r = URLRequest(url: url)
        if !appHeader.isEmpty { r.setValue(appHeader, forHTTPHeaderField: Versioning.appHeader) }
        let reply: HTTPReply
        do { reply = try await transport.exchange(r) } catch { return .unreachable }
        if reply.status >= 500 { return .unreachable }
        guard (200...299).contains(reply.status), let json = try? JSON.parse(reply.body), let key = try? KE2EInfo.parse(json) else { return .otherServer }
        guard key == pin.key else { return .otherServer }
        return .moved(Session(serverUrl: address, token: session.token, e2e: pin))
    }
}
