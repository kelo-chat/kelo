import Foundation
import Testing
@testable import KeloCore

/// Port of android keyguard/KeyguardRulesTest.kt (the parts that are not Android's Keystore) with the software key: the rules, the
/// view, enrol (wrap -> finish -> recovery code) and unlock (blob -> unwrap -> the key sent as bytes).
@Suite struct KeyguardFlowTests {
    let masterKey = "Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5d2Fs"

    @Test func recoveryCodesAreReadTheWayPeopleTypeThem() {
        #expect(KeyguardRules.normalizeCode(" abcd-efgh jklm-npqr stuv-wxyz ") == "ABCDEFGHJKLMNPQRSTUVWXYZ")
        #expect(KeyguardRules.normalizeCode("ａｂｃ１２") == "")   // full-width letters are not the code's ASCII
        #expect(KeyguardRules.groupCode("ABCDEFGHJKLMNPQRSTUVWXYZ") == "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ")
        #expect(KeyguardRules.codeLines("ABCDEFGHJKLMNPQRSTUVWXYZ") == "ABCD-EFGH-JKLM\nNPQR-STUV-WXYZ")
        #expect(KeyguardRules.codeReady("ABCD-EFGH-JKLM-NPQR-STUV-WXYZ"))
        #expect(!KeyguardRules.codeReady("ABCD-EFGH"))
    }

    @Test func theViewAndTheLockedAnswer() throws {
        let v = try JSONDecoder().decode(KeyguardView.self, from: Data(#"{"mode":"phone","locked":true,"env_key":false,"recovery":true,"phones":[{"device_id":7,"name":"iPhone","alg":2,"created":1.0}]}"#.utf8))
        #expect(v.on && v.locked)
        #expect(v.holds(7) && !v.holds(8) && !v.holds(nil))
        #expect(!KeyguardView().on)
        #expect(KeyguardRules.isLocked(ApiError.from(status: 423, body: Data(#"{"detail":"Kelo is locked","code":"locked"}"#.utf8))))
        #expect(KeyguardRules.lastPhoneCodes.contains("last_keyguard_phone") && KeyguardRules.lastPhoneCodes.contains("last_phone"))
        let answer = EnrollAnswer(enrollmentId: "e", key: "secret-key-text", enabling: true, recoveryCode: "CODE")
        #expect(!String(describing: answer).contains("secret-key-text") && !String(reflecting: answer).contains("CODE"))
        #expect(try JSONDecoder().decode(AgentStatus.self, from: Data(#"{"busy":false,"locked":true}"#.utf8)).locked)
        #expect(try !JSONDecoder().decode(AgentStatus.self, from: Data(#"{"busy":false}"#.utf8)).locked)
    }

    @Test func enrolWrapsAFreshKeyAndShowsTheCodeOnce() async throws {
        let t = FakeTransport(.ok(200, #"{"enrollment_id":"en1","key":"\#(masterKey)","enabling":true,"recovery_code":"ABCDEFGHJKLMNPQRSTUVWXYZ"}"#),
                              .ok(200, #"{"mode":"phone","locked":false,"phones":[{"device_id":3,"name":"iPhone","alg":2}]}"#))
        let key = SoftwareKeyguardKey()
        let (view, code) = try await KeyguardFlow.enroll(calls: KeyguardCalls(api: KeloApi(baseUrl: "https://k.test", token: "t", transport: t)), key: key)
        #expect(view.on && view.holds(3) && code == "ABCDEFGHJKLMNPQRSTUVWXYZ")
        let finish = try JSON.parse(t.last.httpBody ?? Data())
        #expect(t.last.url?.path == "/api/keyguard/enroll/finish" && finish["enrollment_id"] == .string("en1") && finish["alg"] == .number(2))
        let blob = try #require(Data(base64Encoded: finish["blob"]?.stringValue ?? ""))
        #expect(blob.first == 0x02 && blob.range(of: Data(masterKey.utf8)) == nil)
        #expect(try await key.unwrap(blob, reason: "") == Data(masterKey.utf8))
    }

    @Test func unlockSendsTheKeyAsBytesOnce() async throws {
        let key = SoftwareKeyguardKey()
        try key.create()
        let blob = try key.wrap(Data(masterKey.utf8))
        let t = FakeTransport(.ok(200, #"{"alg":2,"blob":"\#(blob.base64EncodedString())"}"#), .ok(200, #"{"ok":true,"locked":false}"#))
        try await KeyguardFlow.unlock(calls: KeyguardCalls(api: KeloApi(baseUrl: "https://k.test", token: "t", transport: t)), key: key, reason: "Unlock Kelo")
        #expect(t.requests.value.map { $0.url?.path ?? "" } == ["/api/keyguard/blob", "/api/keyguard/unlock"])
        #expect(try JSON.parse(t.last.httpBody ?? Data())["key"] == .string(masterKey))
    }

    @Test func unlockSaysWhyNot() async throws {
        let api = { (t: FakeTransport) in KeyguardCalls(api: KeloApi(baseUrl: "https://k.test", token: "t", transport: t)) }
        // no copy on this phone
        await #expect(throws: KeyguardFlow.Problem.notHere) {
            try await KeyguardFlow.unlock(calls: api(FakeTransport(.ok(404, #"{"detail":"not enrolled"}"#))), key: SoftwareKeyguardKey(), reason: "")
        }
        // the owner backs out
        let k = SoftwareKeyguardKey(allow: { _ in false })
        try k.create()
        let blob = try k.wrap(Data("x".utf8)).base64EncodedString()
        await #expect(throws: KeyguardFlow.Problem.cancelled) {
            try await KeyguardFlow.unlock(calls: api(FakeTransport(.ok(200, #"{"alg":2,"blob":"\#(blob)"}"#))), key: k, reason: "")
        }
        // another phone's blob, or this phone's key is gone
        await #expect(throws: KeyguardFlow.Problem.keyLost) {
            try await KeyguardFlow.unlock(calls: api(FakeTransport(.ok(200, #"{"alg":2,"blob":"\#(blob)"}"#))), key: SoftwareKeyguardKey(), reason: "")
        }
        // the server says the key is wrong
        let ok = SoftwareKeyguardKey()
        try ok.create()
        let mine = try ok.wrap(Data("x".utf8)).base64EncodedString()
        await #expect(throws: KeyguardFlow.Problem.keyLost) {
            try await KeyguardFlow.unlock(calls: api(FakeTransport(.ok(200, #"{"alg":2,"blob":"\#(mine)"}"#), .ok(422, #"{"detail":"wrong","code":"wrong_key"}"#))),
                                          key: ok, reason: "")
        }
    }

    @Test func oneKeyPerServer() {
        #expect(SecureEnclaveKeyguardKey.account(serverUrl: "https://A.example/") == SecureEnclaveKeyguardKey.account(serverUrl: "https://a.example"))
        #expect(SecureEnclaveKeyguardKey.account(serverUrl: "https://a.example") != SecureEnclaveKeyguardKey.account(serverUrl: "https://b.example"))
    }
}
