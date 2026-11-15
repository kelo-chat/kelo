import Testing
import Foundation
import KeloCore
import CryptoKit
@testable import KeloShared
@testable import KeloMy

/// Port of android keyguard/KeyguardRulesTest.kt (autoPayNeedsKeyguard, a423AnywhereShowsKeloIsLocked…, theLockedNotificationOpensTheUnlock).
struct KeyguardUiTests {
    @Test func autoPayNeedsKeyguard() {
        #expect(AutoPayGate.mayTurnOn(true))
        #expect(!AutoPayGate.mayTurnOn(false))
        #expect(!AutoPayGate.mayTurnOn(nil))   // not known: the dialog too (the server refuses it anyway)
        let auto = WalletCard(id: "c1", autoPay: true, usable: true)
        #expect(AutoPayGate.pausedForKeyguard(auto, keyguardOn: false))
        #expect(!AutoPayGate.pausedForKeyguard(auto, keyguardOn: true))
        #expect(!AutoPayGate.pausedForKeyguard(auto, keyguardOn: nil))
        var told = auto; told.askReason = AutoPayGate.reasonKeyguard
        #expect(AutoPayGate.pausedForKeyguard(told, keyguardOn: nil))   // the server's word
        var off = auto; off.autoPay = false
        #expect(!AutoPayGate.pausedForKeyguard(off, keyguardOn: false))
        var expired = auto; expired.expired = true
        #expect(!AutoPayGate.pausedForKeyguard(expired, keyguardOn: false))
    }

    @MainActor @Test func aPausedCardSaysSoInItsRow() {
        Strings.shared.use("en")
        let auto = WalletCard(id: "c1", autoPay: true, usable: true)
        #expect(walletAutoLine(auto, keyguardOn: false) == L(R.string.wallet_auto_paused_keyguard))
        #expect(walletAutoLine(auto, keyguardOn: true) != L(R.string.wallet_auto_paused_keyguard))
    }

    @Test func theServerRefusingAutoPayAsksForKeyguard() async {
        let card = WalletCard(id: "c", autoPay: false, e2e: true, e2eAlg: CardBlob.alg, e2eKeyId: "k")
        let r = await AutoPayOn.run(card: card, keyId: "k", blob: { E2eBlob(blob: "", alg: CardBlob.alg, keyId: "k") },
                                    unlock: { .key(.init(size: .bits256)) }, send: { _ in })
        // the blob does not open with this key: not on this phone (the gate is checked before; the 409 mapping below)
        #expect(r == .notOnPhone)
        let key = SymmetricKey(size: .bits256)
        let blob = try! CardBlob.seal(CardSecret.of(number: "4242424242424242", cvc: "123")!, key: key, keyId: "k")
        let refused = await AutoPayOn.run(card: card, keyId: "k", blob: { blob }, unlock: { .key(key) }, send: { _ in
            throw ApiError.from(status: 409, body: Data(#"{"detail":"Keyguard first","code":"keyguard_required"}"#.utf8))
        })
        #expect(refused == .keyguardRequired)
    }

    @Test func theLockedNotificationOpensTheUnlock() {
        #expect(OpenTarget.decode(OpenTarget.unlock.encoded) == .unlock)
        #expect(NotifyRules.target(ServerNotification(id: 1, type: NoteTypes.keyguardLocked)) == .unlock)
        #expect(NoteTypes.all.contains("keyguard_locked"))
        #expect(MyRoute.of(.keyguardCode) == .keyguard(code: true))
        #expect(MyRoute.of(.keyguard) == .keyguard(code: false))
    }

    @MainActor @Test func a423AnywhereShowsKeloIsLockedAndTheStatusClearsIt() async {
        let t = LockTransport()
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
                           prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-kg-\(UUID().uuidString)")!), transport: t,
                           cache: ScreenCache(directory: nil, key: { nil }))
        _ = try? await app.requireApi().request("GET", "/api/vault")
        for _ in 0..<100 where !app.locked { try? await Task.sleep(nanoseconds: 10_000_000) }
        #expect(app.locked)
        app.statusPolled(try! JSONDecoder().decode(AgentStatus.self, from: Data(#"{"busy":false,"locked":false}"#.utf8)))
        #expect(!app.locked)
        app.router.open(.unlock)
        #expect(app.router.unlockRequested && app.router.tab == .chat)
    }
}

private final class LockTransport: HTTPTransport, @unchecked Sendable {
    func send(_ request: URLRequest) async throws -> (Int, Data) {
        request.url?.path == "/api/vault" ? (423, Data(#"{"detail":"Kelo is locked","code":"locked"}"#.utf8)) : (200, Data("{}".utf8))
    }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        (200, AsyncThrowingStream { $0.finish() })
    }
}
