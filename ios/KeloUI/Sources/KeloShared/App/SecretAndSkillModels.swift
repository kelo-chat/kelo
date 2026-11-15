import SwiftUI
import Observation
import KeloCore

/// A secure entry the agent asked for, filled in the chat or the Keychain: the one call that answers it (the value is never shown
/// again) and the line that says why it did not go. `onGone`: the server no longer has it open (answered elsewhere, or it ran out).
@MainActor @Observable
public final class SecretAnswerModel {
    public private(set) var busy = false
    public private(set) var problem = ""
    @ObservationIgnored let jobs = Jobs()

    public init() {}

    public func clearProblem() { problem = "" }
    public func setProblem(_ text: String) { problem = text }

    private func fail(_ error: Error, onGone: () -> Void) {
        if let e = error as? ApiError {
            if e.status == 404 { onGone() } else { problem = e.isNetwork || e.detail.isEmpty ? L(R.string.secret_failed) : e.detail }
        } else {
            problem = L(R.string.secret_failed)
        }
        Haptics.warning()
    }

    private func run(_ api: KeloApi?, _ block: @escaping @MainActor (SecretCalls) async throws -> Void, onGone: @escaping () -> Void) {
        busy = true; problem = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                guard let api else { throw ApiError(status: 401, detail: "signed out") }
                try await block(SecretCalls(api: api))
            } catch { fail(error, onGone: onGone) }
        }
    }

    public func saveKey(name: String, secret: String, api: KeloApi?, saved: @escaping (SecretSaveAnswer) -> Void, onGone: @escaping () -> Void) {
        run(api, { calls in
            let answer = try await calls.putSecret(name: name, secret: secret)
            Haptics.success()
            saved(answer)
        }, onGone: onGone)
    }

    public func saveLogin(name: String, user: String, pass: String, mode: SecretLoginMode, api: KeloApi?,
                          saved: @escaping (SecretSaveAnswer) -> Void, onGone: @escaping () -> Void) {
        run(api, { calls in
            let answer = try await calls.putLogin(name: name, username: user, password: pass, mode: mode)
            Haptics.success()
            saved(answer)
        }, onGone: onGone)
    }

    /// The card the wallet's form kept answers the entry (its id; the nickname and last four digits only go on).
    public func cardSaved(id: String, cardId: String, api: KeloApi?, saved: @escaping (SecretSaveAnswer) -> Void, onGone: @escaping () -> Void) {
        run(api, { calls in saved(try await calls.cardSaved(id: id, cardId: cardId)) }, onGone: onGone)
    }
}

/// The skills waiting for the owner on the Records page (GET /api/skills/proposals), their answers, and a look again every few seconds
/// while Kelo writes a draft (the view's `.task(id: tick)` runs `follow()`; it ends with the page). A read that comes back after a
/// newer one is dropped.
@MainActor @Observable
public final class SkillProposalsModel {
    public private(set) var data: SkillProposals?
    public private(set) var tick = 0
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let say: (String) -> Void
    @ObservationIgnored private var seq = Latest()
    @ObservationIgnored let jobs = Jobs()

    public init(api: @escaping () -> KeloApi?, say: @escaping (String) -> Void) { self.api = api; self.say = say }

    public func load() async {
        guard let api = api() else { return }
        let n = seq.begin()
        if let got = try? await api.skillProposals(), seq.accept(n), !Task.isCancelled { data = got }
    }

    /// A draft lands in the list by itself: asked again while one is being written.
    public func follow() async {
        await load()
        while !Task.isCancelled, data?.drafting.isEmpty == false {
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            if Task.isCancelled { break }
            await load()
        }
    }

    /// Approve (written as shown and used from now on) or discard; `done` runs with true on success. The card says what happened for a
    /// moment and then leaves the list; on a failure the list loads again and the reason is shown.
    public func answer(_ p: SkillProposal, _ approve: Bool, _ done: @escaping (Bool) -> Void) {
        jobs.run { [self] in
            do {
                guard let api = api() else { throw ApiError(status: 401, detail: "signed out") }
                try await api.answerSkill(p, approve: approve)
                done(true)
                try? await Task.sleep(nanoseconds: UInt64(SkillRules.doneSeconds * 1_000_000_000))
                if let d = data { data = SkillRules.without(d, id: p.id) }
            } catch {
                done(false)
                if (error as? ApiError)?.status != 404 { say(ApiMessage.text(error)) }
                tick += 1
            }
        }
    }
}

/// "Save as skill" under a finished chat task or background job: Kelo drafts a skill from the run, and the draft waits for approval.
@MainActor @Observable
public final class SaveAsSkillModel {
    public private(set) var data: SkillProposals?
    public private(set) var posting = false
    public private(set) var tick = 0
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let say: (String) -> Void
    @ObservationIgnored private let taskId: Int64?
    @ObservationIgnored private let workerId: String?
    @ObservationIgnored let jobs = Jobs()

    public init(taskId: Int64?, workerId: String?, api: @escaping () -> KeloApi?, say: @escaping (String) -> Void) {
        self.taskId = taskId; self.workerId = workerId; self.api = api; self.say = say
    }

    public var drafting: Bool { posting || SkillRules.drafting(data, taskId: taskId, workerId: workerId) }

    public func load() async {
        guard let api = api() else { return }
        if let got = try? await api.skillProposals(), !Task.isCancelled { data = got }
    }

    public func follow() async {
        await load()
        while !Task.isCancelled, SkillRules.drafting(data, taskId: taskId, workerId: workerId) {
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            if Task.isCancelled { break }
            await load()
        }
    }

    public func save() {
        guard !drafting else { return }
        Haptics.tick()
        posting = true
        jobs.run { [self] in
            do {
                guard let api = api() else { throw ApiError(status: 401, detail: "signed out") }
                try await api.draftSkill(taskId: workerId == nil ? taskId : nil, workerId: workerId)
                say(L(R.string.skill_save_started, L(R.string.tab_my), L(R.string.my_records_title)))
            } catch let e as ApiError where e.status == 409 {
                say(L(R.string.skill_save_busy))
            } catch let e as ApiError where e.status == 404 {
                say(L(R.string.skill_save_unavailable))
            } catch let e as ApiError where e.status == 503 {
                say(L(R.string.skill_save_no_model))
            } catch {
                say(ApiMessage.text(error))
            }
            await load()
            posting = false
            tick += 1
        }
    }
}
