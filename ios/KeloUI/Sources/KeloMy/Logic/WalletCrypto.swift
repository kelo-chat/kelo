import Foundation
import CryptoKit
import KeloCore

/// A card number and CVC in memory as bytes, wiped once used (Android wallet/CardBlob.kt CardSecret). `description` shows only the
/// last four digits.
public final class CardSecret: CustomStringConvertible, @unchecked Sendable {
    private var number: [UInt8]
    private var cvc: [UInt8]

    private init(number: [UInt8], cvc: [UInt8]) { self.number = number; self.cvc = cvc }

    /// Only digits get in, so the payload never needs escaping.
    public static func of(number: String, cvc: String) -> CardSecret? {
        guard !number.isEmpty, !cvc.isEmpty, number.allSatisfy({ $0.isASCII && $0.isNumber }), cvc.allSatisfy({ $0.isASCII && $0.isNumber })
        else { return nil }
        return CardSecret(number: Array(number.utf8), cvc: Array(cvc.utf8))
    }

    public var last4: String { String(decoding: number.suffix(4), as: UTF8.self) }

    /// {"n":"<digits>","c":"<digits>"} as UTF-8 bytes. The caller wipes them.
    public func payload() -> [UInt8] { Array("{\"n\":\"".utf8) + number + Array("\",\"c\":\"".utf8) + cvc + Array("\"}".utf8) }

    /// `,"number":"…","cvc":"…"` for `SecretJson`.
    func fields() -> [UInt8] { Array(",\"number\":\"".utf8) + number + Array("\",\"cvc\":\"".utf8) + cvc + Array("\"".utf8) }

    public func wipe() {
        for i in number.indices { number[i] = 0 }
        for i in cvc.indices { cvc[i] = 0 }
        number = []; cvc = []
    }

    public var description: String { "CardSecret(\(CardMask.dots)\(last4))" }

    /// Reads a decrypted payload; the bytes are wiped here whatever happens.
    public static func parse(_ payload: inout [UInt8]) -> CardSecret? {
        defer { for i in payload.indices { payload[i] = 0 } }
        func field(_ key: String) -> [UInt8]? {
            let k = Array(key.utf8)
            guard payload.count >= k.count else { return nil }
            for i in 0...(payload.count - k.count) where Array(payload[i..<(i + k.count)]) == k {
                var end = i + k.count
                while end < payload.count, (48...57).contains(payload[end]) { end += 1 }
                guard end < payload.count, payload[end] == UInt8(ascii: "\"") else { return nil }
                return Array(payload[(i + k.count)..<end])
            }
            return nil
        }
        guard let n = field("\"n\":\""), let c = field("\"c\":\""), !n.isEmpty, !c.isEmpty else { return nil }
        return CardSecret(number: n, cvc: c)
    }
}

/// The end-to-end copy of a card: {number, cvc} sealed on the phone with a key only this phone holds (the Keychain, usable only
/// after Face ID / Touch ID / the passcode). blob = base64(version 1 | 12-byte nonce | AES-GCM ciphertext and tag). `alg` names
/// the scheme and `key_id` the phone's key, so a phone can tell a blob it cannot open (made on another phone).
public enum CardBlob {
    public static let alg = "A256GCM-IKC-1"
    static let version: UInt8 = 1
    static let nonceBytes = 12

    public static func seal(_ secret: CardSecret, key: SymmetricKey, keyId: String) throws -> E2eBlob {
        var plain = secret.payload()
        defer { for i in plain.indices { plain[i] = 0 } }
        let box = try AES.GCM.seal(plain, using: key)
        var out = Data([version])
        out.append(contentsOf: box.nonce)
        out.append(box.ciphertext)
        out.append(box.tag)
        return E2eBlob(blob: out.base64EncodedString(), alg: alg, keyId: keyId)
    }

    public static func open(_ blob: E2eBlob, key: SymmetricKey) -> CardSecret? {
        guard blob.alg == alg, let raw = Data(base64Encoded: blob.blob), raw.count > 1 + nonceBytes + 16, raw.first == version,
              let nonce = try? AES.GCM.Nonce(data: raw.subdata(in: 1..<(1 + nonceBytes))),
              let box = try? AES.GCM.SealedBox(nonce: nonce, ciphertext: raw.subdata(in: (1 + nonceBytes)..<(raw.count - 16)),
                                               tag: raw.subdata(in: (raw.count - 16)..<raw.count)),
              let plain = try? AES.GCM.open(box, using: key) else { return nil }
        var bytes = [UInt8](plain)
        return CardSecret.parse(&bytes)
    }

    /// Whether a card's stored blob was made with this phone's key (nil keyId: this phone has no wallet key).
    public static func ours(_ card: WalletCard, keyId: String?) -> Bool {
        card.e2e && keyId != nil && card.e2eAlg == alg && card.e2eKeyId == keyId
    }

    /// The key's id: a short hash of the key, so the server can tell phones apart without learning anything.
    public static func keyId(_ key: SymmetricKey) -> String {
        let h = SHA256.hash(data: key.withUnsafeBytes { Data($0) } + Data("kelo-wallet-key-id".utf8))
        return h.prefix(8).map { String(format: "%02x", $0) }.joined()
    }
}

