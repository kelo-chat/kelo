import Foundation
import KeloCore
import KeloShared

// Secure entries the agent asked for: loading the open ones and settling one saved, cancelled or gone.

extension ChatModel {
    // ---- secure entries the agent asked for (a key, a login, a card; server secret_requests.py) -----------------------------

    /// GET /api/secret-requests into `secrets` (on load, after a reply, and when the status poll's count changes).
    public func refreshSecrets() {
        Task { [weak self] in await self?.refreshSecretsNow() }
        refreshPaymentSteps()
    }

    func refreshSecretsNow() async {
        guard let api = api(), let fresh = try? await SecretCalls(api: api).list() else { return }
        secrets = SecretRules.refresh(secrets, fresh)
        // the server lists only open entries: answered lines leave the rows unless a reply runs here (its end clears them)
        if !busy { items = ChatReducer.dropSettled(items) }
    }

    /// The entry `req` was saved here (the value went straight to the vault or the wallet): its card folds. `answer`'s turn is a
    /// reply the server started to carry on, followed here like this phone's own; without one the reply that asked goes on by itself.
    public func secretSaved(_ req: SecretRequest, answer: SecretSaveAnswer, result: SecretResult?) {
        settleSecret(req, state: SecretRequest.stateSaved, result: result, answer: answer)
        if busy { phase = Phase(.waiting, since: clock()) }
        if let t = answer.turnToFollow { followTurn(t) }
    }

    /// Cancel on an entry: the agent is told (it does not ask again); the card folds at once, or goes when no reply runs here. A
    /// reply the server starts to carry on (the answer's turn) is followed like this phone's own.
    public func secretCancelled(_ req: SecretRequest) {
        settleSecret(req, state: SecretRequest.stateCancelled, result: nil, answer: nil)
        guard let api = api() else { return }
        Task { [weak self] in
            do {
                let answer = try await SecretCalls(api: api).cancel(id: req.id)
                if let t = answer.turnToFollow { self?.followTurn(t) }
            }
            catch let e as ApiError { if e.status != 404 && !e.isUnauthorized { self?.addError(e.isNetwork ? .network : .message(e.detail)) } }
            catch {}
        }
    }

    /// The entry is no longer open on the server (answered on another phone, or it ran out).
    public func secretGone(_ req: SecretRequest) { settleSecret(req, state: SecretRequest.stateExpired, result: nil, answer: nil) }

    private func settleSecret(_ req: SecretRequest, state: String, result: SecretResult?, answer: SecretSaveAnswer?) {
        let next = SecretEntries.settle(items: items, secrets: secrets, req, state: state, result: result,
                                        followsHere: SecretRules.followsHere(answer, busy: busy))
        items = next.items
        secrets = next.secrets
    }
}
