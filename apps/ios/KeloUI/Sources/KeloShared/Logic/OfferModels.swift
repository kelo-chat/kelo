import Foundation
import Observation
import KeloCore

/// One ready-to-run task Kelo offers (server offers.py): its sentence, what it still needs from the owner, whether it names amounts.
public struct Offer: Codable, Identifiable, Equatable, Sendable {
    public var id: String
    public var kind: String
    public var sentence: String
    public var needs: [String]
    /// What will run when the owner taps: sent as Kelo's own turn (server offers.py).
    public var request: String
    /// The steps Kelo prepared for that turn.
    public var steps: [String]
    public var createdAt: Double
    public var hasNumbers: Bool
    public var checkedAt: Double?

    public init(
        id: String = "",
        kind: String = "",
        sentence: String = "",
        needs: [String] = [],
        request: String = "",
        steps: [String] = [],
        createdAt: Double = 0.0,
        hasNumbers: Bool = false,
        checkedAt: Double? = nil
    ) {
        self.id = id
        self.kind = kind
        self.sentence = sentence
        self.needs = needs
        self.request = request
        self.steps = steps
        self.createdAt = createdAt
        self.hasNumbers = hasNumbers
        self.checkedAt = checkedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, kind, sentence, needs, request, steps
        case createdAt = "created_at"
        case hasNumbers = "has_numbers"
        case checkedAt = "checked_at"
    }
}

extension Offer {
    /// An older server sends no `request` / `steps`: they stay empty rather than failing the whole list.
    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        self.init(
            id: try c.decodeIfPresent(String.self, forKey: .id) ?? "",
            kind: try c.decodeIfPresent(String.self, forKey: .kind) ?? "",
            sentence: try c.decodeIfPresent(String.self, forKey: .sentence) ?? "",
            needs: try c.decodeIfPresent([String].self, forKey: .needs) ?? [],
            request: try c.decodeIfPresent(String.self, forKey: .request) ?? "",
            steps: try c.decodeIfPresent([String].self, forKey: .steps) ?? [],
            createdAt: try c.decodeIfPresent(Double.self, forKey: .createdAt) ?? 0,
            hasNumbers: try c.decodeIfPresent(Bool.self, forKey: .hasNumbers) ?? false,
            checkedAt: try c.decodeIfPresent(Double.self, forKey: .checkedAt)
        )
    }
}

/// The steps list on a card: the first `collapsed` steps, the rest behind "show all". Pure (Android OfferSteps).
public enum OfferSteps {
    public static let collapsed = 3

    public static func visible(_ steps: [String], expanded: Bool) -> [String] {
        (expanded || steps.count <= collapsed) ? steps : Array(steps.prefix(collapsed))
    }

    public static func collapsible(_ steps: [String]) -> Bool { steps.count > collapsed }
}

public struct OfferList: Codable, Sendable {
    public var items: [Offer]
    public init(items: [Offer] = []) { self.items = items }
}

public struct OfferStarted: Codable, Sendable {
    public var turnId: String
    public init(turnId: String = "") { self.turnId = turnId }

    enum CodingKeys: String, CodingKey {
        case turnId = "turn_id"
    }
}

/// Why an offer is dismissed; the rawValue is the server's wire word.
public enum OfferReason: String, Codable, Sendable, CaseIterable {
    case notInterested = "not_interested"
    case doneAlready = "done_already"
    case notNow = "not_now"
}

/// What starting an offer came to.
public enum OfferStart: Equatable, Sendable {
    case started(String)
    case busy
    case failed
}

public enum OfferCalls {
    public static func list(api: KeloApi) async throws -> [Offer] {
        let list: OfferList = try await api.get("/api/offers")
        return list.items
    }

    public static func start(api: KeloApi, id: String) async -> OfferStart {
        do {
            let res: OfferStarted = try await api.post("/api/offers/\(KeloApi.segment(id))/start")
            let tid = res.turnId.trimmingCharacters(in: .whitespacesAndNewlines)
            return tid.isEmpty ? .failed : .started(tid)
        } catch let e as ApiError {
            // 409 without a code of its own: a reply is already running
            if e.status == 409 && (e.code == nil || e.code?.isEmpty == true) {
                return .busy
            }
            return .failed
        } catch {
            return .failed
        }
    }

    public static func dismiss(api: KeloApi, id: String, reason: OfferReason) async throws {
        _ = try await api.request("POST", "/api/offers/\(KeloApi.segment(id))/dismiss", body: ["reason": .string(reason.rawValue)])
    }
}

/// The offer cards: loaded on open (and by [load]), started, dismissed (the card goes at once).
@MainActor @Observable
public final class OffersModel {
    public private(set) var items: [Offer] = []
    public private(set) var starting: String?
    public private(set) var problem: (id: String, start: OfferStart)?
    @ObservationIgnored public let jobs = Jobs()
    @ObservationIgnored public var api: () -> KeloApi?

    public init(api: @escaping () -> KeloApi? = { nil }) {
        self.api = api
    }

    public func setItems(_ items: [Offer]) {
        self.items = items
    }

    public func load() async {
        guard let api = api() else { return }
        do {
            let list = try await OfferCalls.list(api: api)
            if !Task.isCancelled {
                self.items = list
            }
        } catch {}
    }

    public func start(_ o: Offer, onStarted: @escaping (String) -> Void) {
        guard starting == nil, let api = api() else { return }
        starting = o.id
        problem = nil
        jobs.run { [weak self] in
            guard let self else { return }
            let r = await OfferCalls.start(api: api, id: o.id)
            if case .started(let turnId) = r {
                self.items.removeAll { $0.id == o.id }
                self.starting = nil
                onStarted(turnId)
            } else {
                self.starting = nil
                self.problem = (id: o.id, start: r)
            }
        }
    }

    public func dismiss(_ o: Offer, reason: OfferReason) {
        items.removeAll { $0.id == o.id }
        if problem?.id == o.id { problem = nil }
        guard let api = api() else { return }
        jobs.run {
            try? await OfferCalls.dismiss(api: api, id: o.id, reason: reason)
        }
    }
}