/// A JSON request body that carries a card number and CVC: the ordinary fields as usual, the secret ones appended as bytes.
public enum SecretJson {
    public static func body(_ fields: JSON, _ secret: CardSecret) -> Data {
        let head = Array(fields.compactString.utf8)
        var extra = secret.fields()
        defer { for i in extra.indices { extra[i] = 0 } }
        var out = Array(head.dropLast())
        out.append(contentsOf: head.count == 2 ? Array(extra.dropFirst()) : extra)
        out.append(UInt8(ascii: "}"))
        return Data(out)
    }
}

/// When the card pages are open: after the owner's check, closing again when the app goes to the background or after two minutes
/// without a touch (Android WalletLock). Times are seconds from a monotonic clock.
public struct WalletLock: Sendable {
    public static let timeout: Double = 120
    private var lastActive: Double?

    public init() {}

    public mutating func unlock(_ now: Double) { lastActive = now }
    public mutating func touch(_ now: Double) { if isOpen(now) { lastActive = now } }
    public mutating func lock() { lastActive = nil }

    public mutating func isOpen(_ now: Double) -> Bool {
        guard let at = lastActive else { return false }
        if now - at >= Self.timeout || now < at { lastActive = nil; return false }
        return true
    }
}

/// Why a card could not be sent for a payment the user allowed (Android CardSendError / DeliveryRules).
public enum CardSendError: Equatable, Sendable {
    case notWaiting, mismatch, notOnPhone, noLock, cancelled, network, other
    /// The server refuses auto-pay without Keyguard (409 keyguard_required)
    case keyguardRequired

    public static func fromStatus(_ status: Int) -> CardSendError? {
        switch status {
        case 200...299, 409: nil
        case 404: .notWaiting
        case 422: .mismatch
        case ApiError.noResponse: .network
        default: .other
        }
    }
}

/// Turning auto-pay on without typing the card again (Android wallet/WalletCalls.kt AutoPayOn): this phone's own end-to-end copy (its
/// blob from the server) is opened with the key the owner's Face ID / Touch ID / passcode unlocks, and the number and CVC go once
/// over TLS to the auto-pay endpoint, which seals them. Nothing is logged or shown; the secret is wiped whatever happens.
/// nil = auto-pay is on; `.notOnPhone` = this phone holds no copy it can open (added on another device), so the owner turns it on
/// there or types the number.
public enum AutoPayOn {
    /// What the owner's check came to.
    public enum Unlock: Sendable { case key(SymmetricKey), cancelled, noLock, lost }

    public static func run(card: WalletCard, keyId: String?, blob: () async throws -> E2eBlob, unlock: () async -> Unlock,
                           send: (CardSecret) async throws -> Void) async -> CardSendError? {
        guard CardBlob.ours(card, keyId: keyId) else { return .notOnPhone }
        let b: E2eBlob
        do { b = try await blob() } catch let e as ApiError {
            return e.status == 404 ? .notOnPhone : (CardSendError.fromStatus(e.status) ?? .other)
        } catch { return .other }
        guard b.keyId == keyId, b.alg == CardBlob.alg else { return .notOnPhone }
        let key: SymmetricKey
        switch await unlock() {
        case .key(let k): key = k
        case .cancelled: return .cancelled
        case .noLock: return .noLock
        case .lost: return .notOnPhone
        }
        guard let secret = CardBlob.open(b, key: key) else { return .notOnPhone }
        defer { secret.wipe() }
        do {
            try await send(secret)
            return nil
        } catch let e as ApiError {
            if e.code == AutoPayGate.codeKeyguardRequired { return .keyguardRequired }
            return e.status == 422 ? .mismatch : (CardSendError.fromStatus(e.status) ?? .other)
        } catch {
            return .other
        }
    }
}

/// Auto-pay needs Keyguard (owner's decision, 2026-11-10; Android wallet/WalletModels.kt AutoPayGate): the server keeps a copy of an
/// auto-pay card, and only with Keyguard on does that copy's lock open with the owner's phone alone. Turning auto-pay on with Keyguard
/// off (or unknown) asks to turn Keyguard on first; the server refuses it too (409 {"code": "keyguard_required"}); an auto-pay card on
/// a server without Keyguard is paused (each payment asks on the phone) until it is turned on. `keyguardOn`: GET /api/keyguard
/// "mode" is "phone"; nil = not known.
public enum AutoPayGate {
    public static let codeKeyguardRequired = "keyguard_required"
    /// The card's `ask_reason` when its auto-pay waits for Keyguard (the server's word for it, when it sends one).
    public static let reasonKeyguard = "keyguard_off"

    /// Auto-pay may be turned on now; otherwise the "Auto-pay needs Keyguard" dialog shows.
    public static func mayTurnOn(_ keyguardOn: Bool?) -> Bool { keyguardOn == true }

    /// The card's auto-pay waits for Keyguard (the card page says so, with "Turn on Keyguard").
    public static func pausedForKeyguard(_ card: WalletCard, keyguardOn: Bool?) -> Bool {
        card.autoPay && !card.expired && (card.askReason == reasonKeyguard || keyguardOn == false)
    }
}
