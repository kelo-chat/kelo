import Foundation
import KeloCore
import KeloShared

// My → Nest → "Install from a link" (Android my/NestLinks.kt): paste a GitHub link, Look (POST /api/nest/install-from-link), Install with
// the permissions the owner saw (POST /api/nest/install-from-link/confirm), and the installed packages with their updates
// (GET /api/nest/installed). Nobody reviews packages.

public struct NestWord: Decodable, Equatable, Sendable {
    public var key = "", text = "", level = "info"
    public init(key: String = "", text: String = "", level: String = "info") { self.key = key; self.text = text; self.level = level }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        key = j.str("key"); text = j.str("text"); level = j.str("level", "info")
    }
}

public struct NestPerms: Decodable, Equatable, Sendable {
    public var hash = "", connect = ""
    public var words: [NestWord] = []
    public init(hash: String = "", connect: String = "", words: [NestWord] = []) { self.hash = hash; self.connect = connect; self.words = words }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        hash = j.str("hash"); connect = j.str("connect"); words = j.list("words", of: NestWord.self)
    }
}

/// The preview of a link: what the package is and what it may do (POST /api/nest/install-from-link).
public struct NestLinkCard: Decodable, Equatable, Sendable {
    public var token = "", id = "", name = "", description = "", kind = "skill", version = "", author = "", repo = ""
    /// The version already installed, nil when it is not.
    public var installed: String?
    public var permissions = NestPerms()
    /// What the package installs (files, endpoints, notes); empty from an older server.
    public var contents = NestContents()
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        token = j.str("token"); id = j.str("id"); name = j.str("name"); description = j.str("description"); kind = j.str("kind", "skill")
        version = j.str("version"); author = j.str("author"); repo = j.str("repo"); installed = j.optStr("installed")
        permissions = j.decode("permissions", as: NestPerms.self) ?? NestPerms()
        contents = j.decode("contents", as: NestContents.self) ?? NestContents()
    }
}

/// An update the repository has: what changed in what it may do (`widened` = it asks for more than before) and which files changed.
public struct NestUpdate: Decodable, Equatable, Sendable {
    public var version = "", permissionsHash = ""
    public var widened: [String] = []
    public var words: [NestWord] = []
    public var pinned = false
    /// The files the update adds, removes and changes; empty from an older server.
    public var changed = NestChanged()
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        version = j.str("version"); widened = j.strings("widened"); permissionsHash = j.str("permissions_hash")
        words = j.list("words", of: NestWord.self); pinned = j.bool("pinned")
        changed = j.decode("changed", as: NestChanged.self) ?? NestChanged()
    }
}

/// One row of GET /api/nest/installed.
public struct NestInstalledRow: Decodable, Equatable, Identifiable, Sendable {
    public var id: String
    public var version = "", name = "", description = "", author = "", repo = ""
    public var enabled = true, pinned = false
    public var words: [NestWord] = []
    public var update: NestUpdate?
    public var title: String { name.isEmpty ? id : name }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id"))
        version = j.str("version"); name = j.str("name"); description = j.str("description"); author = j.str("author"); repo = j.str("repo")
        enabled = j.bool("enabled", true); pinned = j.bool("pinned")
        words = j.list("permission_words", of: NestWord.self)
        update = j.decode("update", as: NestUpdate.self)
    }
}

public enum NestLinkRules {
    /// A pasted link, trimmed; "github.com/a/b" gets https:// (people copy it without), a trailing ".git" goes.
    public static func clean(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasSuffix(".git") { s.removeLast(4) }
        return s.hasPrefix("github.com/") ? "https://\(s)" : s
    }

