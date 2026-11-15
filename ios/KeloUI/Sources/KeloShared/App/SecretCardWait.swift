import Foundation
import Observation
import KeloCore

/// A card entry the agent asked for (`SecretRequest.kindCard`) waiting for the wallet's card form. On iOS the card is added on the
/// wallet's own page (behind its lock, sealed with this iPhone's key), so the entry hands over to it: `start` notes the request,
/// the card form calls `cardAdded` once the card is in the wallet, which answers the request (POST /api/secret-requests/{id}/card)
/// and leaves the outcome for the entry to `take`. Only ids and the card's nickname and last four digits pass through here, never
/// the number. Memory only.
@MainActor @Observable
public final class SecretCardWait {
    /// How the card form's answer went for one request.
    public struct Outcome: Equatable, Sendable {
        public enum Kind: Equatable, Sendable { case saved, gone, failed }
        public var requestId: String
        public var kind: Kind
        /// saved: the card that was added (nickname, brand, last four digits)
        public var result: SecretResult?
        /// saved: a reply the server started to carry on (follow it like this phone's own), or nil
        public var turnId: String?
        /// failed: the server's words (the card is in the wallet either way)
        public var detail: String

        public init(requestId: String, kind: Kind, result: SecretResult? = nil, turnId: String? = nil, detail: String = "") {
            self.requestId = requestId; self.kind = kind; self.result = result; self.turnId = turnId; self.detail = detail
        }
    }

    /// The request the card form is filling, and the tab to go back to afterwards (nil: stay on the wallet).
    public private(set) var waiting: String?
    public private(set) var returnTo: KeloTab?
    /// Goes up whenever an outcome is left (the entry looks for its own with `take`).
    public private(set) var tick = 0
    @ObservationIgnored private var outcomes: [String: Outcome] = [:]

    public init() {}

    /// The card entry `requestId` opens the wallet's card form.
    public func start(_ requestId: String, returnTo: KeloTab? = nil) {
        waiting = requestId
        self.returnTo = returnTo
    }

    /// The card form was left without a card (or the session ended): nothing waits any more.
    public func stop() { waiting = nil; returnTo = nil }

    /// The card form kept `card` in the wallet. When a card entry waits for it, the request is answered with it here (so it works
    /// whichever screen opened the form) and the outcome is left for the entry. Returns the tab to go back to, if any.
    @discardableResult
    public func cardAdded(_ card: SecretCard, api: KeloApi?) async -> KeloTab? {
        guard let id = waiting else { return nil }
        let back = returnTo
        stop()
        guard let api, !card.id.isEmpty else {
            leave(Outcome(requestId: id, kind: .failed))
            return back
        }
        do {
            let answer = try await SecretCalls(api: api).cardSaved(id: id, cardId: card.id)
            leave(Outcome(requestId: id, kind: .saved, result: SecretResult(card: card), turnId: answer.turnId))
        } catch let e as ApiError where e.status == 404 {
            leave(Outcome(requestId: id, kind: .gone))
        } catch let e as ApiError {
            leave(Outcome(requestId: id, kind: .failed, detail: e.isNetwork ? "" : e.detail))
        } catch {
            leave(Outcome(requestId: id, kind: .failed))
        }
        return back
    }

    /// The outcome for `requestId`, once (nil when there is none).
    public func take(_ requestId: String) -> Outcome? { outcomes.removeValue(forKey: requestId) }

    /// Whether an outcome waits for `requestId` (without taking it).
    public func has(_ requestId: String) -> Bool { outcomes[requestId] != nil }

    func leave(_ o: Outcome) {
        outcomes[o.requestId] = o
        tick += 1
    }

    /// The card a POST /api/wallet/cards answered (its id, nickname, brand and last four digits).
    public nonisolated static func card(from e: JSON) -> SecretCard {
        let o = e["card"]?.objectValue != nil ? e["card"]! : e
        return SecretCard(id: o["id"]?.text ?? "", label: o["label"]?.text ?? "", brand: o["brand"]?.text ?? "",
                          last4: o["last4"]?.text ?? "", isDefault: o["default"]?.boolValue ?? false)
    }
}
