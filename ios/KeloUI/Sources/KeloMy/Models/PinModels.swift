import SwiftUI
import KeloCore
import KeloShared

/// The form that saves a payment PIN for a site: Face ID, sealing with this phone's key, and the one call that sends the ciphertext.
@MainActor @Observable final class PinFormModel {
    private(set) var busy = false
    private(set) var problem: CardSendError?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    /// `onSaved` gets the server's answer. The secret is wiped whatever happens.
    func save(site: String, label: String, secret: PinSecret, requestId: String?, onSaved: @escaping (PinSaved) -> Void) {
        busy = true; problem = nil
        jobs.run { [self] in
            let (saved, err) = await PinVault.save(api: api, site: site, label: label, secret: secret, requestId: requestId)
            busy = false
            if let saved {
                Haptics.success()
                onSaved(saved)
            } else {
                problem = err
                Haptics.warning()
            }
        }
    }
}

/// "결제 비밀번호" in the keychain: the PINs saved for sites, and deleting one.
@MainActor @Observable final class PaymentPinsModel {
    private(set) var pins: [PaymentPin] = []
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    /// A newer read cancels the older one.
    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let list = try? await PinCalls(api: api).list(), !Task.isCancelled else { return }
            pins = list
        }
        loading = task
        await task.value
    }

    func reload() { jobs.run { [self] in await load() } }

    func delete(_ p: PaymentPin) {
        jobs.run { [self] in
            try? await PinCalls(api: api).delete(p.id)
            await load()
        }
    }
}
