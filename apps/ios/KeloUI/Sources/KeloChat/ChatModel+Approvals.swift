import Foundation
import KeloCore
import KeloShared

// Questions the agent asks the owner: approvals (with an end-to-end card), the location card, waiting cards from other work.

extension ChatModel {
    // ---- the location card (location_request): the screen asks the phone (permission, place) and hands the answer here -------
    /// The card shows "getting your location" while the screen reads the place.
    public func locationReading(_ approvalId: String) {
        items = ChatReducer.locationState(items, approvalId, ChatItem.LocationAsk.sending)
    }

    /// POST /api/location-requests/{id}: `place` (lat, lon, name?, country_code?, accuracy?, sublocality?) when shared, nil for
    /// "no" (the system prompt refused, Not now, or no place could be read). The reply that waits on it goes on by itself. 404/409:
    /// the question is gone (expired); a network failure leaves the card open to try again.
    public func answerLocation(_ approvalId: String, place: [String: JSON]?) {
        guard let api = api() else { return }
        items = ChatReducer.locationState(items, approvalId, ChatItem.LocationAsk.sending)
        Task { [weak self] in
            var body: [String: JSON] = ["allowed": .bool(place != nil)]
            if let place { body["place"] = .object(place) }
            let end: String
            do {
                _ = try await api.request("POST", "/api/location-requests/\(KeloApi.segment(approvalId))", body: .object(body))
                end = place != nil ? ChatItem.LocationAsk.allowed : ChatItem.LocationAsk.refused
            } catch let e as ApiError {
                end = e.status == 404 || e.status == 409 ? ChatItem.LocationAsk.expired : ChatItem.LocationAsk.waiting
            } catch {
                end = ChatItem.LocationAsk.waiting
            }
            guard let self else { return }
            self.items = self.items.map { item in
                guard case .locationAsk(var l) = item, l.approvalId == approvalId, l.state == ChatItem.LocationAsk.sending else { return item }
                l.state = end
                return .locationAsk(l)
            }
        }
    }

    /// Answers an approval; `rememberSite`: allow this step on the site; `rememberSchedule`: in that scheduled job.
    public func approve(_ id: String, ok: Bool, rememberSite: Bool = false, rememberSchedule: Bool = false) {
        guard items.contains(where: { $0.asApproval?.id == id && $0.asApproval?.open == true }) else { return }
        items = ChatReducer.answer(items, id, ok)
        if busy { phase = Phase(.waiting, since: clock()) }
        guard let api = api() else { return }
        Task {
            // expired: the reply reports the denial
            let rem: String? = (ok && rememberSite) ? "site" : ((ok && rememberSchedule) ? "schedule" : nil)
            _ = try? await api.answerApproval(id, approve: ok, remember: rem)
            if ok { ApprovalHooks.afterAllow() }   // a calendar change or call may now wait for this phone
        }
    }

    /// Allow on a payment with an end-to-end card: only when the card got there is the payment allowed.
    public func approveWithCard(_ id: String, cardId: String) {
        guard let a = items.lazy.compactMap(\.asApproval).first(where: { $0.id == id }), a.open, a.send != .working else { return }
        runCardSend(id, cardId: cardId) { [weak self] in self?.approve(id, ok: true) }
    }

    /// "카드 보내기" on a card_needed row (the payment was allowed already).
    public func sendCard(_ approvalId: String, cardId: String) {
        let row = items.lazy.compactMap { item -> ChatItem.CardNeeded? in
            if case .cardNeeded(let c) = item, c.approvalId == approvalId { return c }
            return nil
        }.first
        guard let row, !row.ended, row.send != .working, row.send != .sent else { return }
        runCardSend(approvalId, cardId: cardId) {}
    }

