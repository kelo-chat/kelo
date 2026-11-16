import SwiftUI
import CryptoKit
import KeloCore
import KeloShared

// Payment PINs (결제 비밀번호) on this iPhone (Android wallet/PinForms.kt, my/PaymentPinSection.kt, wallet/PinVault.kt savePin /
// releasePin): the form that seals a PIN with the wallet's key after Face ID, the Keychain's list of saved PINs, and the release of one
// PIN for a payment the owner approved.

@MainActor enum PinVault {
    /// Seals a typed PIN with this phone's key (after the owner's check) and saves it for `site`.
    static func save(api: KeloApi, site: String, label: String, secret: PinSecret, requestId: String?) async -> (PinSaved?, CardSendError?) {
        defer { secret.wipe() }
        let key: SymmetricKey
        do { key = try await WalletKey.key(reason: L(R.string.pin_auth_save_subtitle)) }
        catch WalletKey.Failure.noLock { return (nil, .noLock) }
        catch WalletKey.Failure.cancelled { return (nil, .cancelled) }
        catch { return (nil, .notOnPhone) }
        guard let keyId = WalletKey.keyId(), let blob = try? PinBlob.seal(secret, key: key, keyId: keyId) else { return (nil, .notOnPhone) }
        do {
            return (try await PinCalls(api: api).save(site: site, label: label, digits: secret.length, e2e: blob, requestId: requestId), nil)
        } catch let e as ApiError {
            return (nil, CardSendError.fromStatus(e.status) ?? .other)
        } catch {
            return (nil, .other)
        }
    }

    /// The PIN `pinId` opened here (Face ID) and sent once to the payment's slot. A PIN saved on another phone says so.
    static func release(api: KeloApi, slotId: String, pinId: String) async -> CardSendError? {
        let calls = PinCalls(api: api)
        let pin: PaymentPin
        do {
            guard let p = try await calls.list().first(where: { $0.id == pinId }) else { return .notWaiting }
            pin = p
        } catch let e as ApiError { return CardSendError.fromStatus(e.status) ?? .other } catch { return .other }
        return await PinRelease.run(pin: pin, keyId: WalletKey.keyId(), blob: { try await calls.blob(pin.id) }, unlock: {
            do { return .key(try await WalletKey.key(reason: L(R.string.pin_auth_subtitle))) }
            catch WalletKey.Failure.noLock { return .noLock }
            catch WalletKey.Failure.cancelled { return .cancelled }
            catch { return .lost }
        }, send: { secret in try await calls.deliver(slotId: slotId, pinId: pin.id, secret) })
    }
}

/// The words for why a PIN could not be saved or sent.
@MainActor func pinErrorText(_ e: CardSendError) -> String {
    switch e {
    case .noLock: L(R.string.chat_card_error_no_lock)
    case .cancelled: L(R.string.chat_card_error_cancelled)
    case .network: L(R.string.chat_card_error_network)
    case .notOnPhone: L(R.string.pin_error_not_on_phone)
    case .mismatch: L(R.string.pin_error_rejected)
    case .notWaiting: L(R.string.chat_card_error_not_waiting)
    case .other: L(R.string.chat_card_error_other)
    case .keyguardRequired: L(R.string.wallet_keyguard_needed_title)
    }
}

/// Saving a payment PIN for one site: the site (fixed when the agent asked for a site's PIN), a name, the PIN twice (masked, digits
/// only, 4-8). Save asks for Face ID, seals the PIN with this phone's key and sends only that ciphertext. The PIN never goes to the
/// chat, the agent or a log. `onSaved`: the server's answer.
struct PinForm: View {
    var site = ""
    var siteFixed = false
    var requestId: String? = nil
    let onSaved: (PinSaved) -> Void
    @Environment(\.kelo) private var c
    @State private var model: PinFormModel
    // memory only: the PIN goes when the form goes
    @State private var whereTo = ""
    @State private var name = ""
    @State private var pin = ""
    @State private var again = ""

