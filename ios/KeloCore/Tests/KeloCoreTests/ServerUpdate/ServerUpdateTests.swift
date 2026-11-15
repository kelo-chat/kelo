import Testing
import Foundation
@testable import KeloCore

// Android my/ServerUpdateTest.kt: what the owner sees for the server update, and following a run through the server's restart.
@Suite struct ServerUpdateTests {
    private let now: Double = 1_731_200_000

    private func st(_ json: String) throws -> ServerUpdateStatus { try JSONDecoder().decode(ServerUpdateStatus.self, from: Data(json.utf8)) }

    private func api(_ t: FakeTransport) -> KeloApi { KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "" }) }

    private let full = """
    {"state":"ok","message":"","current":"0.4.0",
     "latest":{"version":"0.5.0","date":"2026-11-01T00:00:00Z","notes":"Faster chat\\nFixes"},
     "update_available":true,"checked_at":1731100000.0,"auto_update":false,"auto_time":"03:50","job":null,
     "rollback":{"available":true,"version":"0.3.9","created_at":1731000000.0},"extra":{"ignored":1}}
    """

    private func job(_ state: String, action: String = "update", finished: Double? = nil, step: String = "download", progress: Int = 40) -> ServerUpdateJob {
        ServerUpdateJob(id: "20261109-035000", action: action, state: state, step: step, progress: progress, from: "0.4.0", to: "0.5.0",
                        startedAt: (finished ?? now) - 300, finishedAt: finished)
    }

    // ---- decoding ----

    @Test func decodesTheWholeAnswer() throws {
        let s = try st(full)
        #expect(s.state == "ok")
        #expect(s.current == "0.4.0")
        #expect(s.latest == ServerUpdateRelease(version: "0.5.0", date: "2026-11-01T00:00:00Z", notes: "Faster chat\nFixes"))
        #expect(s.updateAvailable)
        #expect(s.checkedAt == 1_731_100_000)
        #expect(!s.autoUpdate)
        #expect(s.autoTime == "03:50")
        #expect(s.job == nil)
        #expect(s.rollback == ServerUpdateRollback(available: true, version: "0.3.9", createdAt: 1_731_000_000))
        #expect(s.latest?.dateSeconds == 1_793_491_200)
    }

    @Test func everythingMayBeMissingOrNull() throws {
        let s = try st("{}")
        #expect(s.state == "unavailable")
        #expect(s.current == nil && s.latest == nil && !s.updateAvailable && s.job == nil && !s.rollback.available)
        let n = try st(#"{"state":"ok","current":null,"latest":null,"update_available":null,"job":null,"rollback":null,"auto_time":null}"#)
        #expect(n.state == "ok" && n.current == nil && n.latest == nil && n.rollback == ServerUpdateRollback())
        // mistyped fields take their defaults; a release without a version counts as none
        let m = try st(#"{"state":"ok","latest":{"version":""},"update_available":"yes","job":{"progress":"x"}}"#)
        #expect(m.latest == nil && !m.updateAvailable && m.job == nil)
    }

    @Test func decodesARun() throws {
        let s = try st("""
        {"state":"ok","current":"0.4.0","job":{"id":"20261109-035000","action":"rollback","auto":true,"state":"rolled_back",
         "step":"rollback","progress":140,"from":"0.4.0","to":"0.5.0","error":"health check timed out","data_restored":true,
         "started_at":1731100000.0,"finished_at":1731100300.0}}
        """)
        let j = try #require(s.job)
        #expect(j.isRollback && j.auto && j.state == "rolled_back" && j.step == "rollback")
        #expect(j.progress == 100)   // clamped
        #expect(j.error == "health check timed out" && j.dataRestored)
        #expect(j.startedAt == 1_731_100_000 && j.finishedAt == 1_731_100_300)
        #expect(try st(#"{"job":{"state":"running","progress":-5}}"#).job?.progress == 0)
    }

    @Test func roundTripsThroughTheScreenCache() throws {
        let s = try st(full)
        let back = try JSONDecoder().decode(ServerUpdateStatus.self, from: JSONEncoder().encode(s))
        #expect(back == s)
        var r = s; r.job = job("rolled_back", finished: now)
        #expect(try JSONDecoder().decode(ServerUpdateStatus.self, from: JSONEncoder().encode(r)) == r)
    }

    // ---- what shows ----

    @Test func onlyOkAndLocalShowAnything() throws {
        #expect(ServerUpdateRules.visible(try st(full)))
        #expect(ServerUpdateRules.visible(try st(#"{"state":"local","current":"0.4.0","message":"Built here"}"#)))
        #expect(!ServerUpdateRules.visible(try st(#"{"state":"unavailable"}"#)))
        #expect(!ServerUpdateRules.visible(try st(#"{"state":"something-new"}"#)))
        #expect(!ServerUpdateRules.visible(nil))
        #expect(ServerUpdateRules.hides(403) && ServerUpdateRules.hides(404))
        #expect(!ServerUpdateRules.hides(500) && !ServerUpdateRules.hides(0))
    }

    @Test func localShowsNoButtonsAndNoCard() throws {
        let s = try st(#"{"state":"local","current":"0.4.0","message":"Built here","update_available":true,"latest":{"version":"0.5.0"},"rollback":{"available":true,"version":"0.3.9"}}"#)
        #expect(ServerUpdateRules.isLocal(s))
        #expect(!ServerUpdateRules.showUpdateButton(s))
        #expect(ServerUpdateRules.rollbackVersion(s) == nil)
        #expect(!ServerUpdateRules.canCheck(s))
        #expect(!ServerUpdateRules.showAutoToggle(s))
        #expect(ServerUpdateRules.card(s, now: now) == nil)
        #expect(ServerUpdateRules.rowValue(s) == "0.4.0")
    }

    @Test func updateButtonWhenNewerAndIdle() throws {
        var s = try st(full)
        #expect(ServerUpdateRules.showUpdateButton(s))
        #expect(ServerUpdateRules.available(s)?.version == "0.5.0")
        s.job = job("running")
        #expect(!ServerUpdateRules.showUpdateButton(s))
        #expect(!ServerUpdateRules.canCheck(s))
        #expect(ServerUpdateRules.showAutoToggle(s))
        s.job = job("done", finished: now - 60)
        s.updateAvailable = false
        #expect(!ServerUpdateRules.showUpdateButton(s))
        s.updateAvailable = true; s.latest = nil
        #expect(!ServerUpdateRules.showUpdateButton(s))
    }

    @Test func goBackOnlyWithARollbackPointAndNothingRunning() throws {
        var s = try st(full)
        #expect(ServerUpdateRules.rollbackVersion(s) == "0.3.9")
        s.job = job("running", action: "rollback")
        #expect(ServerUpdateRules.rollbackVersion(s) == nil)
        s.job = nil; s.rollback.available = false
        #expect(ServerUpdateRules.rollbackVersion(s) == nil)
        s.rollback = ServerUpdateRollback(available: true, version: " ")
        #expect(ServerUpdateRules.rollbackVersion(s) == nil)
    }

    @Test func cardOnMy() throws {
        var s = try st(full)
        #expect(ServerUpdateRules.card(s, now: now) == .available("0.5.0"))
        s.job = job("running", progress: 55)
        #expect(ServerUpdateRules.card(s, now: now) == .running(rollback: false, progress: 55, to: "0.5.0"))
        s.job = job("running", action: "rollback")
        #expect(ServerUpdateRules.card(s, now: now) == .running(rollback: true, progress: 40, to: "0.5.0"))
        // a recent run that went wrong comes before a newer version
        s.job = job("rolled_back", finished: now - 3600)
        #expect(ServerUpdateRules.card(s, now: now) == .rolledBack(from: "0.4.0", to: "0.5.0"))
        s.job = job("rollback_failed", finished: now - 3600)
        #expect(ServerUpdateRules.card(s, now: now) == .rollbackFailed)
        s.job = job("failed", finished: now - 3600)
        #expect(ServerUpdateRules.card(s, now: now) == .failed(rollback: false))
        s.job = job("failed", action: "rollback", finished: now - 3600)
        #expect(ServerUpdateRules.card(s, now: now) == .failed(rollback: true))
        // after a day it goes; with nothing newer there is no card at all
        s.job = job("rolled_back", finished: now - 25 * 3600)
        #expect(ServerUpdateRules.card(s, now: now) == .available("0.5.0"))
        s.updateAvailable = false
        #expect(ServerUpdateRules.card(s, now: now) == nil)
        // a run that went well brings no card
        s.job = job("done", finished: now - 60)
        #expect(ServerUpdateRules.card(s, now: now) == nil)
        #expect(ServerUpdateRules.card(nil, now: now) == nil)
        #expect(ServerUpdateRules.card(try st(#"{"state":"unavailable","update_available":true,"latest":{"version":"9"}}"#), now: now) == nil)
    }

    @Test func resultPerState() throws {
        var s = try st(full)
        #expect(ServerUpdateRules.result(s, now: now) == nil)
        s.job = job("running")
        #expect(ServerUpdateRules.result(s, now: now) == nil)
        s.job = job("done", finished: now - 10)
        #expect(ServerUpdateRules.result(s, now: now) == .updated(to: "0.5.0"))
        s.job = job("done", action: "rollback", finished: now - 10)
        #expect(ServerUpdateRules.result(s, now: now) == .wentBack(to: "0.5.0"))
        var j = job("failed", finished: now - 10); j.error = "signature check failed"
        s.job = j
        #expect(ServerUpdateRules.result(s, now: now) == .failed(rollback: false, error: "signature check failed"))
        j = job("rolled_back", finished: now - 10); j.dataRestored = true
        s.job = j
        #expect(ServerUpdateRules.result(s, now: now) == .rolledBack(from: "0.4.0", to: "0.5.0", dataRestored: true, error: nil))
        s.job = job("rollback_failed", finished: now - 10)
        #expect(ServerUpdateRules.result(s, now: now) == .rollbackFailed(error: nil))
        s.job = job("rollback_failed", finished: now - 2 * 86400)
        #expect(ServerUpdateRules.result(s, now: now) == nil)
        // no finish time: still shown
        var nf = job("failed"); nf.finishedAt = nil; nf.startedAt = nil
        s.job = nf
        #expect(ServerUpdateRules.result(s, now: now) == .failed(rollback: false, error: nil))
    }

    @Test func stepsClockAndDate() {
        #expect(ServerUpdateRules.step(job("running", step: "self_update")) == "self_update")
        #expect(ServerUpdateRules.step(job("running", step: "warp")) == nil)
        #expect(ServerUpdateRules.restarting(job("running", step: "restart")))
        #expect(!ServerUpdateRules.restarting(job("running", step: "download")))
        #expect(ServerUpdateRules.fraction(job("running", progress: 25)) == 0.25)
        #expect(ServerUpdateRules.clock("03:50")! == (3, 50))
        #expect(ServerUpdateRules.clock("23:05:00")! == (23, 5))
        #expect(ServerUpdateRules.clock("24:00") == nil)
        #expect(ServerUpdateRules.clock("soon") == nil)
        #expect(ServerUpdateRules.clock(nil) == nil)
        #expect(ServerUpdateRules.parseDate("2026-11-01T00:00:00.250Z") != nil)
        #expect(ServerUpdateRules.parseDate("2026-11-01") == 1_793_491_200)
        #expect(ServerUpdateRules.parseDate("") == nil)
        #expect(ServerUpdateRules.parseDate("tomorrow") == nil)
    }

    // ---- calls ----

    @Test func loadHidesForOtherDevicesOlderServersAndUnavailable() async throws {
        #expect(await ServerUpdateRules.load(api(FakeTransport(.ok(403, #"{"detail":"Only the owner"}"#)))) == .hidden)
        #expect(await ServerUpdateRules.load(api(FakeTransport(.ok(404, #"{"detail":"Not Found"}"#)))) == .hidden)
        #expect(await ServerUpdateRules.load(api(FakeTransport(.ok(200, #"{"state":"unavailable"}"#)))) == .hidden)
        let t = FakeTransport(.ok(200, full))
        #expect(await ServerUpdateRules.load(api(t)) == .shown(try st(full)))
        #expect(t.last.url?.path == "/api/server-update" && t.last.httpMethod == "GET")
        guard case .failed(let f) = await ServerUpdateRules.load(api(FakeTransport(.ok(502, "{}")))) else { Issue.record("expected failed"); return }
        #expect(f.status == 502 && !f.hides)
    }

    @Test func actionsSendTheirBodies() async throws {
        let t = FakeTransport(.ok(200, full), .ok(202, full), .ok(202, full), .ok(200, full))
        let a = api(t)
        _ = try await ServerUpdateRules.check(a)
        #expect(t.last.url?.path == "/api/server-update/check" && t.last.httpMethod == "POST" && t.lastBody == "{}")
        _ = try await ServerUpdateRules.startUpdate(a)
        #expect(t.last.url?.path == "/api/server-update/update" && t.lastBody == "{}")
        _ = try await ServerUpdateRules.startRollback(a)
        #expect(t.last.url?.path == "/api/server-update/rollback" && t.lastBody == "{}")
        _ = try await ServerUpdateRules.setAuto(a, true)
        #expect(t.last.url?.path == "/api/server-update/settings" && t.last.httpMethod == "PUT" && t.lastBody == #"{"auto_update":true}"#)
    }

    @Test func aRefusalCarriesTheServersWords() async {
        let t = FakeTransport(.ok(409, #"{"detail":"Kelo is already up to date."}"#))
        do {
            _ = try await ServerUpdateRules.startUpdate(api(t))
            Issue.record("expected a refusal")
        } catch let f as ServerUpdateFailure {
            #expect(f.status == 409 && f.detail == "Kelo is already up to date.")
        } catch { Issue.record("unexpected \(error)") }
    }

    // ---- following a run ----

    /// A clock that moves by each sleep, so 15 minutes pass at once.
    final class FakeClock: @unchecked Sendable {
        let t = Box<Double>(0)
        var now: @Sendable () -> Double { { [t] in t.value } }
        var sleep: @Sendable (Double) async -> Void { { [t] s in t.update { $0 += s } } }
    }

    private func running(_ step: String, _ progress: Int, id: String = "20261109-035000") -> String {
        #"{"state":"ok","current":"0.4.0","job":{"id":"\#(id)","action":"update","state":"running","step":"\#(step)","progress":\#(progress),"from":"0.4.0","to":"0.5.0"}}"#
    }

    @Test func followsThroughTheRestart() async throws {
        let start = try st(running("preflight", 5))
        let t = FakeTransport(
            .ok(200, running("download", 30)),
            .fail(URLError(.cannotConnectToHost)),        // the server restarting
            .ok(502, "<html>Bad gateway</html>"),           // the proxy in front of it
            .ok(404, "{}"),                                 // something answering in between
            .ok(200, #"{"state":"unavailable"}"#),          // the updater itself coming back
            .ok(200, running("health", 90)),
            .ok(200, #"{"state":"ok","current":"0.5.0","job":{"id":"20261109-035000","state":"done","step":"done","progress":100,"to":"0.5.0"}}"#))
        let clock = FakeClock()
        let box = Box<[ServerUpdateRules.Progress]>([])
        let out = await ServerUpdateRules.follow(api(t), from: start, now: clock.now, sleep: clock.sleep) { p in box.update { $0.append(p) } }
        let seen = box.value
        guard case .finished(let end) = out else { Issue.record("expected finished, got \(out)"); return }
        #expect(end.current == "0.5.0" && end.job?.state == "done")
        #expect(seen.map(\.reconnecting) == [false, true, true, true, true, false, false])
        #expect(seen[1].status.job?.step == "download")   // a failed ask keeps the last status
        #expect(t.requests.value.count == 7)
        #expect(clock.t.value == 14)                       // every 2 s
    }

    @Test func givesUpOnlyAfterFifteenMinutes() async throws {
        let start = try st(running("restart", 70))
        let t = FakeTransport()
        t.answers.value = Array(repeating: .fail(URLError(.timedOut)), count: 1000)
        let clock = FakeClock()
        let out = await ServerUpdateRules.follow(api(t), from: start, now: clock.now, sleep: clock.sleep)
        #expect(out == .gaveUp(start))
        #expect(t.requests.value.count == 450)   // 15 min / 2 s
    }

    @Test func stopsAtOnceWhenTurnedAway() async throws {
        let t = FakeTransport(.fail(URLError(.networkConnectionLost)), .ok(403, #"{"detail":"no"}"#))
        let clock = FakeClock()
        let out = await ServerUpdateRules.follow(api(t), from: try st(running("download", 10)), now: clock.now, sleep: clock.sleep)
        #expect(out == .refused(ServerUpdateFailure(status: 403, detail: "no")))
    }

    @Test func aFinishedStartNeedsNoFollowing() async throws {
        let t = FakeTransport()
        let done = try st(#"{"state":"ok","job":{"id":"a","state":"failed","error":"x"}}"#)
        #expect(await ServerUpdateRules.follow(api(t), from: done) == .finished(done))
        let none = try st(full)
        #expect(await ServerUpdateRules.follow(api(t), from: none) == .finished(none))
        #expect(t.requests.value.isEmpty)
    }

    @Test func anotherRunOrNoRunEventuallyEnds() async throws {
        let start = try st(running("download", 10))
        let t = FakeTransport()
        t.answers.value = Array(repeating: .ok(200, #"{"state":"ok","current":"0.4.0","job":null}"#), count: 100)
        let clock = FakeClock()
        let out = await ServerUpdateRules.follow(api(t), from: start, now: clock.now, sleep: clock.sleep)
        guard case .finished(let end) = out else { Issue.record("expected finished"); return }
        #expect(end.job == nil)
        #expect(t.requests.value.count == ServerUpdateRules.lostAnswers)
        // an older finished run with another id does not end it at once, the matching one does
        let t2 = FakeTransport(.ok(200, #"{"state":"ok","job":{"id":"old","state":"done"}}"#),
                               .ok(200, #"{"state":"ok","job":{"id":"20261109-035000","state":"rolled_back","from":"0.4.0","to":"0.5.0"}}"#))
        let out2 = await ServerUpdateRules.follow(api(t2), from: start, now: clock.now, sleep: clock.sleep)
        guard case .finished(let end2) = out2 else { Issue.record("expected finished"); return }
        #expect(end2.job?.state == "rolled_back")
    }
}