    private func runCardSend(_ id: String, cardId: String, then: @escaping () -> Void) {
        items = ChatReducer.cardSend(items, id, .working)
        Task { [weak self] in
            guard let self else { return }
            let err: CardSendError?
            if let sender = self.cardSender, let api = self.api() {
                err = await sender.deliver(api: api, approvalId: id, cardId: cardId)
            } else {
                err = .notOnPhone
            }
            if let err {
                var next = ChatReducer.cardSend(self.items, id, .failed(err))
                if err == .notWaiting {
                    next = next.map { item in
                        guard case .approval(var a) = item, a.id == id, a.open else { return item }
                        a.expired = true
                        return .approval(a)
                    }
                }
                self.items = next
                if err != .cancelled { self.onEvent(.error) }
            } else {
                self.items = ChatReducer.cardSend(self.items, id, .sent)
                then()
            }
        }
    }

    /// The user finished or closed a secure / connect card: it folds into one line.
    public func settleCard(_ key: String, _ state: CardState) { items = ChatReducer.settleCard(items, key, state) }

    // ---- questions from work the chat is not following -------------------------------------------------------------------

    /// GET /api/approvals/pending joined into the chat's waiting cards; nothing is asked when the poll says nothing waits.
    public func refreshAsks(force: Bool = false) {
        if !force && ChatAsks.skipFetch(app.activity.status, asks) {
            // not fetched: the location cards stay as they are (only a fetched list says one has ended)
            asks = ChatAsks.merge(asks, pending: [], newestKey: items.last?.key)
            return
        }
        Task { [weak self] in
            guard let self, let api = self.api() else { return }
            // not reachable now: the cards stay as they are until the next poll
            guard let (pending, resolved, locations) = try? await api.pendingApprovalsFull() else { return }
            self.asks = ChatAsks.merge(self.asks, pending: pending, newestKey: self.items.last?.key, resolved: resolved)
            self.items = ChatReducer.withPendingLocations(self.items, locations, busy: self.busy)
        }
    }

    /// Answers a waiting card the same way the 작업 page does (`rememberSite`: allow this kind of step on that site from now on;
    /// `rememberSchedule`: in that scheduled job). A payment with an end-to-end card sends the card first on Allow; when that
    /// fails the question stays open and says why.
    public func answerAsk(_ id: String, approve: Bool, rememberSite: Bool = false, rememberSchedule: Bool = false, cardId: String? = nil) {
        guard let ask = asks.first(where: { $0.id == id }), ask.open, !ask.working, let api = api() else { return }
        let a = ask.approval
        asks = ChatAsks.working(asks, id, on: true)
        Task { [weak self] in
            guard let self else { return }
            if approve, let cardId {
                let err: CardSendError? = if let sender = self.cardSender { await sender.deliver(api: api, approvalId: id, cardId: cardId) } else { .notOnPhone }
                if let err {
                    self.asks = err == .notWaiting ? ChatAsks.ended(self.asks, id, .gone, newestKey: self.items.last?.key)
                        : ChatAsks.working(self.asks, id, on: false, error: err == .cancelled ? nil : err)
                    if err != .cancelled { self.onEvent(.error) }
                    return
                }
            }
            let mode: String? = approve && rememberSite && a.canRemember ? "site" : (approve && rememberSchedule && a.canRememberSchedule ? "schedule" : nil)
            do {
                _ = try await api.answerApproval(id, approve: approve, remember: mode)
                if approve { ApprovalHooks.afterAllow() }
                let end = ChatAsks.answerEnd(a, approve: approve, rememberSite: rememberSite, rememberSchedule: rememberSchedule)
                self.asks = ChatAsks.ended(self.asks, id, end, newestKey: self.items.last?.key)
            } catch let e as ApiError {
                if e.status == 404 { self.asks = ChatAsks.ended(self.asks, id, .gone, newestKey: self.items.last?.key) }
                else {
                    self.asks = ChatAsks.working(self.asks, id, on: false)
                    if !e.isUnauthorized { self.addError(e.isNetwork ? .network : .message(e.detail)) }
                }
            } catch {
                self.asks = ChatAsks.working(self.asks, id, on: false)
            }
        }
    }
}
