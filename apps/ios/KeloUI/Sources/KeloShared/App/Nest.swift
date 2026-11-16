import SwiftUI
import Observation
import KeloCore

// Kelo Nest in the app (Android shell/Nest.kt): the extensions the server offers for the app's surfaces (GET /api/nest/extensions).
// The app holds the slots; what fills them is drawn by one `NestSurfaceView` the app root puts in the environment: the native
// `NestRenderer` (SwiftUI, no web view). Without one (a test, a screen outside the root) nothing from Nest shows anywhere.

/// One extension placed on one of the app's surfaces (an item of `places.<place>`).
public struct NestEntry: Decodable, Equatable, Hashable, Sendable, Identifiable {
    public var key = ""
    public var packageId = ""
    public var surface = ""
    public var place = ""
    public var title = ""
    public var icon = ""
    public var packageName = ""
    public var author = ""
    public var dataUrl = ""
    public var shown = true
    /// A draft made by talking to Kelo (not installed yet): its screen carries a small "Draft" badge, nothing else differs.
    public var draft = false
    public var id: String { key }

    public init(key: String = "", packageId: String = "", surface: String = "", place: String = "", title: String = "", icon: String = "",
                packageName: String = "", author: String = "", dataUrl: String = "", shown: Bool = true, draft: Bool = false) {
        self.key = key; self.packageId = packageId; self.surface = surface; self.place = place; self.title = title; self.icon = icon
        self.packageName = packageName; self.author = author; self.dataUrl = dataUrl; self.shown = shown; self.draft = draft
    }

    private enum K: String, CodingKey {
        case key, surface, place, title, icon, author, shown, draft
        case packageId = "package", packageName = "package_name", dataUrl = "data_url"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        key = (try? c.decodeIfPresent(String.self, forKey: .key)) ?? ""
        packageId = (try? c.decodeIfPresent(String.self, forKey: .packageId)) ?? ""
        surface = (try? c.decodeIfPresent(String.self, forKey: .surface)) ?? ""
        place = (try? c.decodeIfPresent(String.self, forKey: .place)) ?? ""
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        icon = (try? c.decodeIfPresent(String.self, forKey: .icon)) ?? ""
        packageName = (try? c.decodeIfPresent(String.self, forKey: .packageName)) ?? ""
        author = (try? c.decodeIfPresent(String.self, forKey: .author)) ?? ""
        dataUrl = (try? c.decodeIfPresent(String.self, forKey: .dataUrl)) ?? ""
        shown = (try? c.decodeIfPresent(Bool.self, forKey: .shown)) ?? true
        draft = (try? c.decodeIfPresent(Bool.self, forKey: .draft)) ?? false
    }
}

/// GET /api/nest/extensions: `places` by place name; `maxTabs` is how many Nest tabs the bar may hold.
public struct NestExtensions: Decodable, Equatable, Sendable {
    public var maxTabs = 0
    public var places: [String: [NestEntry]] = [:]

    private enum K: String, CodingKey { case maxTabs = "max_tabs", places }

    public init(maxTabs: Int = 0, places: [String: [NestEntry]] = [:]) { self.maxTabs = maxTabs; self.places = places }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        maxTabs = (try? c.decodeIfPresent(Int.self, forKey: .maxTabs)) ?? 0
        places = (try? c.decodeIfPresent([String: [NestEntry]].self, forKey: .places)) ?? [:]
    }
}

/// The places an extension can be put (the server's `place` names).
public enum NestPlace {
    public static let tab = "tab", mySection = "my_section", homeCard = "home_card", chatCard = "chat_card"
}

/// Draws one extension's surface. The app root provides one through the environment; the screens never draw a Nest entry themselves.
@MainActor public protocol NestSurfaceView {
    func view(for entry: NestEntry) -> AnyView
}

private struct NestSurfaceKey: EnvironmentKey {
    static let defaultValue: (any NestSurfaceView)? = nil
}

public extension EnvironmentValues {
    /// The renderer of Nest surfaces; nil = there is none, and then no Nest entry shows anywhere.
    var nestSurfaceView: (any NestSurfaceView)? {
        get { self[NestSurfaceKey.self] }
        set { self[NestSurfaceKey.self] = newValue }
    }
}

/// What the server offers, as the screens read it.
public struct NestUiState: Equatable, Sendable {
    public var loaded = false
    public var maxTabs = 0
    public var byPlace: [String: [NestEntry]] = [:]

    public init(loaded: Bool = false, maxTabs: Int = 0, byPlace: [String: [NestEntry]] = [:]) {
        self.loaded = loaded; self.maxTabs = maxTabs; self.byPlace = byPlace
    }

    public static let empty = NestUiState()

    /// The entries of a place the owner sees (only the ones marked `shown`).
    public func entries(_ place: String) -> [NestEntry] { (byPlace[place] ?? []).filter(\.shown) }

    /// The entries of `place` to show: none while the app has no renderer.
    public func entries(_ place: String, surface: (any NestSurfaceView)?) -> [NestEntry] {
        surface == nil ? [] : entries(place)
    }
}

/// Fetches the extensions once per session and again on `reload()`. A 404 (an older server), a failure or an empty answer is an empty
/// list; a newer read replaces a running one.
@MainActor @Observable public final class NestModel {
    public private(set) var state = NestUiState.empty
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?

    public init(api: KeloApi) { self.api = api }

    public func reload() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            let next: NestUiState
            do {
                let x: NestExtensions = try await api.get("/api/nest/extensions")
                next = NestUiState(loaded: true, maxTabs: x.maxTabs, byPlace: x.places)
            } catch {
                if error is CancellationError { return }
                next = NestUiState(loaded: true)
            }
            if !Task.isCancelled { state = next }
        }
        loading = task
        await task.value
    }

    /// The owner picks which package tabs the bar shows (at most `maxTabs`): PUT /api/nest/extensions/tabs; the answer is the new list.
    /// Returns the server's words when it refused ("" = failed without a reason), nil when it worked.
    public func setTabs(_ keys: [String]) async -> String? {
        do {
            let body: JSON = ["show": .array(keys.prefix(max(0, state.maxTabs)).map { JSON.string($0) })]
            let x: NestExtensions = try await api.put("/api/nest/extensions/tabs", body: body)
            state = NestUiState(loaded: true, maxTabs: x.maxTabs, byPlace: x.places)
            return nil
        } catch let e as ApiError {
            return e.detail
        } catch {
            return ""
        }
    }

    /// Once per session: a second call while loaded (or loading) does nothing.
    public func loadOnce() async {
        guard !state.loaded, loading == nil else { return }
        await reload()
    }
}
