import SwiftUI
import CryptoKit
import KeloCore
import KeloShared

/// 마이 → 결제 카드, behind the lock: the wallet (cards, limits' pause, addresses), the automatic payments the owner has not seen yet,
/// and what is done to one card. One per open wallet; its pages call `reload()` after a change. `message` is the last failure in
/// words (a page clears it when it opens).
@MainActor @Observable final class WalletModel {
    private(set) var info: WalletInfo?
    private(set) var notices: [WalletPayment] = []
    private(set) var failed = ""
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let keyguard: @MainActor () async -> Bool?
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    /// `keyguard`: asks the server whether Keyguard is on (nil when not known) before auto-pay is turned on.
    init(api: KeloApi, keyguard: @escaping @MainActor () async -> Bool? = { nil }) { self.api = api; self.keyguard = keyguard }

    func clearMessage() { message = "" }

    /// Auto-pay needs Keyguard: asked first (`AutoPayGate`), then `then` gets whether it is on.
    func checkKeyguard(then: @escaping (Bool?) -> Void) {
        jobs.run { [self] in then(await keyguard()) }
    }

    /// A newer read cancels the older one, so an old wallet never covers what a change just made.
    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                var w = try await api.wallet()
                // the shipping addresses from their own list (GET /api/wallet carries them too, but not every server does)
                if let list = try? await api.shippingProfiles() { w.shipping = list }
                if Task.isCancelled { return }
                info = w
                failed = ""
            } catch {
                if !Task.isCancelled { failed = ApiMessage.text(error) }
            }
        }
        loading = task
        await task.value
    }

    func reload() { jobs.run { [self] in await load() } }

    func loadAll() async {
        await load()
        // automatic payments the owner has not looked at: shown at the top of the list once, then marked as seen
        if let n = try? await api.walletNotifications() {
            notices = n.items.filter { !$0.seen }
            if n.unseen > 0, let top = n.items.map(\.id).max() {
                _ = try? await api.request("POST", "/api/wallet/notifications/seen", body: ["up_to": .number(Double(top))])
            }
        }
    }

    func revoke(_ g: ScheduleGrant) {
        jobs.run { [self] in
            do { try await api.revokeScheduleGrant(g); Haptics.success(); await load() }
            catch { Haptics.warning() }
        }
    }

    // ---- one card -------------------------------------------------------------------------------------------------------------------

    private func cardPath(_ id: String) -> String { "/api/wallet/cards/\(KeloApi.segment(id))" }

    /// A change to one card (default, auto-pay off, nickname): then the wallet is read again; `then` runs after it went through.
    func patchCard(_ id: String, _ body: [String: JSON], then: @escaping () -> Void = {}) {
        act(then: then) { [api, path = cardPath(id)] in _ = try await api.patch(path, body: .object(body), as: WalletCard.self) }
    }

    func deleteCard(_ id: String, newDefault: String? = nil, then: @escaping () -> Void) {
        let path = cardPath(id) + (newDefault.map { "?new_default=\(KeloApi.segment($0))" } ?? "")
        act(then: then) { [api] in _ = try await api.request("DELETE", path) }
    }

    private func act(then: @escaping () -> Void, _ block: @escaping @Sendable () async throws -> Void) {
        message = ""
        jobs.run { [self] in
            do {
                try await block()
                Haptics.success()
                reload()
                then()
            } catch {
                message = ApiMessage.text(error)
                Haptics.warning()
            }
        }
    }

    /// Turning auto-pay on from this phone's own copy (Face ID opens it, no retyping): after `delay` (the sheet goes first, so the
    /// system check is not behind it); `done` gets nil when it is on now.
    func turnAutoPayOn(_ card: WalletCard, delay: UInt64 = 0, done: @escaping (CardSendError?) -> Void) {
        jobs.run { [self] in
            if delay > 0 { try? await Task.sleep(nanoseconds: delay) }
            done(await WalletAutoPay.turnOn(api: api, card: card))
        }
    }
}

