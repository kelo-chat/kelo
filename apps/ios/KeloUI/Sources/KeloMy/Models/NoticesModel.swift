import SwiftUI
import KeloCore
import KeloShared

/// One operator notice (GET /api/notices): unread only, newest first, already in the requested language.
public struct OperatorNotice: Decodable, Identifiable, Equatable, Sendable {
    public let id: String
    public let date: String
    public let level: String
    public let title: String
    public let body: String
    public let link: String?

    enum CodingKeys: String, CodingKey { case id, date, level, title, body, link }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""
        level = try c.decodeIfPresent(String.self, forKey: .level) ?? NoticesRules.info
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        body = try c.decodeIfPresent(String.self, forKey: .body) ?? ""
        link = try c.decodeIfPresent(String.self, forKey: .link)
    }
}

/// The answer of GET /api/notices: {"notices": [...]}.
public struct NoticesAnswer: Decodable, Sendable {
    public let notices: [OperatorNotice]
}

/// 마이 → 공지 (Android my/NoticesModel.kt): the rules of the notices area. Parsing the answer, which links open, the language asked for.
public enum NoticesRules {
    public static let info = "info"
    public static let important = "important"
    /// The only links the app opens (in the browser): https pages on Kelo's own site.
    public static let linkPrefix = "https://kelo.chat/"

    public static func isImportant(_ n: OperatorNotice) -> Bool { n.level == important }

    /// An id the app will put in a path: letters, digits, dot, underscore, dash (1 to 128 characters).
    public static func validID(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 128 && id.allSatisfy { ch in
            ch.isASCII && (ch.isLetter || ch.isNumber || ch == "." || ch == "_" || ch == "-")
        }
    }

    /// The notices with a usable id (the others are left out).
    public static func usable(_ list: [OperatorNotice]) -> [OperatorNotice] { list.filter { validID($0.id) } }

    /// The notices not read in this session (a server that still lists one after its read does not bring it back).
    public static func unread(_ list: [OperatorNotice], read: Set<String>) -> [OperatorNotice] { list.filter { !read.contains($0.id) } }

    /// The link button's address, or nil: only an https link on kelo.chat with no spaces is offered.
    public static func safeLink(_ link: String?) -> URL? {
        guard let t = link?.trimmingCharacters(in: .whitespaces), t.hasPrefix(linkPrefix), t.count > linkPrefix.count,
              !t.contains(where: { $0.isWhitespace }) else { return nil }
        return URL(string: t)
    }

    /// The language asked for: the saved pick, or the phone's language when the app follows the phone.
    public static func language(pick: String?, systemShown: String) -> String { Languages.override(pick) ?? systemShown }
}

/// 마이 → 공지: the operator's notices. Loaded when My opens; a notice stays while unread and goes when it is opened (its card goes at
/// once and POST /api/notices/{id}/read tells the server). An older server (404) or a failed read shows no notices.
@MainActor @Observable public final class NoticesModel {
    public private(set) var notices: [OperatorNotice] = []
    /// The notice open in the sheet (nil when none).
    public private(set) var opened: OperatorNotice?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let language: () -> String
    /// Read in this session: never shown again here, even while the server's list still has them.
    @ObservationIgnored private var read: Set<String> = []

    /// `language`: the code to ask for (the app's language, or the phone's when it follows the phone).
    public init(api: KeloApi, language: @escaping () -> String) {
        self.api = api
        self.language = language
    }

    public func load() async {
        let lang = language()
        do {
            let answer = try await api.get("/api/notices?lang=\(lang)", as: NoticesAnswer.self)
            notices = NoticesRules.unread(NoticesRules.usable(answer.notices), read: read)
        } catch is CancellationError {
            return
        } catch {
            notices = []   // an older server (404), or no answer: nothing shows
        }
    }

    /// The notice was opened: it is read, its card goes now and the server is told.
    public func open(_ n: OperatorNotice) {
        read.insert(n.id)
        notices.removeAll { $0.id == n.id }
        opened = n
        let id = n.id
        Task { try? await markRead(id) }
    }

    public func close() { opened = nil }

    /// POST /api/notices/{id}/read. A failure keeps the notice hidden in this session; the server keeps it unread.
    public func markRead(_ id: String) async throws {
        _ = try await api.request("POST", "/api/notices/\(id)/read")
    }
}
