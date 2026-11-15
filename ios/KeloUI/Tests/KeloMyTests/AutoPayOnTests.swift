import Testing
import Foundation
import CryptoKit
import KeloCore
@testable import KeloMy

/// Turning auto-pay on without typing the card again (AutoPayOn, Android AutoPayOnTest): this phone opens its own end-to-end copy
/// after the owner's check and sends the number and CVC once; a card added on another device says so. A fake key store (a key made
/// here, standing in for the Keychain item Face ID releases) replaces WalletKey.
@Suite struct AutoPayOnTests {
    private struct FakeKeys {
        let key = SymmetricKey(size: .bits256)
        var keyId: String { CardBlob.keyId(key) }
    }

    private let keys = FakeKeys()

    private func blob() throws -> E2eBlob {
        try CardBlob.seal(CardSecret.of(number: "4242424242424242", cvc: "123")!, key: keys.key, keyId: keys.keyId)
    }

    private func card(keyId: String? = nil, alg: String = CardBlob.alg) -> WalletCard {
        WalletCard(id: "c1", label: "Everyday", brand: "visa", last4: "4242", e2e: true, e2eAlg: alg, e2eKeyId: keyId ?? keys.keyId, usable: true)
    }

    @Test func theOwnersCheckOpensThisPhonesCopyAndSendsItOnce() async throws {
        let b = try blob()
        var sent: [String] = []
        var asked = 0
        var kept: CardSecret?
        let r = await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { b }, unlock: { asked += 1; return .key(keys.key) }) { s in
            sent.append(String(decoding: s.payload(), as: UTF8.self))
            kept = s
        }
        #expect(r == nil)
        #expect(asked == 1)
        #expect(sent == [#"{"n":"4242424242424242","c":"123"}"#])
        #expect(kept?.isEmpty == true)                                                  // wiped once sent
    }

    @Test func aCardAddedOnAnotherDeviceIsNotOnThisPhone() async throws {
        let b = try blob()
        var fetched = false
        let never: () async -> AutoPayOn.Unlock = { Issue.record("the owner was asked"); return .cancelled }
        #expect(await AutoPayOn.run(card: card(keyId: "android-other"), keyId: keys.keyId, blob: { fetched = true; return b }, unlock: never) { _ in } == .notOnPhone)
        #expect(await AutoPayOn.run(card: card(), keyId: nil, blob: { b }, unlock: never) { _ in } == .notOnPhone)   // no key here
        #expect(!fetched)
        #expect(await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { throw ApiError(status: 404, detail: "no copy") }, unlock: never) { _ in } == .notOnPhone)
    }

    @Test func backingOutOrAWrongKeySendsNothing() async throws {
        let b = try blob()
        var sent = false
        #expect(await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { b }, unlock: { .cancelled }) { _ in sent = true } == .cancelled)
        #expect(await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { b }, unlock: { .noLock }) { _ in sent = true } == .noLock)
        let wrong = SymmetricKey(size: .bits256)
        #expect(await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { b }, unlock: { .key(wrong) }) { _ in sent = true } == .notOnPhone)
        #expect(!sent)
    }

    @Test func theServerRefusingSaysWhy() async throws {
        let b = try blob()
        #expect(await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { b }, unlock: { .key(keys.key) }) { _ in
            throw ApiError(status: 422, detail: "last 4 differ") } == .mismatch)
        #expect(await AutoPayOn.run(card: card(), keyId: keys.keyId, blob: { b }, unlock: { .key(keys.key) }) { _ in
            throw ApiError(status: ApiError.noResponse, detail: "") } == .network)
    }
}
