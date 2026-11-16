import Foundation
import KeloCore
import KeloShared

// Kelo's own files (Android my/KeloFiles.kt; the server's api/files.py "FILE MANAGER").

/// One row of GET /api/files?folders=1.
public struct HomeEntry: Decodable, Equatable, Hashable, Sendable {
    public var path: String
    public var type = "file"
    public var bytes: Int64?
    public var writable = false

    public init(path: String, type: String = "file", bytes: Int64? = nil, writable: Bool = false) {
        self.path = path; self.type = type; self.bytes = bytes; self.writable = writable
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        path = try j.require("path", j.optStr("path"))
        type = j.str("type", "file"); bytes = j.optInt64("bytes"); writable = j.bool("writable")
    }
    public var isDir: Bool { type == "dir" }
    public var name: String {
        let p = path.hasSuffix("/") ? String(path.dropLast()) : path
        return p.split(separator: "/").last.map(String.init) ?? p
    }
}

/// A file in the trash (GET /api/home-trash); kept until `purgeAt` (unix seconds).
public struct HomeTrashed: Decodable, Equatable, Hashable, Sendable, Identifiable {
    public var id: String
    public var path: String
    public var bytes: Int64?
    public var deletedAt: Double = 0
    public var type = "file"
    public var purgeAt: Double = 0

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id")); path = j.str("path"); bytes = j.optInt64("bytes")
        deletedAt = j.double("deleted_at"); type = j.str("type", "file"); purgeAt = j.double("purge_at")
    }
    /// Whole days left, at least 0.
    public func daysLeft(now: Date = Date()) -> Int { max(0, Int(((purgeAt - now.timeIntervalSince1970) / 86400).rounded(.up))) }
}

/// GET /api/files/{path}.
public struct HomeFile: Decodable, Equatable, Sendable {
    public var path: String
    public var content = ""
    public init(path: String, content: String = "") { self.path = path; self.content = content }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        path = try j.require("path", j.optStr("path")); content = j.str("content")
    }
}

/// One row of GET /api/skills; only the custom ones (source "custom", written on this server) are shown in Kelo's files. `state` "held" =
/// not approved as it is now (shown as "Waiting"). Android my/KeloFiles.kt CustomSkillRow.
public struct CustomSkillRow: Decodable, Equatable, Hashable, Sendable, Identifiable {
    public var id: String
    public var name = ""
    public var description = ""
    public var source = ""
    public var state = "ready"
    public var createdAt: Double?

    public init(id: String, name: String = "", description: String = "", source: String = "custom", state: String = "ready", createdAt: Double? = nil) {
        self.id = id; self.name = name; self.description = description; self.source = source; self.state = state; self.createdAt = createdAt
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id")); name = j.str("name"); description = j.str("description")
        source = j.str("source"); state = j.str("state", "ready"); createdAt = j.optDouble("created_at")
    }
    public var held: Bool { state == "held" }
    public var title: String { name.isEmpty ? id : name }

    /// The custom skills for the list: newest first by created_at when every row has it, else by name. Built-in and Nest-installed ones never.
    public static func forList(_ items: [CustomSkillRow]) -> [CustomSkillRow] {
        let custom = items.filter { $0.source == "custom" }
        if custom.allSatisfy({ $0.createdAt != nil }) { return custom.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) } }
        return custom.sorted { $0.title.lowercased() < $1.title.lowercased() }
    }
}

/// GET /api/skills: the list answers {"items": [...]}.
public struct CustomSkillList: Decodable, Sendable {
    public var items: [CustomSkillRow] = []
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = j.list("items", of: CustomSkillRow.self)
    }
}

/// GET /api/skills/custom/{id}: the skill's text without its front matter.
public struct CustomSkill: Decodable, Equatable, Sendable {
    public var id: String
    public var name = ""
    public var description = ""
    public var text = ""
    public var state = "ready"
    public var createdAt: Double?

    public init(id: String, name: String = "", description: String = "", text: String = "", state: String = "ready", createdAt: Double? = nil) {
        self.id = id; self.name = name; self.description = description; self.text = text; self.state = state; self.createdAt = createdAt
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id")); name = j.str("name"); description = j.str("description"); text = j.str("text")
        state = j.str("state", "ready"); createdAt = j.optDouble("created_at")
    }
    public var title: String { name.isEmpty ? id : name }
}

public enum KeloFileRules {
    /// The files Kelo always keeps (deleting one = "back to the start"; the server answers 409 reset).
    public static let core = ["assistant.md", "user.md", "MEMORY.md", "AGENTS.md", "CHECKLIST.md", "NOTIFY_PREFS.md"]
    /// Folders where the owner may add folders and delete files.
    public static let openFolders = ["memory", "notes"]

    public static func isCore(_ path: String) -> Bool { core.contains(path) }
    /// Delete (to the trash) is offered for files and folders inside memory/ or notes/, not for those two folders themselves.
    public static func canDelete(_ path: String) -> Bool { openFolders.contains { path.hasPrefix("\($0)/") } }
    /// A new folder can be made here ("" = the top: no).
    public static func canMakeFolder(_ folder: String) -> Bool { openFolders.contains { folder == $0 || folder.hasPrefix("\($0)/") } }

    /// The rows directly inside `folder` ("" = the top): folders first, then core files in `core` order, then the rest by name.
    public static func children(_ all: [HomeEntry], folder: String) -> [HomeEntry] {
        let prefix = folder.isEmpty ? "" : "\(folder)/"
        let direct = all.filter { e in
            guard e.path.hasPrefix(prefix), e.path.count > prefix.count else { return false }
            var rest = String(e.path.dropFirst(prefix.count))
            if rest.hasSuffix("/") { rest.removeLast() }
            return !rest.contains("/")
        }
        func rank(_ e: HomeEntry) -> Int { core.firstIndex(of: e.path) ?? Int.max }
        return direct.sorted { a, b in
            if a.isDir != b.isDir { return a.isDir }
            if rank(a) != rank(b) { return rank(a) < rank(b) }
            return a.name.lowercased() < b.name.lowercased()
        }
    }

    /// The API path for a home path: every part through `segment`, joined with "/".
    public static func apiPath(_ path: String, segment: (String) -> String = { KeloApi.segment($0) }) -> String {
        path.split(separator: "/").map { segment(String($0)) }.joined(separator: "/")
    }

    /// A new folder's name: one part, no slash, not hidden, not empty (trimmed). Nil when it cannot be used.
    public static func folderName(_ raw: String) -> String? {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, !t.contains("/"), !t.contains("\\"), !t.hasPrefix("."), t.count <= 80 else { return nil }
        return t
    }
}
