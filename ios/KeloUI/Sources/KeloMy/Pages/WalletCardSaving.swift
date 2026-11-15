import SwiftUI
import CryptoKit
import KeloCore
import KeloShared

// What adding and re-entering a card share: why a save failed, the phone's key for sealing, the number's error word.

/// Why a card was not saved.
@MainActor func saveWord(_ e: Error) -> String {
    switch e as? WalletKey.Failure {
    case .noLock: L(R.string.my_ios_wallet_save_no_lock)
    case .cancelled: L(R.string.wallet_save_cancelled)
    default: L(R.string.wallet_save_key_lost)
    }
}

/// Card key from the Keychain (asks Face ID). Only the card forms (WalletAddCardForm, WalletReenterPage) call this; do not use it elsewhere.
@MainActor func sealKey() async throws -> SymmetricKey {
    try await WalletKey.key(reason: L(R.string.wallet_auth_save_subtitle))
}

/// The phone's sealed copy of a card (Face ID opens this phone's key): the blob, or why it could not be made, in words.
@MainActor func sealCard(_ secret: CardSecret) async -> (e2e: E2eBlob?, problem: String) {
    let key: SymmetricKey
    do { key = try await sealKey() } catch { return (nil, saveWord(error)) }
    guard let keyId = WalletKey.keyId(), let e2e = try? CardBlob.seal(secret, key: key, keyId: keyId) else {
        return (nil, L(R.string.wallet_save_key_lost))
    }
    return (e2e, "")
}

@MainActor func numberError(_ number: String, mustEnd: String?) -> String? {
    switch WalletFormRules.numberProblem(number, mustEnd: mustEnd) {
    case .invalid: L(R.string.wallet_number_invalid)
    case .otherCard(let end): L(R.string.wallet_number_other_card, end)
    case nil: nil
    }
}
