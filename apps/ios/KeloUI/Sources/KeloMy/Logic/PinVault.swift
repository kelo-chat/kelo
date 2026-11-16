import Foundation
import CryptoKit
import KeloCore

// Payment PINs (결제 비밀번호), as Android wallet/PinVault.kt: a site's 4-8 digit PIN sealed on this iPhone with the wallet's own key
// (the same Keychain key as the end-to-end cards, released by Face ID / Touch ID / the passcode), saved on the server as ciphertext
// only, and opened here once for a payment the owner approved. The PIN never reaches the chat, the model or a log.

/// A payment PIN in memory as bytes, wiped once used. `description` never shows it.
public final class PinSecret: CustomStringConvertible, @unchecked Sendable {
    public static let min = 4, max = 8
    private var digits: [UInt8]

    private init(_ digits: [UInt8]) { self.digits = digits }

    public var length: Int { digits.count }

    /// Only 4-8 digits get in.
    public static func of(_ text: String) -> PinSecret? {
        let b = Array(text.utf8)
        guard (min...max).contains(b.count), b.allSatisfy({ (48...57).contains($0) }) else { return nil }
        return PinSecret(b)
    }

    /// {"p":"<digits>"}: the payload that is sealed. The caller wipes it.
    public func payload() -> [UInt8] { Array("{\"p\":\"".utf8) + digits + Array("\"}".utf8) }

    /// `,"pin":"…"` for a request body built as bytes.
    func field() -> [UInt8] { Array(",\"pin\":\"".utf8) + digits + Array("\"".utf8) }

    public func wipe() {
        for i in digits.indices { digits[i] = 0 }
        digits = []
    }

    public var description: String { "PinSecret(\(digits.count) digits)" }

    /// Reads an opened payload; the bytes are wiped here whatever happens.
    public static func parse(_ payload: inout [UInt8]) -> PinSecret? {
        defer { for i in payload.indices { payload[i] = 0 } }
        let key = Array("\"p\":\"".utf8)
        guard payload.count >= key.count else { return nil }
        for i in 0...(payload.count - key.count) where Array(payload[i..<(i + key.count)]) == key {
            var end = i + key.count
            while end < payload.count, (48...57).contains(payload[end]) { end += 1 }
            guard end < payload.count, payload[end] == UInt8(ascii: "\""), (min...max).contains(end - i - key.count) else { return nil }
            return PinSecret(Array(payload[(i + key.count)..<end]))
        }
        return nil
    }
}

/// The end-to-end copy of a payment PIN: the same scheme and phone key as a card's `CardBlob` (version | nonce | AES-GCM).
public enum PinBlob {
    public static let alg = "A256GCM-IKC-PIN-1"

    public static func seal(_ secret: PinSecret, key: SymmetricKey, keyId: String) throws -> E2eBlob {
        var plain = secret.payload()
        defer { for i in plain.indices { plain[i] = 0 } }
        let box = try AES.GCM.seal(plain, using: key)
        var out = Data([CardBlob.version])
        out.append(contentsOf: box.nonce)
        out.append(box.ciphertext)
        out.append(box.tag)
        return E2eBlob(blob: out.base64EncodedString(), alg: alg, keyId: keyId)
    }

    public static func open(_ blob: E2eBlob, key: SymmetricKey) -> PinSecret? {
        let n = CardBlob.nonceBytes
        guard blob.alg == alg, let raw = Data(base64Encoded: blob.blob), raw.count > 1 + n + 16, raw.first == CardBlob.version,
              let nonce = try? AES.GCM.Nonce(data: raw.subdata(in: 1..<(1 + n))),
              let box = try? AES.GCM.SealedBox(nonce: nonce, ciphertext: raw.subdata(in: (1 + n)..<(raw.count - 16)),
                                               tag: raw.subdata(in: (raw.count - 16)..<raw.count)),
              let plain = try? AES.GCM.open(box, using: key) else { return nil }
        var bytes = [UInt8](plain)
        return PinSecret.parse(&bytes)
    }

    /// This phone can open it (made here with this phone's key).
    public static func ours(_ pin: PaymentPin, keyId: String?) -> Bool { keyId != nil && pin.e2eAlg == alg && pin.e2eKeyId == keyId }
}

/// What the PIN form checks before saving.
public enum PinRules {
    /// The PIN as typed: digits only, at most `PinSecret.max`.
    public static func clean(_ text: String) -> String { String(text.filter { $0.isASCII && $0.isNumber }.prefix(PinSecret.max)) }