/// The payments Kelo made with one card, newest first, older ones a page at a time.
@MainActor @Observable final class WalletCardHistoryModel {
    private(set) var items: [WalletPayment]?
    private(set) var next: Int64?
    private(set) var loading = false
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let cardId: String
    @ObservationIgnored private var seq = Latest()
    @ObservationIgnored private let jobs = Jobs()

    init(api: KeloApi, cardId: String) { self.api = api; self.cardId = cardId }

    func load(before: Int64? = nil) async {
        loading = true
        let n = seq.begin()
        defer { loading = false }
        do {
            var path = "/api/wallet/cards/\(KeloApi.segment(cardId))/payments?limit=20"
            if let before { path += "&before_id=\(before)" }
            let page = try await api.get(path, as: WalletPayments.self)
            guard seq.accept(n) else { return }   // a newer read answered first
            items = CardHistoryRules.merge(before == nil ? [] : (items ?? []), page)
            next = page.nextBeforeId
            message = ""
        } catch {
            message = ApiMessage.text(error)
            if items == nil { items = [] }
        }
    }

    func loadMore() { jobs.run { [self] in await load(before: next) } }
}

/// 결제 내역: every payment that went ahead.
@MainActor @Observable final class WalletPaymentsModel {
    private(set) var items: [WalletPayment]?
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi) { self.api = api }

    func load() async {
        do { items = try await api.get("/api/wallet/payments?limit=100", as: WalletPayments.self).items }
        catch { message = ApiMessage.text(error); items = [] }
    }
}

/// 자동결제 한도: the limits per currency and the pause.
@MainActor @Observable final class WalletLimitsModel {
    private(set) var data: WalletLimits?
    private(set) var isPaused: Bool?
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let wallet: WalletModel
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, wallet: WalletModel) { self.api = api; self.wallet = wallet }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do { let d = try await api.walletLimits(); if !Task.isCancelled { data = d } }
            catch {
                if Task.isCancelled { return }
                message = ApiMessage.text(error)
                if data == nil { data = try? KeloApi.decode(WalletLimits.self, ["limits": .array([])]) }
            }
        }
        loading = task
        await task.value
    }

    func setPaused(_ on: Bool) {
        message = ""
        jobs.run { [self] in
            do { isPaused = try await api.setWalletPaused(on).paused; Haptics.success(); wallet.reload() }
            catch { message = ApiMessage.text(error); Haptics.warning() }
        }
    }

    /// `done` runs when the limit was saved (the editor closes).
    func save(_ currency: String, _ per: Double, _ mon: Double, done: @escaping () -> Void) {
        message = ""
        jobs.run { [self] in
            do {
                _ = try await api.put("/api/wallet/limits/\(KeloApi.segment(currency))",
                                      body: ["per_payment": .number(per), "monthly": .number(mon)], as: WalletLimit.self)
                done(); Haptics.success()
                await load()
            } catch { message = ApiMessage.text(error); Haptics.warning() }
        }
    }

    func reset(_ currency: String, done: @escaping () -> Void) {
        message = ""
        jobs.run { [self] in
            do {
                _ = try await api.request("DELETE", "/api/wallet/limits/\(KeloApi.segment(currency))")
                done(); Haptics.success()
                await load()
            } catch { message = ApiMessage.text(error) }
        }
    }
}

