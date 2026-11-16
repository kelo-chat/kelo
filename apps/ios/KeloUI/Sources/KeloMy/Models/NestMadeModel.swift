import Foundation
import KeloCore
import KeloShared

// My → Nest → "Made here" (Android my/NestMadeHere.kt; docs/nest.md, "Made by talking"): the drafts Kelo made on this server
// (GET /api/nest/drafts) and the custom skills (GET /api/skills, source "custom"), with Open, Share (POST /api/nest/drafts/{id}/share)
// and Delete.

/// Where a draft was put on GitHub (`.shared.json`): "owner/name", the 40-character commit, when.
public struct NestShared: Decodable, Equatable, Sendable {
    public var repo = "", commit = ""
    public var at: Double?
    public init(repo: String = "", commit: String = "", at: Double? = nil) { self.repo = repo; self.commit = commit; self.at = at }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        repo = j.str("repo"); commit = j.str("commit"); at = j.optDouble("at")
    }
}

/// One row of GET /api/nest/drafts.
public struct NestDraftRow: Decodable, Equatable, Sendable {
    public var id: String
    public var kind = "skill", name = "", version = ""
    public var updatedAt: Double?
    public var shared: NestShared?
    public init(id: String, kind: String = "skill", name: String = "", version: String = "", updatedAt: Double? = nil, shared: NestShared? = nil) {
        self.id = id; self.kind = kind; self.name = name; self.version = version; self.updatedAt = updatedAt; self.shared = shared
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id"))
        kind = j.str("kind", "skill"); name = j.str("name"); version = j.str("version"); updatedAt = j.optDouble("updated_at")
        shared = j.decode("shared", as: NestShared.self)
    }
}

public struct NestDraftList: Decodable, Sendable {
    public var items: [NestDraftRow] = []
    public init(from decoder: Decoder) throws { items = try Lenient(decoder).list("items", of: NestDraftRow.self) }
}

/// GET /api/nest/drafts/{id}: the files (text) and the validator's findings.
public struct NestDraftDetails: Decodable, Equatable, Sendable {
    public var id = "", version = "", kind = ""
    public var files: [String: String] = [:]
    public var valid = true
    public var findings: [String] = []
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.str("id"); version = j.str("version"); kind = j.str("kind"); valid = j.bool("valid", true); findings = j.strings("findings")
        files = (j["files"]?.objectValue ?? [:]).compactMapValues { $0.text }
    }
}

public enum MadeKind: Equatable, Sendable { case skill, extensionKind, connector, customSkill }

/// One line of "Made here". A `draft` can be shared and its screen opened; a custom skill is a skill Kelo made that is not a draft (yet).
public struct MadeItem: Equatable, Identifiable, Sendable {
    public var id: String
    public var kind: MadeKind
    public var name: String
    public var version = ""
    public var repo = ""
    public var draft = true

    public init(id: String, kind: MadeKind, name: String, version: String = "", repo: String = "", draft: Bool = true) {
        self.id = id; self.kind = kind; self.name = name; self.version = version; self.repo = repo; self.draft = draft
    }

    public var title: String { name.isEmpty ? id : name }
    /// The shared repository's page, nil when it was not shared (or the name is not a plain "owner/name").
    public var repoUrl: String? { MadeHereRules.repoUrl(repo) }
    public var shared: Bool { !repo.isEmpty }
}