    public static func ready(pin: String, again: String, site: String) -> Bool {
        (PinSecret.min...PinSecret.max).contains(pin.count) && pin == again && siteOK(site)
    }

    public static func mismatch(pin: String, again: String) -> Bool { again.count >= pin.count && !again.isEmpty && pin != again }

    /// A site's address: a host with a dot, or a URL.
    public static func siteOK(_ site: String) -> Bool {
        var h = site.trimmingCharacters(in: .whitespaces).lowercased()
        for p in ["https://", "http://"] where h.hasPrefix(p) { h = String(h.dropFirst(p.count)) }
        h = String(h.prefix { $0 != "/" })
        h = String(h.prefix { $0 != ":" })
        return h.contains(".") && h.allSatisfy { $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" } && !h.hasPrefix(".") && !h.hasSuffix(".")
    }
}

/// A JSON body with the PIN appended as bytes (it never becomes a String).
public enum PinJson {
    public static func body(_ fields: JSON, _ secret: PinSecret) -> Data {
        let head = Array(fields.compactString.utf8)
        var extra = secret.field()
        defer { for i in extra.indices { extra[i] = 0 } }
        var out = Array(head.dropLast())
        out.append(contentsOf: head.count == 2 ? Array(extra.dropFirst()) : extra)
        out.append(UInt8(ascii: "}"))
        return Data(out)
    }
}

/// The payment PIN endpoints (server.py PAYMENT PINS).
public struct PinCalls: Sendable {
    let api: KeloApi
    public init(api: KeloApi) { self.api = api }

    public func list() async throws -> [PaymentPin] { try await api.get("/api/payment-pins", as: PaymentPinList.self).items }
    public func blob(_ id: String) async throws -> E2eBlob { try await api.get("/api/payment-pins/\(KeloApi.segment(id))/blob") }
    public func delete(_ id: String) async throws { _ = try await api.request("DELETE", "/api/payment-pins/\(KeloApi.segment(id))") }

    /// POST /api/payment-pins: the ciphertext only. `requestId`: the chat's fill-in card it answers.
    public func save(site: String, label: String, digits: Int, e2e: E2eBlob, requestId: String?) async throws -> PinSaved {
        var body: [String: JSON] = ["site": .string(site.trimmingCharacters(in: .whitespaces)),
                                    "label": .string(label.trimmingCharacters(in: .whitespaces)), "digits": .number(Double(digits)),
                                    "e2e": ["blob": .string(e2e.blob), "alg": .string(e2e.alg), "key_id": .string(e2e.keyId)]]
        if let requestId { body["request_id"] = .string(requestId) }
        return try JSON.decode(PinSaved.self, from: try await api.request("POST", "/api/payment-pins", body: .object(body)))
    }

    /// POST /api/payment-pins/deliveries/{slot}: the opened PIN, once, for the payment the user approved.
    public func deliver(slotId: String, pinId: String, _ secret: PinSecret) async throws {
        var body = PinJson.body(["pin_id": .string(pinId)], secret)
        defer { body.resetBytes(in: 0..<body.count) }
        _ = try await api.requestBytes("POST", "/api/payment-pins/deliveries/\(KeloApi.segment(slotId))", body: body)
    }
}

/// Releases a payment PIN for one approved payment: its blob, the owner's check (which unlocks this phone's key), open, send to the
/// payment's slot, wipe. nil when it got there. The steps are passed in so the tests can run it.
public enum PinRelease {
    public static func run(pin: PaymentPin, keyId: String?, blob: () async throws -> E2eBlob, unlock: () async -> AutoPayOn.Unlock,
                           send: (PinSecret) async throws -> Void) async -> CardSendError? {
        guard PinBlob.ours(pin, keyId: keyId) else { return .notOnPhone }
        let b: E2eBlob
        do { b = try await blob() } catch let e as ApiError {
            return e.status == 404 ? .notOnPhone : (CardSendError.fromStatus(e.status) ?? .other)
        } catch { return .other }
        guard b.keyId == keyId, b.alg == PinBlob.alg else { return .notOnPhone }
        let key: SymmetricKey
        switch await unlock() {
        case .key(let k): key = k
        case .cancelled: return .cancelled
        case .noLock: return .noLock
        case .lost: return .notOnPhone
        }
        guard let secret = PinBlob.open(b, key: key) else { return .notOnPhone }
        defer { secret.wipe() }
        do {
            try await send(secret)
            return nil
        } catch let e as ApiError {
            return CardSendError.fromStatus(e.status) ?? .other
        } catch {
            return .other
        }
    }
}
