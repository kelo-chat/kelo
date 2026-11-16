import Testing
@testable import KeloCore

@Suite struct SmokeTests {
    @Test func version() { #expect(KeloCore.version == "0.1.0") }
}
