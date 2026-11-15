import Foundation
import Testing
@testable import KeloCore

/// One scripted socket call: the (seq, event) pairs it gives, then how it ends (nil = waits until cancelled).
private struct Sock { var events: [(Int, TurnEvent)]; var end: SocketEnd? }

private final class SocketFakeApi: TurnApi, @unchecked Sendable {
    let socks: Box<[Sock]>
    let socketAfters = Box<[Int]>([])
    let polls = Box<[Int]>([])
    let stopped = Box<[String]>([])
    let hanging = Box(false)

    init(_ script: [Sock]) { socks = Box(script) }
    func start(text: String, timezone: String) async throws -> String { "t1" }
    func poll(id: String, after: Int) async throws -> TurnPoll {
        polls.update { $0.append(after) }
        return TurnPoll(events: [], next: after, done: true)
    }
    func stop(id: String) async throws { stopped.update { $0.append(id) } }
    func socket(id: String, after: Int, onEvent: @Sendable (Int, TurnEvent) async throws -> Void) async throws -> SocketEnd {
        socketAfters.update { $0.append(after) }
        var next: Sock?
        socks.update { if !$0.isEmpty { next = $0.removeFirst() } }
        guard let s = next else { return .broken }
        for (n, e) in s.events { try await onEvent(n, e) }
        guard let end = s.end else {
            hanging.value = true
            while true { try await Task.sleep(nanoseconds: 1_000_000_000) }
        }
        return end
    }
}

/// Socket tests share the one SocketGate: run one after the other.
@Suite(.serialized) struct TurnSocketTests {
    let now = Box<Int64>(1_000_000)
    let sleeps = Box<[UInt64]>([])
    init() { SocketGate.reset() }
    func follower(_ api: TurnApi) -> TurnFollower {
        TurnFollower(api: api, clock: { [now] in now.value }) { [sleeps] ms in sleeps.update { $0.append(ms) } }
    }
    func ev(_ n: Int) -> (Int, TurnEvent) { (n, .text("e\(n)")) }
    func run(_ f: TurnFollower) async throws -> [TurnEvent] {
        let seen = Box<[TurnEvent]>([])
        try await f.follow(id: "t1") { e in seen.update { $0.append(e) } }
        return seen.value
    }

    @Test func socketGivesAllEventsAndNeverPolls() async throws {
        let api = SocketFakeApi([Sock(events: [ev(0), ev(1), ev(2)], end: .done(next: 3))])
        let seen = try await run(follower(api))
        #expect(seen == [.text("e0"), .text("e1"), .text("e2")])
        #expect(api.polls.value.isEmpty)
    }

    @Test func reconnectsFromTheLastEvent() async throws {
        let api = SocketFakeApi([Sock(events: [ev(0), ev(1)], end: .broken), Sock(events: [ev(2)], end: .done(next: 3))])
        let seen = try await run(follower(api))
        #expect(api.socketAfters.value == [0, 2])
        #expect(seen == [.text("e0"), .text("e1"), .text("e2")])
        #expect(api.polls.value.isEmpty)
    }

    @Test func threeBreaksWithoutEventsFallBackToPollAndBlockSockets() async throws {
        let api = SocketFakeApi(Array(repeating: Sock(events: [], end: .broken), count: 3))
        _ = try await run(follower(api))
        #expect(api.socketAfters.value == [0, 0, 0])
        #expect(api.polls.value == [0])
        _ = try await run(follower(api))   // sockets are skipped now
        #expect(api.socketAfters.value.count == 3)
        #expect(api.polls.value == [0, 0])
        now.value += SocketGate.blockMs   // and tried again later
        _ = try await run(follower(api))
        #expect(api.socketAfters.value.count == 6)
    }

    @Test func unavailablePollsAtOnceWithoutBlocking() async throws {
        let api = SocketFakeApi([Sock(events: [], end: .unavailable)])
        _ = try await run(follower(api))
        #expect(api.polls.value == [0])
        #expect(SocketGate.isOpen(now.value))
    }

    @Test func aGapCountsAsABreak() async throws {
        let api = SocketFakeApi([Sock(events: [ev(0), ev(2)], end: .done(next: 3)), Sock(events: [ev(1), ev(2)], end: .done(next: 3))])
        let seen = try await run(follower(api))
        #expect(api.socketAfters.value == [0, 1])
        #expect(seen == [.text("e0"), .text("e1"), .text("e2")])
    }

    @Test func goneIsA404() async throws {
        let api = SocketFakeApi([Sock(events: [], end: .gone)])
        let f = follower(api)
        do { _ = try await run(f); Issue.record("no error") } catch let e as ApiError { #expect(e.status == 404) }
        #expect(api.polls.value.isEmpty)
    }

    @Test func cancellingDuringTheSocketStopsTheTurn() async throws {
        let api = SocketFakeApi([Sock(events: [ev(0)], end: nil)])
        let f = follower(api)
        let task = Task { try await f.follow(id: "t1") { _ in } }
        while !api.hanging.value { try await Task.sleep(nanoseconds: 5_000_000) }
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(api.stopped.value == ["t1"])
    }
}