public enum MadeHereRules {
    public static func repoUrl(_ repo: String) -> String? {
        let r = repo.trimmingCharacters(in: .whitespaces)
        let ok = r.range(of: #"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"#, options: .regularExpression) != nil
        return ok ? "https://github.com/\(r)" : nil
    }

    private static func kind(_ k: String) -> MadeKind {
        switch k { case "extension": .extensionKind; case "connector": .connector; default: .skill }
    }

    /// Drafts first (newest first), then the custom skills that are not a draft already (a skill made into a draft shows once).
    public static func items(drafts: [NestDraftRow], skills: [CustomSkillRow]) -> [MadeItem] {
        let d = drafts.sorted { ($0.updatedAt ?? 0) > ($1.updatedAt ?? 0) }
            .map { MadeItem(id: $0.id, kind: kind($0.kind), name: $0.name, version: $0.version, repo: $0.shared?.repo ?? "", draft: true) }
        let ids = Set(drafts.map(\.id))
        let s = CustomSkillRow.forList(skills).filter { !ids.contains($0.id) }.map { MadeItem(id: $0.id, kind: .customSkill, name: $0.name, draft: false) }
        return d + s
    }
}

/// Share started a chat turn: open the chat on `turnId` (Kelo asks where to put it).
public enum MadeEffect: Equatable, Sendable { case openChat(String) }

@MainActor @Observable public final class NestMadeModel {
    public private(set) var loaded = false
    public private(set) var failed = false
    public private(set) var items: [MadeItem] = []
    public private(set) var busy: Set<String> = []
    /// The item whose Share needs GitHub write access (the sheet offers to connect it).
    public private(set) var connectFor: MadeItem?
    /// A short failure line; "" = the app's own "couldn't start sharing".
    public var message: String?
    public private(set) var effects: [MadeEffect] = []
    @ObservationIgnored private let api: KeloApi

    public init(api: KeloApi) { self.api = api }

    public func nextEffect() -> MadeEffect? { effects.isEmpty ? nil : effects.removeFirst() }

    /// Reads the drafts and the custom skills; each can fail on its own (an older server has no drafts) and the other still shows.
    public func load() async {
        var bad = 0
        var drafts: [NestDraftRow] = []
        var skills: [CustomSkillRow] = []
        do { drafts = try await api.get("/api/nest/drafts", as: NestDraftList.self).items } catch is CancellationError { return } catch { bad += 1 }
        do { skills = try await api.get("/api/skills", as: CustomSkillList.self).items } catch is CancellationError { return } catch { bad += 1 }
        items = MadeHereRules.items(drafts: drafts, skills: skills)
        loaded = true; failed = bad == 2
    }

    /// The live event: Kelo wrote to a draft, so the list follows.
    public func changed() async { await load() }

    /// Share: 202 opens the chat on the turn; 409 `github_write_needed` raises the "connect GitHub" sheet; anything else says why not.
    public func share(_ item: MadeItem) async {
        if busy.contains(item.id) { return }
        busy.insert(item.id); message = nil
        defer { busy.remove(item.id) }
        do {
            // a custom skill is copied into a draft first ("share this skill"); the draft already being there is fine
            if !item.draft {
                do { _ = try await api.request("POST", "/api/nest/drafts/from-skill/\(KeloApi.segment(item.id))") }
                catch let e as ApiError where e.status == 409 {}
            }
            let r = try await api.post("/api/nest/drafts/\(KeloApi.segment(item.id))/share", as: JSON.self)
            connectFor = nil
            let turn = Lenient(r).str("turn_id")
            if !turn.isEmpty { effects.append(.openChat(turn)) }
            await load()
        } catch is CancellationError {
            return
        } catch let e as ApiError {
            if e.status == 409 && e.code == "github_write_needed" { connectFor = item }
            else { message = e.isNetwork ? "" : e.detail }
        } catch {
            message = "\(error)"
        }
    }

    /// After GitHub was connected: Share again for the same item.
    public func retryShare() async { if let i = connectFor { await share(i) } }
    public func dismissConnect() { connectFor = nil }

    /// Deletes the draft (DELETE /api/nest/drafts/{id}) or the custom skill, then reads the list again.
    public func delete(_ item: MadeItem) async {
        if busy.contains(item.id) { return }
        busy.insert(item.id); message = nil
        defer { busy.remove(item.id) }
        do {
            let path = item.draft ? "/api/nest/drafts/\(KeloApi.segment(item.id))" : "/api/skills/custom/\(KeloApi.segment(item.id))"
            _ = try await api.request("DELETE", path)
            items.removeAll { $0.id == item.id && $0.draft == item.draft }
            await load()
        } catch is CancellationError {
            return
        } catch let e as ApiError {
            message = e.detail
        } catch {
            message = "\(error)"
        }
    }
}
