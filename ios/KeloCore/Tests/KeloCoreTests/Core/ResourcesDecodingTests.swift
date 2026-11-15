import Foundation
import Testing
@testable import KeloCore

/// GET /api/resources as servers really send it. Until 2026-11 the server sent minimum/recommended memory as floats (536870912.0),
/// which left the Android page blank (a Long field could not read it). Here the same answers must read to the same numbers.
@Suite struct ResourcesDecodingTests {
    /// The answer from the phone that showed the blank page (Android 0.1.402102), as it was sent.
    static let withFloats = #"{"memory":{"used":109551616,"limit":null},"cpus":null,"disk":{"used":1609985,"limit":null,"#
        + #""parts":{"database":765952,"notes":763710,"uploads":42978,"other":37066,"history":279},"free_on_host":16335097856},"#
        + #""host":{"memory":12514037760,"cpus":4,"disk_free":16335097856,"disk_total":83953606656},"profile":"unlimited","#
        + #""minimum":{"memory":536870912.0,"cpus":0.5,"disk":5637144576},"recommended":{"memory":1073741824.0,"cpus":1.0,"disk":13555990528}}"#

    /// The same answer from a fixed server: every size a whole number.
    static let withInts = withFloats.replacingOccurrences(of: "536870912.0", with: "536870912")
        .replacingOccurrences(of: "1073741824.0", with: "1073741824")

    private func decode(_ json: String) throws -> Resources { try JSONDecoder().decode(Resources.self, from: Data(json.utf8)) }

    private func expectThePhonesNumbers(_ r: Resources) {
        #expect(r.memory == MemoryUse(used: 109551616, limit: nil))
        #expect(r.cpus == nil)
        #expect(r.disk == DiskUse(used: 1609985, limit: nil,
                                  parts: ["database": 765952, "notes": 763710, "uploads": 42978, "other": 37066, "history": 279],
                                  freeOnHost: 16335097856))
        #expect(r.host == HostCapacity(memory: 12514037760, cpus: 4, diskFree: 16335097856, diskTotal: 83953606656))
        #expect(r.profile == "unlimited")
        #expect(r.minimum == Sizes(memory: 536870912, cpus: 0.5, disk: 5637144576))
        #expect(r.recommended == Sizes(memory: 1073741824, cpus: 1, disk: 13555990528))
    }

    @Test func readsTheAnswerThatLeftTheAndroidPageBlank() throws { expectThePhonesNumbers(try decode(Self.withFloats)) }

    @Test func readsTheSameAnswerWithWholeNumbers() throws {
        expectThePhonesNumbers(try decode(Self.withInts))
        #expect(try decode(Self.withFloats) == decode(Self.withInts))
    }

    @Test func throughTheApiClientToo() async throws {
        let api = KeloApi(baseUrl: "https://kelo.example.com", token: "t", transport: FakeTransport(.ok(200, Self.withFloats)))
        expectThePhonesNumbers(try await api.resources())
    }

    @Test func everySizeMayComeAsAFloatOrAsText() throws {
        let r = try decode(#"{"memory":{"used":1.0E8,"limit":"2147483648"},"disk":{"used":2048.0,"limit":4096.0,"#
                           + #""parts":{"uploads":12.0,"notes":7},"free_on_host":1.6335097856E10},"#
                           + #""host":{"memory":1.251403776E10,"disk_free":10.0,"disk_total":20.0},"minimum":{"memory":5.36870912E8,"disk":5.637144576E9}}"#)
        #expect(r.memory == MemoryUse(used: 100_000_000, limit: 2147483648))
        #expect(r.disk == DiskUse(used: 2048, limit: 4096, parts: ["uploads": 12, "notes": 7], freeOnHost: 16335097856))
        #expect(r.host == HostCapacity(memory: 12514037760, cpus: nil, diskFree: 10, diskTotal: 20))
        #expect(r.minimum == Sizes(memory: 536870912, cpus: 0, disk: 5637144576))
    }

    @Test func missingAndNullSizesTakeTheDefaults() throws {
        let r = try decode(#"{"memory":{},"disk":{"used":null,"parts":{}},"host":{"memory":null}}"#)
        #expect(r.memory == MemoryUse())
        #expect(r.disk.used == 0)
        #expect(r.host.memory == nil)
        #expect(r.minimum == Sizes())
    }
}
