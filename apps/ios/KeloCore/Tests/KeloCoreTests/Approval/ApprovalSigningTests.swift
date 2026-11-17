import Testing
import Foundation
import CryptoKit
@testable import KeloCore

/// The vectors of docs/security/approval-sign-vectors.json (test key only): the message bytes, three signatures that verify, two that do not.
private let pub = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEdGbVG+hSOxAy5lIiPnDCkO8UdPWowk9uk9KPWuMsvzxk0EtJPjUSU0Bh2WPJXsL8zIIBKXwwTvSbr0aMuleDCQ=="

private func signHex(_ s: String) -> Data {
    var d = Data(); var i = s.startIndex
    while i < s.endIndex { let j = s.index(i, offsetBy: 2); d.append(UInt8(s[i..<j], radix: 16)!); i = j }
    return d
}

@Suite struct ApprovalSigningTests {
    @Test func messageBytesMatchTheVectors() {
        #expect(ApprovalSignRules.message(approvalId: "3f9c0a1e5b7d42c8a6e1f0b9d2c4a7e3", approve: true, issuedAt: 1789000000)
            == signHex("6b656c6f2d617070726f76616c2076310a33663963306131653562376434326338613665316630623964326334613765330a617070726f76650a31373839303030303030"))
        #expect(ApprovalSignRules.message(approvalId: "b1e4d7a20c9f4e6b8d3a5c7e9f1b2d4a", approve: true, issuedAt: 1789000123)
            == signHex("6b656c6f2d617070726f76616c2076310a62316534643761323063396634653662386433613563376539663162326434610a617070726f76650a31373839303030313233"))
        #expect(ApprovalSignRules.message(approvalId: "6a2c8e4f0b1d3c5e7a9b2d4f6e8c0a1b", approve: false, issuedAt: 1789003600)
            == signHex("6b656c6f2d617070726f76616c2076310a36613263386534663062316433633565376139623264346636653863306131620a64656e790a31373839303033363030"))
    }

    @Test func theThreeApprovalsVerifyAndTheTwoBadOnesDoNot() {
        #expect(ApprovalSignRules.verify(publicKey: pub, message: signHex("6b656c6f2d617070726f76616c2076310a33663963306131653562376434326338613665316630623964326334613765330a617070726f76650a31373839303030303030"),
            sig: "MEQCICnnFjavXb4sJpNtfpizVogreaEB3+N3DRDCr5HKRl28AiA5/IZyaq0VTStnIMXpnyOSIqIMRhhxStdT0tujHzY+tA=="))
        #expect(ApprovalSignRules.verify(publicKey: pub, message: signHex("6b656c6f2d617070726f76616c2076310a62316534643761323063396634653662386433613563376539663162326434610a617070726f76650a31373839303030313233"),
            sig: "MEYCIQDr8F+z1Hg8Yd68B0pU76o87gTXUlqJYjNxjWICk66tKgIhALeQH4yAAdoC6cexqIw8yK8DSVmLG4uAB+NxSVU/be9p"))
        #expect(ApprovalSignRules.verify(publicKey: pub, message: signHex("6b656c6f2d617070726f76616c2076310a36613263386534663062316433633565376139623264346636653863306131620a64656e790a31373839303033363030"),
            sig: "MEQCIF/SIp6fyUsJsAFm5cd+eDVqtKd72dzB8k1M3Q2uk+aLAiAmGPrEy8r7XwAduM8Xe7CculVafBQjNky3YLHOKxmzKQ=="))
        // issued_at changed
        #expect(!ApprovalSignRules.verify(publicKey: pub, message: signHex("6b656c6f2d617070726f76616c2076310a33663963306131653562376434326338613665316630623964326334613765330a617070726f76650a31373839303030303031"),
            sig: "MEQCICnnFjavXb4sJpNtfpizVogreaEB3+N3DRDCr5HKRl28AiA5/IZyaq0VTStnIMXpnyOSIqIMRhhxStdT0tujHzY+tA=="))
        // one signature byte flipped
        #expect(!ApprovalSignRules.verify(publicKey: pub, message: signHex("6b656c6f2d617070726f76616c2076310a33663963306131653562376434326338613665316630623964326334613765330a617070726f76650a31373839303030303030"),
            sig: "MEQCICnnFjavXb8sJpNtfpizVogreaEB3+N3DRDCr5HKRl28AiA5/IZyaq0VTStnIMXpnyOSIqIMRhhxStdT0tujHzY+tA=="))
    }

    @Test func paymentRuleMirrorsPythonTruthiness() throws {
        func p(_ s: String) throws -> Bool { ApprovalSignRules.isPayment(try JSON.parse(Data(s.utf8))) }
        #expect(try p(#"{"payment":{"site":"x"}}"#))
        #expect(try p(#"{"name":"confirm_purchase"}"#))
        #expect(try p(#"{"commit":"pay"}"#))
        #expect(try !p(#"{"payment":{}}"#))
        #expect(try !p(#"{"payment":""}"#))
        #expect(try !p(#"{"payment":null,"name":"x"}"#))
        #expect(try !p(#"{"commit":"submit"}"#))
    }

    @Test func aSoftwareKeySignsWhatTheServerVerifiesAndAllowAsksFirst() async {
        let signer = SoftwareApprovalSigner(defaults: UserDefaults(suiteName: "approval-test-1")!)
        let out = await ApprovalSigning.signPaymentAllow(signer, approvalId: "a1", issuedAt: 1789000000, title: "t", subtitle: "s")
        guard case .ready(let sig?) = out else { Issue.record("no signature"); return }
        #expect(ApprovalSignRules.verify(publicKey: signer.publicKey()!, message: ApprovalSignRules.message(approvalId: "a1", approve: true, issuedAt: 1789000000), sig: sig))
        // another issued_at does not verify
        #expect(!ApprovalSignRules.verify(publicKey: signer.publicKey()!, message: ApprovalSignRules.message(approvalId: "a1", approve: true, issuedAt: 1789000001), sig: sig))
        // the owner backs out: nothing is signed
        let no = SoftwareApprovalSigner(defaults: UserDefaults(suiteName: "approval-test-1")!, allow: { _ in false })
        #expect(await ApprovalSigning.signPaymentAllow(no, approvalId: "a1", issuedAt: 1, title: "t", subtitle: "s") == .cancelled)
        // no key on this device: sent unsigned
        #expect(await ApprovalSigning.signPaymentAllow(NoApprovalSigner(), approvalId: "a1", issuedAt: 1, title: "t", subtitle: "s") == .ready(nil))
    }

    @Test func theKeyIsRegisteredOncePerServer() async {
        func api(_ t: FakeTransport) -> KeloApi { KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "" }) }
        let defaults = UserDefaults(suiteName: "approval-test-2")!
        defaults.removePersistentDomain(forName: "approval-test-2")
        let signer = SoftwareApprovalSigner(defaults: defaults)
        let t = FakeTransport(.ok(200, #"{"ok":true}"#), .ok(200, #"{"ok":true}"#))
        await ApprovalKeyRegistrar.refresh(signer, api: api(t))
        await ApprovalKeyRegistrar.refresh(signer, api: api(t))
        #expect(t.requests.value.count == 1)
        #expect(t.last.url?.path == "/api/devices/approval-key")
        #expect(t.lastBody.contains("ES256") && t.lastBody.contains(signer.publicKey()!))
        // 404 (an older server): not remembered, asked again
        let d3 = UserDefaults(suiteName: "approval-test-3")!
        d3.removePersistentDomain(forName: "approval-test-3")
        let s2 = SoftwareApprovalSigner(defaults: d3)
        let old = FakeTransport(.ok(404, #"{"detail":"no"}"#), .ok(404, #"{"detail":"no"}"#))
        await ApprovalKeyRegistrar.refresh(s2, api: api(old))
        await ApprovalKeyRegistrar.refresh(s2, api: api(old))
        #expect(old.requests.value.count == 2)
    }

    @Test func aSignatureRefusalIsRecognisedAndCarriesTheServersMessage() {
        let e = ApiError(status: 403, detail: "x", detailJson: ["code": .string("signature_required"), "message": .string("Approve on your phone")], code: "signature_required")
        #expect(ApprovalSigning.isSignature(e))
        #expect(ApprovalSigning.serverMessage(e) == "Approve on your phone")
        #expect(!ApprovalSigning.isSignature(ApiError(status: 403, detail: "x", code: "other")))
    }

    @Test func theEventCarriesIssuedAtAndSignable() throws {
        let ev = TurnEvent.parse(try JSON.parse(Data(#"{"type":"approval_request","approval_id":"a1","name":"confirm_purchase","issued_at":1789000000,"payment":{"site":"s"}}"#.utf8)))
        guard case .approvalRequest(let a) = ev else { Issue.record("not an approval"); return }
        #expect(a.issuedAt == 1789000000 && a.signable)
        let plain = TurnEvent.parse(try JSON.parse(Data(#"{"type":"approval_request","approval_id":"a2","name":"send_mail"}"#.utf8)))
        guard case .approvalRequest(let b) = plain else { Issue.record("not an approval"); return }
        #expect(!b.signable)
    }
}