    init(api: KeloApi, site: String = "", siteFixed: Bool = false, requestId: String? = nil, onSaved: @escaping (PinSaved) -> Void) {
        self.site = site; self.siteFixed = siteFixed; self.requestId = requestId; self.onSaved = onSaved
        _model = State(initialValue: PinFormModel(api: api))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !siteFixed {
                label(L(R.string.pin_form_site))
                KeloTextField(L(R.string.pin_form_site_hint), text: Binding(get: { whereTo }, set: { whereTo = String($0.prefix(200)) }),
                              keyboard: .url, maxLength: 200)
                    .accessibilityIdentifier("kelo.pin.site")
            }
            label(L(R.string.pin_form_name))
            KeloTextField(L(R.string.pin_form_name_hint), text: Binding(get: { name }, set: { name = String($0.filter { !$0.isNumber }.prefix(60)) }),
                          maxLength: 60)
                .accessibilityIdentifier("kelo.pin.name")
            label(L(R.string.pin_form_pin))
            KeloTextField(L(R.string.pin_form_pin_hint), text: Binding(get: { pin }, set: { pin = PinRules.clean($0) }), secure: true,
                          keyboard: .number, maxLength: PinSecret.max)
                .accessibilityIdentifier("kelo.pin.pin")
            KeloTextField(L(R.string.pin_form_again), text: Binding(get: { again }, set: { again = PinRules.clean($0) }), secure: true,
                          keyboard: .number, maxLength: PinSecret.max)
                .accessibilityIdentifier("kelo.pin.again")
            if PinRules.mismatch(pin: pin, again: again) { ErrorText(L(R.string.pin_form_mismatch), small: true) }
            if let problem = model.problem { ErrorText(pinErrorText(problem), small: true) }
            PrimaryButton(L(model.busy ? R.string.vault_saving : R.string.save), enabled: !model.busy && PinRules.ready(pin: pin, again: again, site: whereTo)) {
                save()
            }
            .accessibilityIdentifier("kelo.pin.save")
            MutedText(L(R.string.pin_form_note), small: true)
        }
        .keloSensitive()
        .onAppear { if whereTo.isEmpty { whereTo = site } }
        .onDisappear { pin = ""; again = "" }
    }

    private func label(_ text: String) -> some View {
        Text(text).font(KeloFont.small.weight(.medium)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
    }

    private func save() {
        guard let secret = PinSecret.of(pin) else { return }
        model.save(site: whereTo, label: name, secret: secret, requestId: requestId) { saved in
            pin = ""; again = ""
            onSaved(saved)
        }
    }
}

/// "결제 비밀번호" in the Keychain: the payment PINs saved for sites (name, site, how many digits; never the PIN), adding one, and
/// deleting one. A PIN saved on another phone is marked: only that phone can release it.
struct PaymentPinSection: View {
    let api: KeloApi
    let locked: Bool
    @State private var model: PaymentPinsModel
    @State private var adding = false
    @State private var deleting: PaymentPin?

    init(api: KeloApi, locked: Bool) {
        self.api = api; self.locked = locked
        _model = State(initialValue: PaymentPinsModel(api: api))
    }

    var body: some View {
        Group {
            SectionLabel(L(R.string.pin_section))
            MutedText(L(R.string.pin_section_help), small: true).padding(.horizontal, 16)
            ForEach(model.pins, id: \.id) { p in
                MenuRow(.lock, p.label.isEmpty ? p.site : p.label,
                        value: PinBlob.ours(p, keyId: WalletKey.keyId()) ? L(R.string.pin_row_value, p.site, p.digits)
                            : L(R.string.pin_row_other_phone, p.site)) { deleting = p }
                    .accessibilityIdentifier("kelo.pin.row.\(p.id)")
            }
            if adding {
                MyCard { PinForm(api: api) { _ in adding = false; model.reload() } }
                    .padding(.horizontal, 16)
            } else if !locked {
                MenuRow(.plus, L(R.string.pin_add)) { adding = true }
                    .accessibilityIdentifier("kelo.pin.add")
            }
        }
        .task { await model.load() }
        .alert(deleting.map { $0.label.isEmpty ? $0.site : $0.label } ?? "", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button(L(R.string.pin_delete), role: .destructive) {
                guard let p = deleting else { return }
                deleting = nil
                model.delete(p)
            }
            Button(L(R.string.cancel), role: .cancel) { deleting = nil }
        } message: {
            if let p = deleting { Text(L(R.string.pin_row_value, p.site, p.digits) + "\n" + L(R.string.pin_delete_body)) }
        }
    }
}

/// The wallet's forms and key for the chat and the Keychain's waiting list (`AppModel.wallet`, set by the shell).
public enum KeloWalletHook {
    @MainActor public static func make(app: AppModel) -> WalletHook {
        WalletHook(
            pinForm: { site, fixed, requestId, onSaved in
                guard let api = app.api else { return AnyView(EmptyView()) }
                return AnyView(PinForm(api: api, site: site, siteFixed: fixed, requestId: requestId) { saved in onSaved(saved.turnId) })
            },
            cardForm: { onSaved in
                guard let api = app.api else { return AnyView(EmptyView()) }
                return AnyView(InlineCardForm(api: api, onSaved: onSaved))
            },
            releasePin: { [weak app] slotId, pinId in
                guard let api = app?.api else { return .other }
                return await PinVault.release(api: api, slotId: slotId, pinId: pinId).map(WalletSendProblem.init)
            })
    }
}

extension WalletSendProblem {
    init(_ e: CardSendError) {
        switch e {
        case .notWaiting: self = .notWaiting
        case .mismatch: self = .mismatch
        case .notOnPhone: self = .notOnPhone
        case .noLock: self = .noLock
        case .cancelled: self = .cancelled
        case .network: self = .network
        case .other, .keyguardRequired: self = .other
        }
    }
}
