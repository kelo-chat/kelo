import SwiftUI

// The tabs as data (Android shell/Tabs.kt TabSpec / tabsFor): the bar draws the list `tabsFor` returns.

/// A tab's name: a text of the app's own, or the name a Nest package gave its tab.
public enum ShellTabTitle: Equatable, Sendable {
    case res(StringKey)
    case text(String)
}

/// One tab of the signed-in app. `id` is the built-in tab's raw name or "nest/<key>"; `builtIn` is set for the built-in ones.
public struct TabSpec: Identifiable {
    public let id: String
    public let title: ShellTabTitle
    public let icon: KeloIcon
    public let builtIn: KeloTab?
    public let fromNest: Bool
    public let content: @MainActor () -> AnyView

    public init(id: String, title: ShellTabTitle, icon: KeloIcon, builtIn: KeloTab? = nil, fromNest: Bool = false,
                content: @escaping @MainActor () -> AnyView) {
        self.id = id; self.title = title; self.icon = icon; self.builtIn = builtIn; self.fromNest = fromNest; self.content = content
    }

    @MainActor public var label: String {
        switch title {
        case .res(let k): L(k)
        case .text(let t): t
        }
    }
}

/// Which built-in tabs the owner hides. 채팅 and 마이 are always there; 다짐 and 커넥터 may be hidden, and then My has a row for each.
/// The default is today's tabs. Editing and keeping it is Nest work: no screen for it and nothing stored yet.
public struct TabLayout: Equatable, Sendable {
    public var hidden: Set<String>
    public init(hidden: Set<String> = []) { self.hidden = hidden }

    public static let removable: Set<String> = [KeloTab.goals.rawValue, KeloTab.connectors.rawValue]
    public static let `default` = TabLayout()

    public func hides(_ id: String) -> Bool { hidden.contains(id) && Self.removable.contains(id) }
}

public enum Tabs {
    /// The built-in tabs in today's order; `content` gives a tab's page.
    @MainActor public static func builtIn(content: @escaping @MainActor (KeloTab) -> AnyView) -> [TabSpec] {
        KeloTab.allCases.map { tab in
            TabSpec(id: tab.rawValue, title: .res(tab.labelKey), icon: tab.icon, builtIn: tab, content: { content(tab) })
        }
    }

    /// The tabs to show: the built-in ones the layout does not hide (채팅 and 마이 always stay), then the Nest `tab` entries marked
    /// `shown`, at most `maxTabs` of them, in the server's order, after 커넥터 and before 마이. `nest` must be empty while the app has
    /// no renderer (`NestUiState.entries(_:surface:)` does that); `page` draws an entry.
    @MainActor public static func `for`(builtIn: [TabSpec], nest: [NestEntry], layout: TabLayout = .default, maxTabs: Int = 0,
                                        page: @escaping @MainActor (NestEntry) -> AnyView = { _ in AnyView(EmptyView()) }) -> [TabSpec] {
        let kept = builtIn.filter { $0.builtIn == .chat || $0.builtIn == .my || !layout.hides($0.id) }
        let added = nest.filter(\.shown).prefix(max(0, maxTabs)).map { e in
            TabSpec(id: "nest/\(e.key)", title: .text(e.title.isEmpty ? e.packageName : e.title), icon: .spark, fromNest: true,
                    content: { page(e) })
        }
        guard let my = kept.firstIndex(where: { $0.builtIn == .my }) else { return kept + added }
        return Array(kept[..<my]) + added + Array(kept[my...])
    }

}
