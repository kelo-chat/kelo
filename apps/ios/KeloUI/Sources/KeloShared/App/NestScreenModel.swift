import Foundation
import Observation
import KeloCore

// One open Nest surface (Android shell/NestScreenViewModel.kt): loads the screen and runs its actions (docs/nest-ui.md Part 1).

/// The live event `nest_changed {"id"}` (a Nest draft or package was written to): the reply that carried it posts it here, and an open
/// screen of that package reloads at once.
public enum NestLive {
    public static let changed = Notification.Name("chat.kelo.nest.changed")

    /// Called by the reply that carried the event.
    public static func post(_ id: String) {
        NotificationCenter.default.post(name: changed, object: nil, userInfo: ["id": id])
    }

    /// The ids of what changed, until the consumer stops listening.
    public static func ids() -> AsyncStream<String> {
        AsyncStream { cont in
            let token = NotificationCenter.default.addObserver(forName: changed, object: nil, queue: nil) { n in
                if let id = n.userInfo?["id"] as? String { cont.yield(id) }
            }
            nonisolated(unsafe) let t = token
            cont.onTermination = { _ in NotificationCenter.default.removeObserver(t) }
        }
    }
}

/// The owner must confirm `action` first (`result`: the question, where it goes, what is sent).
public struct NestConfirm: Equatable, Sendable {
    public var action: NestAction
    public var token: String, title: String, detail: String
    public var sends: [NestSend]
    public var expiresIn: Int
}

/// Things an action asks the app to do outside the screen.
public enum NestEffect: Equatable, Sendable {
    /// Open `url` in the system browser (never in a web view).
    case openUrl(String)
    /// Kelo was asked to do something: `turnId` is the reply; the app offers to open the chat.
    case started(message: String, turnId: String)
}

/// `loaded`: the first answer came; `gone`: the package may not show it any more (403 / 404: the screen is removed from view and the
/// app re-reads the extensions); `error`: a failed load (the old screen stays); `busy`: the keys of the buttons whose request runs;
/// `message`: a line to show briefly (an action's `message`, or the `detail` of a refused one).
@MainActor @Observable public final class NestScreenModel {
    public private(set) var loaded = false
    public private(set) var screen = NestScreen()
    public private(set) var error: String?
    public private(set) var gone = false
    public private(set) var refreshing = false
    public private(set) var busy: Set<String> = []
    public var message: String?
    public private(set) var confirm: NestConfirm?
    public private(set) var effects: [NestEffect] = []

    public let entry: NestEntry
    @ObservationIgnored let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?

    public init(api: KeloApi, entry: NestEntry) { self.api = api; self.entry = entry }

    private var base: String { "/api/nest/extensions/\(KeloApi.segment(entry.packageId))" }

    /// The next effect that waits, if any.
    public func nextEffect() -> NestEffect? { effects.isEmpty ? nil : effects.removeFirst() }

    /// Fetches the screen: at open, on pull-to-refresh, after an action answers `refresh`, and when `nest_changed` names this package.
    public func load() async {
        loading?.cancel()
        refreshing = true
        let task = Task { @MainActor [self] in
            do {
                let el = try await api.get("\(base)/data?surface=\(KeloApi.segment(entry.surface))", as: JSON.self)
                if Task.isCancelled { return }
                screen = NestScreenParse.screen(el)
                loaded = true; refreshing = false; error = nil; gone = false
            } catch is CancellationError {
                return
            } catch let e as ApiError {
                if Task.isCancelled { return }
                // 403 turned off or not allowed any more, 404 no such surface: gone from view. 429 and the rest: say it, keep what we had
                loaded = true; refreshing = false; gone = e.status == 403 || e.status == 404; error = e.detail
            } catch {
                if Task.isCancelled { return }
                loaded = true; refreshing = false; self.error = "\(error)"
            }
        }
        loading = task
        await task.value
    }

    /// The live event: reload when it names this package.
    public func changed(id: String) async {
        if id == entry.packageId { await load() }
    }

    /// Runs `action` (a button, a tapped card or row, a form's submit with its `form` values). Ignored while it already runs.
    public func run(_ action: NestAction, form: [String: JSON]? = nil) async {
        if action.id.isEmpty || busy.contains(action.key) { return }
        if action.type == "open_url" {   // the app opens it itself (the address is in the screen)
            if nestOpenable(action.url) { effects.append(.openUrl(action.url)) }
            return
        }
        var body: [String: JSON] = [:]
        if let form { body["form"] = .object(form) }
        if !action.item.isEmpty { body["item"] = .string(action.item) }
        if !action.data.isEmpty { body["data"] = .string(action.data) }
        await post(action, body: body)
    }

    /// "Confirm" on the dialog: the same POST with the token only.
    public func confirmPending() async {
        guard let c = confirm else { return }
        confirm = nil
        await post(c.action, body: ["confirm": .string(c.token)])
    }

    public func dismissConfirm() { confirm = nil }

    private func post(_ action: NestAction, body: [String: JSON]) async {
        busy.insert(action.key)
        defer { busy.remove(action.key) }
        do {
            let el = try await api.post("\(base)/actions/\(KeloApi.segment(action.id))", body: .object(body), as: JSON.self)
            switch NestActionResult.parse(el) {
            case .done(let m, let refresh):
                message = m.isEmpty ? nil : m
                if refresh { await load() }
            case .confirm(let token, let title, let detail, let sends, let expiresIn):
                confirm = NestConfirm(action: action, token: token, title: title, detail: detail, sends: sends, expiresIn: expiresIn)
            case .open(let url): if nestOpenable(url) { effects.append(.openUrl(url)) }
            case .started(let m, let turn):
                message = m.isEmpty ? nil : m
                effects.append(.started(message: m, turnId: turn))
            case .unknown: break
            }
        } catch is CancellationError {
            return
        } catch let e as ApiError {
            message = e.detail
            if e.status == 410 { confirm = nil }
            // 403 not allowed any more, 404 the button is gone: re-read the screen
            if e.status == 403 || e.status == 404 { await load() }
        } catch {
            message = "\(error)"
        }
    }
}