/// The card form's save: Face ID, sealing with this phone's key, the one call. The number and CVC are wiped whatever happens.
@MainActor @Observable final class AddCardModel {
    private(set) var busy = false
    private(set) var problem = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    /// `added` gets the server's answer for the saved card; `reset` runs first (the draft is emptied).
    func save(_ d: AddCardDraft, month: Int, year: Int, secret: CardSecret, reset: @escaping () -> Void, added: @escaping (JSON) async -> Void) {
        busy = true; problem = ""
        jobs.run { [self] in
            defer { busy = false; secret.wipe() }
            let sealed = await sealCard(secret)
            guard let e2e = sealed.e2e else { problem = sealed.problem; return }
            // the phone's copy always goes; auto-pay is turned on later from the card's page (Android NewCard autoPay = false)
            let fields: [String: JSON] = [
                "exp_month": .number(Double(month)), "exp_year": .number(Double(year)),
                "holder": .string(d.holder.trimmingCharacters(in: .whitespaces)), "postal_code": .string(d.postal.trimmingCharacters(in: .whitespaces)),
                "auto_pay": .bool(false), "brand": .string(CardNumber.brand(d.number).rawValue), "last4": .string(secret.last4),
                "e2e": ["blob": .string(e2e.blob), "alg": .string(e2e.alg), "key_id": .string(e2e.keyId)],
            ].merging(d.nickname.trimmingCharacters(in: .whitespaces).isEmpty ? [:] :
                        ["label": .string(d.nickname.trimmingCharacters(in: .whitespaces))]) { a, _ in a }
            do {
                let saved = try await api.request("POST", "/api/wallet/cards", body: .object(fields))
                reset()
                Haptics.success()
                await added(saved)
            } catch let e as ApiError {
                problem = e.isNetwork ? L(R.string.wallet_save_server) : L(R.string.wallet_save_failed, e.detail)
                Haptics.warning()
            } catch {
                problem = L(R.string.wallet_save_server)
            }
        }
    }
}

/// The number and CVC of a saved card typed again (a new phone copy, or auto-pay on with the server's own sealed copy).
@MainActor @Observable final class ReenterModel {
    private(set) var busy = false
    private(set) var problem = ""
    /// The server says auto-pay needs Keyguard: the page shows its dialog and saves again.
    private(set) var needKeyguard = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let card: WalletCard
    @ObservationIgnored private let autoPay: Bool
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, card: WalletCard, autoPay: Bool) { self.api = api; self.card = card; self.autoPay = autoPay }

    func keyguardAsked() { needKeyguard = false }

    func save(secret: CardSecret, saved: @escaping () -> Void) {
        busy = true; problem = ""
        jobs.run { [self] in
            defer { busy = false; secret.wipe() }
            let sealed = await sealCard(secret)
            guard let e2e = sealed.e2e else { problem = sealed.problem; return }
            var fields: [String: JSON] = ["e2e": ["blob": .string(e2e.blob), "alg": .string(e2e.alg), "key_id": .string(e2e.keyId)]]
            if autoPay { fields["auto_pay"] = .bool(true) }
            let path = "/api/wallet/cards/\(KeloApi.segment(card.id))"
            do {
                if autoPay {
                    // the number and CVC go too, for the server's own sealed copy (as bytes, wiped after)
                    var body = SecretJson.body(.object(fields), secret)
                    defer { body.resetBytes(in: 0..<body.count) }
                    _ = try await api.requestBytes("PATCH", path, body: body)
                } else {
                    _ = try await api.request("PATCH", path, body: .object(fields))
                }
                Haptics.success()
                saved()
            } catch let e as ApiError {
                if e.code == AutoPayGate.codeKeyguardRequired { needKeyguard = true; return }   // the dialog, then again
                problem = e.isNetwork ? L(R.string.wallet_save_server) : e.detail
                Haptics.warning()
            } catch {
                problem = L(R.string.wallet_save_server)
            }
        }
    }
}

/// Adding, editing or deleting a shipping address.
@MainActor @Observable final class ShippingEditModel {
    private(set) var saving = false
    private(set) var message = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearMessage() { message = "" }

    /// `profile` nil: a new address.
    func save(_ body: [String: JSON], profile: ShippingProfile?, saved: @escaping () -> Void) {
        message = ""
        saving = true
        jobs.run { [self] in
            defer { saving = false }
            do {
                if let profile { _ = try await api.updateShippingProfile(profile.id, .object(body)) }
                else { _ = try await api.addShippingProfile(.object(body)) }
                Haptics.success()
                saved()
            } catch {
                message = ApiMessage.text(error)
                Haptics.warning()
            }
        }
    }

    func delete(_ profile: ShippingProfile, deleted: @escaping () -> Void) {
        message = ""
        jobs.run { [self] in
            do { try await api.deleteShippingProfile(profile.id); Haptics.success(); deleted() }
            catch { message = ApiMessage.text(error); Haptics.warning() }
        }
    }
}
