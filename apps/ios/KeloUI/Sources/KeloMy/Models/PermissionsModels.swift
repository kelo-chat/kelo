import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 권한: the questions waiting, the sites where the agent may act without asking (each can be taken back) and every question
/// it asked before with the answer it got.
@MainActor @Observable final class PermissionsModel {
    private(set) var pending: [PendingApproval]?
    private(set) var rules: [ApprovalRule] = []
    /// The recipients allowed for good ("always for this recipient" on a data card)
    private(set) var recipients: [RecipientAllow] = []
    private(set) var history: [ApprovalRecord] = []
    /// Payments approved in advance (the switch and both lists); nil from an older server (404): the section stays hidden.
    private(set) var cover: PaymentCover?
    private(set) var next: Double?
    private(set) var failed = false
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored private var loadingMore = false
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearNotice() { notice = nil }

    /// A newer read cancels the older one.
    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                let p = try await api.pendingApprovals()
                let r = try await api.approvalRules().items
                let rc = try await api.recipientAllows()
                let h = try await api.approvalHistory()
                let pc = try await api.paymentCover()
                if Task.isCancelled { return }
                cover = pc
                rules = r
                recipients = rc
                history = h.items; next = h.nextBefore
                pending = p
                failed = false
            } catch {
                if !Task.isCancelled { failed = true }
            }
        }
        loading = task
        await task.value
    }

    func more() {
        guard let before = next, !loadingMore else { return }
        loadingMore = true
        jobs.run { [self] in
            defer { loadingMore = false }
            guard let h = try? await api.approvalHistory(before: before) else { return }
            history += h.items; next = h.nextBefore
        }
    }

    /// Takes back an "always for this recipient": the next such send asks again.
    func removeRecipient(_ r: RecipientAllow) {
        recipients = RecipientRules.without(recipients, r.id)
        jobs.run { [self] in
            do { try await api.removeRecipientAllow(r.id) }
            catch let e as ApiError where e.status == 404 {}
            catch { notice = ApiMessage.text(error); await load() }
        }
    }

    /// The switch: shown at once, the server's answer wins; a failure puts it back.
    func setCover(_ on: Bool) {
        guard var c = cover, c.enabled != on else { return }
        let before = c.enabled
        c.enabled = on; cover = c
        jobs.run { [self] in
            do {
                let now = try await api.setPaymentCover(on)
                cover?.enabled = now
            } catch {
                cover?.enabled = before
                notice = ApiMessage.text(error)
            }
        }
    }

    /// Takes back an order approved in advance: it asks when it is paid. Already gone (404) counts as done.
    func removeOrder(_ p: Preapproval) {
        guard let c = cover else { return }
        cover = PaymentCoverRules.withoutOrder(c, p.id)
        jobs.run { [self] in
            do { try await api.removePreapproval(p.id); notice = L(R.string.paycover_removed) }
            catch { notice = ApiMessage.text(error); await load() }
        }
    }

    /// Takes back a schedule's payment allow: its payments ask each time again.
    func removeAllow(_ a: PayAllow) {
        guard let c = cover else { return }
        cover = PaymentCoverRules.withoutAllow(c, a.id)
        jobs.run { [self] in
            do { try await api.removePayAllow(a.id); notice = L(R.string.paycover_removed) }
            catch { notice = ApiMessage.text(error); await load() }
        }
    }

    func revoke(_ r: ApprovalRule) {
        jobs.run { [self] in
            do {
                try await api.deleteApprovalRule(r.id)
                rules.removeAll { $0.id == r.id }
            } catch { notice = ApiMessage.text(error) }
        }
    }
}

/// One phone feature's sheet (calendar, contacts, dialer): the switch, the last send, "send now". The phone's own sync does the work.
@MainActor @Observable final class DeviceFeatureModel {
    let feature: PhoneFeature
    private(set) var on = false
    private(set) var offFailed = false
    private(set) var isDenied = false
    @ObservationIgnored let sync: PhoneDataSync
    @ObservationIgnored let source: any PhoneSource
    @ObservationIgnored let jobs = Jobs()

    init(feature: PhoneFeature, sync: PhoneDataSync, source: any PhoneSource) { self.feature = feature; self.sync = sync; self.source = source }

    func refresh() {
        on = sync.on(feature) && sync.permitted(feature)
        isDenied = sync.on(feature) && sync.denied(feature)
    }

    func toggle(_ v: Bool) {
        offFailed = false
        jobs.run { [self] in
            if v {
                let ok = await sync.turnOn(feature, app: source)
                on = ok
                isDenied = !ok && sync.denied(feature)
            } else {
                on = false
                isDenied = false
                offFailed = !(await sync.turnOff(feature, app: source))
            }
        }
    }

    func sendNow() { jobs.run { [self] in _ = await sync.sync(feature, app: source, force: true) } }
}

/// The calendar row's own options: also allow adding and editing events, and which calendar Kelo adds to.
@MainActor @Observable final class CalendarWriteModel {
    private(set) var write = false
    private(set) var pick: String?
    @ObservationIgnored let sync: PhoneDataSync
    @ObservationIgnored private let source: any PhoneSource
    @ObservationIgnored let jobs = Jobs()

    init(sync: PhoneDataSync, source: any PhoneSource) { self.sync = sync; self.source = source }

    func refresh() { write = sync.writeAllowed; pick = sync.writeCalendarId }

    func setWrite(_ v: Bool) {
        write = v
        sync.writeAllowed = v
        resend()
    }

    func choose(_ id: String) {
        pick = id
        sync.writeCalendarId = id
        resend()
    }

    private func resend() { jobs.run { [self] in _ = await sync.sync(.calendar, app: source, force: true) } }
}
