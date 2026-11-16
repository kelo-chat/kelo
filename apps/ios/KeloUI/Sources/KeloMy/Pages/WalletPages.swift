import SwiftUI
import CryptoKit
import LocalAuthentication
import Security
import KeloCore
import KeloShared

/// This iPhone's card key: AES-256 in the Keychain, readable only after Face ID / Touch ID / the passcode
/// (`.userPresence`, this device only). Cards are sealed with it before they go to the server (Android wallet/WalletKey.kt).
enum WalletKey {
    private static let service = "chat.kelo.app.wallet"
    private static let account = "card-key-v1"

    /// The key's id (not secret), kept outside the Keychain so the list can tell its cards without asking for Face ID.
    static func keyId(prefs: Prefs = .shared) -> String? { prefs.string("wallet.key_id") }

    enum Failure: Error { case noLock, cancelled, lost }

    /// The key after the owner's check (made on first use). `reason` is shown in the system prompt.
    static func key(reason: String, prefs: Prefs = .shared) async throws -> SymmetricKey {
        let ctx = LAContext()
        var err: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else {
            throw err?.code == LAError.passcodeNotSet.rawValue ? Failure.noLock : Failure.cancelled
        }
        do {
            guard try await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) else { throw Failure.cancelled }
        } catch let e as NSError where e.domain == LAError.errorDomain {
            throw e.code == LAError.passcodeNotSet.rawValue ? Failure.noLock : Failure.cancelled
        }
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecReturnData as String: true,
                                kSecUseAuthenticationContext as String: ctx]
        var out: CFTypeRef?
        if SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data, d.count == 32 {
            let k = SymmetricKey(data: d)
            if keyId(prefs: prefs) == nil { prefs.set("wallet.key_id", CardBlob.keyId(k)) }
            return k
        }
        if keyId(prefs: prefs) != nil {
            // the key is gone (the passcode was removed, the phone restored): forget it, cards made with it are not usable here
            prefs.set("wallet.key_id", nil as String?)
        }
        let k = SymmetricKey(size: .bits256)
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, .userPresence, nil) else { throw Failure.lost }
        q = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
             kSecValueData as String: k.withUnsafeBytes { Data($0) }, kSecAttrAccessControl as String: access,
             kSecUseAuthenticationContext as String: ctx]
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account] as CFDictionary)
        guard SecItemAdd(q as CFDictionary, nil) == errSecSuccess else { throw Failure.lost }
        prefs.set("wallet.key_id", CardBlob.keyId(k))
        return k
    }
}

/// What a saved card can do on this phone (Android CardStatusRules).
enum CardStatus: Equatable { case usable, autoOnly, notOnPhone, expired, unfinished
    static func of(_ card: WalletCard, keyId: String?) -> CardStatus {
        if card.expired { return .expired }
        if CardBlob.ours(card, keyId: keyId) { return .usable }
        if card.serverCopy { return .autoOnly }
        if card.e2e { return .notOnPhone }
        return .unfinished
    }
}

/// Sends a card for a payment the owner allowed (the chat's payment card calls this): opens the phone's copy after Face ID and
/// posts it once to POST /api/wallet/deliveries/{approval_id}. Exposed for the chat; never stores the number.
public enum WalletDelivery {
    @MainActor public static func deliver(api: KeloApi, approvalId: String, cardId: String) async -> CardSendError? {
        do {
            let blob: E2eBlob = try await api.get("/api/wallet/cards/\(KeloApi.segment(cardId))/blob")
            let key: SymmetricKey
            do { key = try await WalletKey.key(reason: L(R.string.wallet_auth_pay_subtitle)) }
            catch WalletKey.Failure.noLock { return .noLock }
            catch WalletKey.Failure.cancelled { return .cancelled }
            catch { return .notOnPhone }
            guard let secret = CardBlob.open(blob, key: key) else { return .notOnPhone }
            defer { secret.wipe() }
            var body = SecretJson.body(["card_id": .string(cardId)], secret)
            defer { body.resetBytes(in: 0..<body.count) }
            try await api.requestBytes("POST", "/api/wallet/deliveries/\(KeloApi.segment(approvalId))", body: body)
            return nil
        } catch let e as ApiError {
            return CardSendError.fromStatus(e.status)
        } catch {
            return .other
        }
    }
}

