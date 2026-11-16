import Foundation

// What a package installs, as the server lists it (server/src/assistant/nest_routes.py, module docstring): the link card's `contents`
// and an update's `changed` (Android my/NestContents.kt). Every field is optional: an older server sends neither, and a missing one
// reads as empty, so no row shows.

/// One file a package installs: its path in the package, its size, its sha256.
public struct NestFile: Decodable, Equatable, Sendable {
    public var path = "", sha256 = ""
    public var bytes: Int64 = 0
    public init(path: String = "", bytes: Int64 = 0, sha256: String = "") { self.path = path; self.bytes = bytes; self.sha256 = sha256 }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        path = j.str("path"); bytes = j.int64("bytes"); sha256 = j.str("sha256")
    }
}

/// The card's "contents": every file, the full text of each .md file and the manifest, the hosts it reaches, and the notes from loading.
public struct NestContents: Decodable, Equatable, Sendable {
    public var files: [NestFile] = []
    public var totalBytes: Int64 = 0
    /// The full text of every .md file and the manifest, by path.
    public var texts: [String: String] = [:]
    /// The paths in `texts` that were cut (their text ends early).
    public var cut: [String] = []
    /// Every host and URL the package can reach, sorted.
    public var endpoints: [String] = []
    /// Notes from loading the package.
    public var warnings: [String] = []
    public init(files: [NestFile] = [], totalBytes: Int64 = 0, texts: [String: String] = [:], cut: [String] = [],
                endpoints: [String] = [], warnings: [String] = []) {
        self.files = files; self.totalBytes = totalBytes; self.texts = texts; self.cut = cut
        self.endpoints = endpoints; self.warnings = warnings
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        files = j.list("files", of: NestFile.self)
        totalBytes = j.int64("total_bytes")
        texts = j.obj("texts").object.compactMapValues { $0.text }
        cut = j.strings("cut"); endpoints = j.strings("endpoints"); warnings = j.strings("warnings")
    }
}

/// An update's view of what differs from the installed files (`update.changed`).
public struct NestChanged: Decodable, Equatable, Sendable {
    public var added: [String] = [], removed: [String] = [], modified: [String] = []
    public init(added: [String] = [], removed: [String] = [], modified: [String] = []) {
        self.added = added; self.removed = removed; self.modified = modified
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        added = j.strings("added"); removed = j.strings("removed"); modified = j.strings("modified")
    }
}

/// A file size as the sheets show it: bytes under 1 KB, else whole kilobytes.
public enum NestSize: Equatable, Sendable {
    case bytes(Int64)
    case kilobytes(Int64)
}

public enum NestContentsRules {
    /// The number on an update row: added + removed + modified.
    public static func changedCount(_ c: NestChanged) -> Int { c.added.count + c.removed.count + c.modified.count }

    /// Bytes under 1024, else whole kilobytes rounded up (a file is never shown as 0 KB).
    public static func size(_ bytes: Int64) -> NestSize {
        bytes < 1024 ? .bytes(bytes) : .kilobytes((bytes + 1023) / 1024)
    }

    public static func isCut(_ c: NestContents, _ path: String) -> Bool { c.cut.contains(path) }

    /// The "see everything" row is shown only when there is something to show.
    public static func hasContents(_ c: NestContents) -> Bool { !c.files.isEmpty || !c.endpoints.isEmpty || !c.warnings.isEmpty }
}
