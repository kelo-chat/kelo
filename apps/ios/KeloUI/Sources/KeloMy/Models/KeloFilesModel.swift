import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Kelo의 파일 (Android my/KeloFilesViewModel.kt): browse, read, edit and delete the files Kelo keeps for itself; a 30-day trash.
@MainActor @Observable final class KeloFilesModel {
    private(set) var entries: [HomeEntry]?
    private(set) var failed = false
    private(set) var open: HomeFile?
    /// The text while editing (nil = not editing).
    var draft: String?
    private(set) var busy = false
    private(set) var trash: [HomeTrashed]?
    /// The custom skills (written on this server); empty also when the skills call failed (the row is then hidden).
    private(set) var skills: [CustomSkillRow] = []
    private(set) var skill: CustomSkill?
    private(set) var note: StringKey?
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi) { self.api = api }

    private func path(_ p: String) -> String { KeloFileRules.apiPath(p) }

    func load() async {
        do { entries = try await api.get("/api/files?folders=1", as: [HomeEntry].self); failed = false }
        catch is CancellationError { return }
        catch { failed = true }
        await loadSkills()
    }

    /// The skills Kelo made on this server; a failure only hides the row, it never fails the page.
    func loadSkills() async {
        do { skills = CustomSkillRow.forList(try await api.get("/api/skills", as: CustomSkillList.self).items) }
        catch is CancellationError { return }
        catch { skills = [] }
    }

    func openSkill(_ id: String) async {
        skill = nil
        do { skill = try await api.get("/api/skills/custom/\(KeloApi.segment(id))", as: CustomSkill.self) }
        catch is CancellationError { return }
        catch { note = R.string.files_load_failed }
    }

    /// Deletes the skill and reloads the list; `then` runs afterwards with true when no custom skill is left.
    func deleteSkill(_ id: String, then: (_ lastOne: Bool) -> Void) async {
        busy = true
        defer { busy = false }
        do {
            _ = try await api.request("DELETE", "/api/skills/custom/\(KeloApi.segment(id))")
            let left = (try? await api.get("/api/skills", as: CustomSkillList.self).items).map(CustomSkillRow.forList) ?? []
            skills = left; skill = nil
            then(left.isEmpty)
        } catch is CancellationError { return }
        catch { note = R.string.files_skill_delete_failed }
    }

    func openFile(_ p: String) async {
        open = nil; draft = nil
        do { open = try await api.get("/api/files/\(path(p))", as: HomeFile.self); draft = nil }
        catch is CancellationError { return }
        catch { note = R.string.files_load_failed }
    }

    func edit() { draft = open?.content }
    func cancelEdit() { draft = nil }
    var dirty: Bool { draft != nil && draft != open?.content }

    func save() async {
        guard let file = open, let text = draft else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await api.request("PUT", "/api/files/\(path(file.path))", body: ["content": .string(text)])
            open = HomeFile(path: file.path, content: text); draft = nil; note = R.string.files_saved
            await load()
        } catch is CancellationError { return }
        catch { note = (error as? ApiError)?.status == 507 ? R.string.files_full : R.string.files_save_failed }
    }

    /// Moves the file to the trash; `then` runs afterwards (the page goes back).
    func delete(_ p: String, then: () -> Void) async {
        busy = true
        defer { busy = false }
        do {
            _ = try await api.request("DELETE", "/api/files/\(path(p))")
            note = R.string.files_deleted
            await load()
            then()
        } catch is CancellationError { return }
        catch { note = (error as? ApiError)?.status == 409 ? R.string.files_core_reset_hint : R.string.files_save_failed }
    }

    func reset(_ p: String) async {
        busy = true
        do {
            _ = try await api.request("POST", "/api/files/\(path(p))/reset")
            busy = false
            note = R.string.files_reset_done
            await openFile(p)
            await load()
        } catch is CancellationError { busy = false; return }
        catch { busy = false; note = R.string.files_save_failed }
    }

    func makeFolder(parent: String, rawName: String) async {
        guard let name = KeloFileRules.folderName(rawName) else { note = R.string.files_folder_bad; return }
        let full = parent.isEmpty ? name : "\(parent)/\(name)"
        do {
            _ = try await api.request("POST", "/api/files/\(path(full))/folder")
            await load()
        } catch is CancellationError { return }
        catch { note = R.string.files_save_failed }
    }

    func loadTrash() async {
        do { trash = try await api.get("/api/home-trash", as: [HomeTrashed].self) }
        catch is CancellationError { return }
        catch { note = R.string.files_load_failed }
    }

    func restore(_ id: String) async {
        do {
            _ = try await api.request("POST", "/api/home-trash/\(KeloApi.segment(id))/restore")
            note = R.string.files_restored
            await loadTrash(); await load()
        } catch is CancellationError { return }
        catch { note = R.string.files_save_failed }
    }

    func purge(_ id: String) async {
        do {
            _ = try await api.request("DELETE", "/api/home-trash/\(KeloApi.segment(id))")
            await loadTrash()
        } catch is CancellationError { return }
        catch { note = R.string.files_save_failed }
    }

    func clearNote() { note = nil }
}
