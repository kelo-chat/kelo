import Foundation

// The designed mail preview in the approval card (Android chat/MailPreview.kt, docs/mail-html.md): the card's `mail` part, the link
// rules the owner confirms before anything opens, the preview the server sends (GET /api/approvals/{id}/mail-preview), and the store
// that fetches it once per card. The HTML itself never travels in the approval event.

/// A designed mail's card part (server "mail"): where its preview is and what the owner should know.
public struct MailDesign: Equatable, Sendable {
    public var previewPath: String
    public var pictures: Int
    public var links: Int

    public init(previewPath: String, pictures: Int, links: Int) {
        self.previewPath = previewPath; self.pictures = pictures; self.links = links
    }

    /// nil unless the server says the mail is designed and its preview is under /api/approvals/ (never a link out).
    public static func parse(_ e: JSON?) -> MailDesign? {
        guard let o = e?.objectValue, o["designed"]?.boolValue == true, let path = o["preview"]?.stringValue,
              path.hasPrefix("/api/approvals/") else { return nil }
        let l = Lenient(.object(o))
        return MailDesign(previewPath: path, pictures: l.int("pictures"), links: l.int("links"))
    }
}

/// The preview the server sent: the sanitized mail, shown by a web view that loads and runs nothing. iPhone draws only the document
/// (the desktop app is the one that draws the blocks).
public struct MailPreview: Equatable, Sendable {
    public var html: String
    public var cut: Bool
    public var pictures: Int
    public var links: Int
}

public enum MailPreviewRules {
    private static let openable: Set<String> = ["http", "https", "mailto"]

    /// The address a tapped link may open outside the app after the owner confirms: http, https, mailto only (nil otherwise).
    public static func linkToOpen(_ url: String?) -> String? {
        guard let url, !url.isEmpty else { return nil }
        if url.unicodeScalars.contains(where: { $0.value < 0x21 || $0.value == 0x7f }) { return nil }
        guard let colon = url.firstIndex(of: ":") else { return nil }
        let scheme = url[..<colon].lowercased()
        guard openable.contains(scheme), url.index(after: colon) < url.endIndex else { return nil }
        return url
    }

    /// What the confirm dialog shows first: the host (or the mail address), in full. The whole address is shown below it.
    public static func linkLabel(_ url: String) -> String {
        guard let colon = url.firstIndex(of: ":") else { return url }
        let scheme = url[..<colon].lowercased()
        let rest = String(url[url.index(after: colon)...])
        if scheme == "mailto" { return String(rest.prefix { $0 != "?" }) }
        var host = rest
        if host.hasPrefix("//") { host.removeFirst(2) }
        host = String(host.prefix { $0 != "/" && $0 != "?" && $0 != "#" })
        if let at = host.lastIndex(of: "@") { host = String(host[host.index(after: at)...]) }
        return host.isEmpty ? url : host
    }

    /// The server's answer, or nil when it is not a preview (no html).
    public static func parse(_ json: JSON) -> MailPreview? {
        guard let o = json.objectValue, let html = o["html"]?.stringValue else { return nil }
        let l = Lenient(json)
        return MailPreview(html: html, cut: o["cut"]?.boolValue == true, pictures: l.int("pictures"), links: l.int("links"))
    }
}

/// The fetched previews by approval id, so an answered card keeps showing what the owner approved (the newest eight). A preview is
/// fetched once per card; a failure may be tried again. One store per app, used with the session's KeloApi.
public actor MailPreviewStore {
    public enum State: Equatable, Sendable {
        case loading
        case ready(MailPreview)
        /// 404: the card is no longer waiting and nothing was fetched before
        case gone
        /// 413: the preview is larger than the server shows
        case tooBig
        case failed
    }

    static let keep = 8
    private var states: [String: State] = [:]
    private var order: [String] = []   // least recently used first

    public init() {}

    /// What is known about a card's preview now, without a call.
    public func cached(_ approvalId: String) -> State? { states[approvalId] }

    /// The card's preview: fetched once (a ready, gone or too-big answer is kept), retried after a failure. Returns the state after the call.
    public func load(_ approvalId: String, path: String, api: KeloApi) async -> State {
        if let s = states[approvalId], s != .failed { return s }
        remember(approvalId, .loading)
        let result: State
        do {
            let json = try await api.request("GET", path)
            result = MailPreviewRules.parse(json).map { State.ready($0) } ?? .failed
        } catch let e as ApiError {
            switch e.status {
            case 404: result = .gone
            case 413: result = .tooBig
            default: result = .failed
            }
        } catch {
            result = .failed
        }
        remember(approvalId, result)
        return result
    }

    private func remember(_ approvalId: String, _ s: State) {
        order.removeAll { $0 == approvalId }
        order.append(approvalId)
        states[approvalId] = s
        while order.count > Self.keep {
            let old = order.removeFirst()
            states[old] = nil
        }
    }
}
