import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 기록: one week of the journal at a time, the nightly look-back with what it learned, the last memory pass and its undo, and
/// the learned message hours waiting for an answer. A week's answer that comes back after the person moved to another week is dropped.
@MainActor @Observable final class RecordsModel {
    private(set) var start = DigestRules.startOf(LocalDay(seconds: Date().timeIntervalSince1970))
    private(set) var entries: [JournalEntry]?
    private(set) var gaps: [JournalGap] = []
    private(set) var weekFailed = false
    private(set) var look: MyLookback?
    private(set) var lookFailed = false
    private(set) var rebuilding: Set<String> = []
    private(set) var notice: String?
    /// Suggestions just kept or discarded: they stay in place with "지킬게요" / "버렸어요" and an undo for a few seconds.
    private(set) var decided: [Int64: String] = [:]
    private(set) var memChanges: MemoryChanges?
    private(set) var memFailed = false
    private(set) var undoing = false
    /// Goes up when the memory sheet should close (the pass was undone, or was already gone).
    private(set) var memoryClosed = 0
    /// Learned message hours waiting for an answer (GET /api/proactive "hours_suggestion"); hoursDone = the choice just made.
    private(set) var hours: HoursSuggestion?
    private(set) var hoursDone: Bool?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var lookLoad: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    var today: LocalDay { LocalDay(seconds: Date().timeIntervalSince1970) }

    func clearNotice() { notice = nil }
    func setNotice(_ text: String) { notice = text }

    func previousWeek() { start = start.adding(days: -7) }
    func nextWeek() { start = start.adding(days: 7) }

    // ---- the week ----------------------------------------------------------------------------------------------------------------------

    /// The page's `.task(id: start)`: a week that was left before its answer came is never shown.
    func loadWeek() async {
        let week = start
        weekFailed = false
        do {
            var all: [JournalEntry] = [], allGaps: [JournalGap] = []
            for m in DigestRules.months(week) {
                let month = try await api.journal(month: m)
                all += month.items; allGaps += month.pending
            }
            guard week == start, !Task.isCancelled else { return }
            gaps = DigestRules.gaps(week, allGaps)
            entries = DigestRules.entries(week, all)
        } catch {
            guard week == start, !Task.isCancelled else { return }
            entries = nil
            weekFailed = true
        }
    }

    func rebuild(_ date: String) {
        rebuilding.insert(date)
        jobs.run { [self] in
            defer { rebuilding.remove(date) }
            do {
                try await api.request("POST", "/api/journal/rebuild?date=\(date)")
                await loadWeek()
            } catch { notice = ApiMessage.text(error) }
        }
    }

    func delete(_ e: JournalEntry) {
        jobs.run { [self] in
            do {
                try await api.request("DELETE", "/api/journal/\(e.id)")
                entries?.removeAll { $0.id == e.id }
            } catch { notice = ApiMessage.text(error) }
        }
    }

    // ---- the look-back -----------------------------------------------------------------------------------------------------------------

    /// A newer read cancels the older one, so an old list never covers what a change just made.
    func loadLookback() async {
        lookLoad?.cancel()
        let task = Task { @MainActor [self] in
            do { let l: MyLookback = try await api.get("/api/reflection"); if !Task.isCancelled { look = l } }
            catch { if !Task.isCancelled { lookFailed = true } }
        }
        lookLoad = task
        await task.value
    }

    func setLookback(_ on: Bool) {
        jobs.run { [self] in
            do { look = try await api.put("/api/reflection/enabled", body: ["enabled": .bool(on)]) }
            catch { notice = ApiMessage.text(error) }
        }
    }

    func setRule(_ id: Int64, _ status: String) {
        jobs.run { [self] in
            do {
                _ = try await api.setRuleStatus(id, status: status)
                await loadLookback()
            } catch { notice = ApiMessage.text(error) }
        }
    }

    /// Kept or discarded: it stays in place with an undo for `LookbackRules.undoSeconds`.
    func decide(_ id: Int64, _ status: String) {
        decided[id] = status
        setRule(id, status)
        jobs.run { [self] in
            try? await Task.sleep(nanoseconds: UInt64(LookbackRules.undoSeconds * 1_000_000_000))
            if decided[id] == status { decided[id] = nil }
        }
    }

    func undoDecision(_ id: Int64) {
        decided[id] = nil
        setRule(id, LearnedRule.proposed)
    }

    // ---- the last memory pass ----------------------------------------------------------------------------------------------------------

    func openMemory() {
        memChanges = nil; memFailed = false
        jobs.run { [self] in
            do { memChanges = try await api.get("/api/reflection/memory") } catch { memFailed = true }
        }
    }

    func undoMemory() {
        undoing = true
        jobs.run { [self] in
            defer { undoing = false }
            do {
                try await api.request("POST", "/api/reflection/memory/undo")
                notice = L(R.string.memory_pass_undone)
                memoryClosed += 1
            } catch let e as ApiError where e.status == 404 {
                notice = L(R.string.memory_pass_undo_gone)
                memoryClosed += 1
            } catch { notice = ApiMessage.text(error) }
            await loadLookback()
        }
    }

    // ---- the learned message hours -----------------------------------------------------------------------------------------------------

    func loadHours() async {
        guard let got = try? await api.get("/api/proactive", as: ContactHours.self) else { return }
        hours = ContactHoursRules.open(got)   // the weekday one first, else the weekend one
    }

    func answerHours(_ h: HoursSuggestion, accept: Bool) {
        hoursDone = accept
        jobs.run { [self] in
            do {
                _ = try await api.post(accept ? ContactHoursRules.acceptPath(h.id) : ContactHoursRules.keepPath(h.id), as: ContactHours.self)
            } catch let e as ApiError where e.status == 404 {
                // already answered elsewhere or replaced; the next visit shows what is open now
            } catch {
                hoursDone = nil
                notice = ApiMessage.text(error)
                return
            }
            try? await Task.sleep(nanoseconds: UInt64(ContactHoursRules.doneSeconds * 1_000_000_000))
            hours = nil
            hoursDone = nil
        }
    }
}
