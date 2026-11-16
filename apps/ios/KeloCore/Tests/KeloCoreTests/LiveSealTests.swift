import Foundation
import Testing
@testable import KeloLive

@Suite struct LiveSealTests {
    let plain = LivePlain(step: "Opening a site", site: "example.com", state: "working")

    @Test func roundTrip() throws {
        let key = Data((0..<32).map { UInt8($0) })
        let sealed = try #require(LiveSeal.seal(plain, key: key))
        #expect(LiveSeal.open(sealed, key: key) == plain)
    }

    @Test func wrongKeyOrJunkGivesNil() throws {
        let key = Data(repeating: 1, count: 32)
        let sealed = try #require(LiveSeal.seal(plain, key: key))
        #expect(LiveSeal.open(sealed, key: Data(repeating: 9, count: 32)) == nil)
        #expect(LiveSeal.open(sealed, key: Data(repeating: 1, count: 16)) == nil)
        #expect(LiveSeal.open("", key: key) == nil)
        #expect(LiveSeal.open("not base64!", key: key) == nil)
    }

    /// Made by the server's side (Python cryptography ChaCha20Poly1305): key 32x0x01, nonce 12x0x02, aad "kelo-live v1".
    @Test func serverMadeVector() {
        let sealed = "AgICAgICAgICAgICnT6BWujTyuBTYpmcveKKTqzSTH7q3hjxOKZo1IHzZubxdoGUVn5q3PioUGqq4gyU/fKtt1gE/X09KvfSB+I7qDqJdpveohftYkX61gKFKKk="
        #expect(LiveSeal.open(sealed, key: Data(repeating: 1, count: 32)) == plain)
    }

    @Test func tokenHex() {
        #expect(LiveToken.hex(Data([0x00, 0xab])) == "00ab")
    }
}