    public static func looksRight(_ raw: String) -> Bool {
        clean(raw).range(of: #"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/tree/[^\s]+)?/?$"#, options: .regularExpression) != nil
    }

    /// An update that asks for more than before must be shown before it installs.
    public static func needsLook(_ u: NestUpdate) -> Bool { !u.widened.isEmpty }
}

public enum NestLinkProblem: Equatable, Sendable { case notLink, notPackage, expired, changed, already, failed }

/// `card`: the preview to show; `installing`: confirm running; `problem`: why the link or the install did not work (shown under the field).
@MainActor @Observable public final class NestLinksModel {
    /// The pasted link; typing clears the problem line and the server's reason.
    public var link = "" { didSet { problem = nil; reason = "" } }
    public private(set) var checking = false
    public private(set) var card: NestLinkCard?
    public private(set) var installing = false
    public private(set) var problem: NestLinkProblem?
    /// The server's own words for a refusal (422: an unknown field, a key-like text, not a package), shown under the problem line.
    public private(set) var reason = ""
    public private(set) var installed: [NestInstalledRow] = []
    public private(set) var loaded = false
    public private(set) var busy: Set<String> = []
    /// The installed package whose update asks for more: the look sheet shows it before Update.
    public private(set) var look: NestInstalledRow?
    @ObservationIgnored private let api: KeloApi

    public init(api: KeloApi) { self.api = api }

    public func dismissCard() { card = nil }
    public func dismissLook() { look = nil }

    public func loadInstalled() async {
        do { installed = try await api.get("/api/nest/installed", as: [NestInstalledRow].self) }
        catch is CancellationError { return }
        catch { installed = [] }
        loaded = true
    }

    /// Reads the link: the server fetches the newest commit and answers with what it is and what it may do.
    public func check() async {
        let l = NestLinkRules.clean(link)
        guard NestLinkRules.looksRight(l) else { problem = .notLink; return }
        if checking { return }
        checking = true; problem = nil; reason = ""
        defer { checking = false }
        do { card = try await api.post("/api/nest/install-from-link", body: .object(["url": .string(l)]), as: NestLinkCard.self) }
        catch is CancellationError { return }
        catch let e as ApiError {
            problem = e.status == 422 ? .notPackage : .failed
            reason = e.status == 422 ? e.detail : ""
        }
        catch { problem = .failed }
    }

    /// Install exactly what the card showed (its token and permissions hash).
    public func install() async {
        guard let c = card, !installing else { return }
        installing = true
        defer { installing = false }
        do {
            _ = try await api.request("POST", "/api/nest/install-from-link/confirm",
                                      body: .object(["token": .string(c.token), "accept": .string(c.permissions.hash)]))
            card = nil; link = ""
            await loadInstalled()
        } catch is CancellationError {
            return
        } catch let e as ApiError {
            card = nil
            if e.status == 410 { problem = .expired }
            else if e.code == "changed" || e.code == "look_again" { problem = .changed }   // both: the owner has to look at it again
            else if e.code == "already_installed" { problem = .already }
            else { problem = .failed }
            reason = e.status == 422 ? e.detail : ""
        } catch {
            card = nil; problem = .failed
        }
    }

    /// Update: one that asks for more opens `look` first; `accepted` = the owner saw the new permissions. The server may also answer
    /// 409 `look_again` (it asks for more than the list shows): then the look opens instead of failing.
    public func update(_ row: NestInstalledRow, accepted: Bool = false) async {
        guard let u = row.update else { return }
        if NestLinkRules.needsLook(u) && !accepted { look = row; return }
        await runBusy(row.id) {
            let body: JSON = .object(accepted ? ["accept": .string(u.permissionsHash)] : [:])
            do {
                _ = try await self.api.request("POST", "/api/nest/packages/\(KeloApi.segment(row.id))/update", body: body)
                self.look = nil
            } catch let e as ApiError where e.code == "look_again" {
                self.look = row
            }
        }
    }

    public func remove(_ row: NestInstalledRow) async {
        await runBusy(row.id) { _ = try await self.api.request("DELETE", "/api/nest/packages/\(KeloApi.segment(row.id))") }
    }

    /// One call per package at a time; after it the installed list is read again. A failure says it failed.
    private func runBusy(_ id: String, _ call: () async throws -> Void) async {
        if busy.contains(id) { return }
        busy.insert(id)
        defer { busy.remove(id) }
        do { try await call(); await loadInstalled() }
        catch is CancellationError { return }
        catch { problem = .failed }
    }
}