/// Turning auto-pay on from this iPhone without typing the card again (`AutoPayOn` with this phone's Keychain key): the card's blob from
/// the server, Face ID / Touch ID / the passcode (which releases the key), open it here, PATCH {"auto_pay": true, number, cvc} once.
enum WalletAutoPay {
    @MainActor static func turnOn(api: KeloApi, card: WalletCard) async -> CardSendError? {
        let path = "/api/wallet/cards/\(KeloApi.segment(card.id))"
        let reason = L(R.string.wallet_auth_auto_subtitle, card.label.isEmpty ? card.last4 : card.label)
        return await AutoPayOn.run(card: card, keyId: WalletKey.keyId(), blob: { try await api.get("\(path)/blob") }, unlock: {
            do { return .key(try await WalletKey.key(reason: reason)) }
            catch WalletKey.Failure.noLock { return .noLock }
            catch WalletKey.Failure.cancelled { return .cancelled }
            catch { return .lost }
        }, send: { secret in
            var body = SecretJson.body(["auto_pay": .bool(true)], secret)
            defer { body.resetBytes(in: 0..<body.count) }
            _ = try await api.requestBytes("PATCH", path, body: body)
        })
    }
}

/// 마이 → 결제 카드. Opening it always takes the owner's check; it locks again when the app goes to the background or after two
/// minutes without a touch. Card numbers never leave the phone unsealed, and are never shown.
struct WalletPage: View {
    let api: KeloApi
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @Environment(\.kelo) private var c
    @State private var lock = WalletLock()
    @State private var open = false
    @State private var noLock = false
    @State private var asking = false

    var body: some View {
        Group {
            if open {
                WalletOpenView(api: api, keyguard: { await app.refreshKeyguard() }, onBack: { leave() }, touch: { lock.touch(ProcessInfo.processInfo.systemUptime) })
            } else {
                lockedView
            }
        }
        .keloSensitive()
        .task { unlock() }
        .onDisappear { if phase == .active { WalletPlaces.clear() } }
        .onChange(of: phase) { _, p in
            if p != .active { lock.lock(); open = false }
            else if !open { unlock() }
        }
        .task(id: open) {
            while open && !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if !lock.isOpen(ProcessInfo.processInfo.systemUptime) { open = false }
            }
        }
    }

    private var lockedView: some View {
        SubPage(L(R.string.wallet_title), onBack: { leave() }) {
            VStack(spacing: 12) {
                KeloIconView(.lock, size: 28, tint: c.accent)
                Text(noLock ? L(R.string.wallet_lock_no_lock_title) : L(R.string.wallet_lock_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                Text(noLock ? L(R.string.my_ios_wallet_no_lock_body) : L(R.string.my_ios_wallet_lock_body)).font(KeloFont.body).foregroundStyle(c.muted)
                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                if WalletPlaces.hasTyping() && !noLock {
                    // something typed on the add / re-enter form waits behind the lock (memory only, ten minutes)
                    Text(L(R.string.wallet_lock_kept)).font(KeloFont.small).foregroundStyle(c.ink)
                        .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
                }
                if noLock {
                    PrimaryButton(L(R.string.my_ios_open_settings)) { DeviceInfo.openAppSettings() }
                } else {
                    PrimaryButton(L(R.string.wallet_lock_unlock), enabled: !asking) { unlock() }
                }
                SecondaryButton(L(R.string.wallet_lock_back), fill: true) { leave() }
            }
            .padding(.horizontal, 20).padding(.vertical, 22)
            .background(RoundedRectangle(cornerRadius: 20).fill(c.card))
            .padding(.top, 40)
        }
    }

    /// Leaving the card pages forgets the kept place and what was typed.
    private func leave() {
        WalletPlaces.clear()
        dismiss()
    }

    private func unlock() {
        guard !asking else { return }
        asking = true
        Task {
            defer { asking = false }
            let ctx = LAContext()
            var err: NSError?
            if !ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err), err?.code == LAError.passcodeNotSet.rawValue {
                noLock = true
                return
            }
            noLock = false
            if (try? await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: L(R.string.my_ios_wallet_reason))) == true {
                lock.unlock(ProcessInfo.processInfo.systemUptime)
                open = true
            }
        }
    }
}
