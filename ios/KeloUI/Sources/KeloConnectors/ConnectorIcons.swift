import SwiftUI
import KeloCore
import KeloShared

/// The services' app icons from the server (GET /api/connectors/icons/…), decoded once and kept in memory, and the files kept in
/// Caches/connector-icons so the list shows them at once next time and offline (Android connectors/ConnectorIcons.kt). A URL that
/// cannot be fetched gives nil and the tile keeps its initials.
@MainActor final class ConnectorIcons {
    static let shared = ConnectorIcons()
    private var memory: [String: PlatformImage] = [:]
    private var loading: [String: Task<PlatformImage?, Never>] = [:]
    private let dir: URL? = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
        .appendingPathComponent("connector-icons", isDirectory: true)

    func cached(_ url: String) -> PlatformImage? { memory[url] }

    func load(_ url: String, api: KeloApi?) async -> PlatformImage? {
        guard ConnectorIconRules.valid(url) else { return nil }
        if let i = memory[url] { return i }
        if let t = loading[url] { return await t.value }
        let dir = self.dir
        let t = Task<PlatformImage?, Never> {
            let name = ConnectorIconRules.fileName(url)
            let file = dir?.appendingPathComponent(name)
            if let file, let d = try? Data(contentsOf: file), let img = PlatformImage(data: d) { return img }
            guard let api, let d = try? await api.download(url), let img = PlatformImage(data: d) else { return nil }
            if let dir, let file {
                let fm = FileManager.default
                try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
                for old in (try? fm.contentsOfDirectory(atPath: dir.path)) ?? [] where ConnectorIconRules.stale(url, kept: old) {
                    try? fm.removeItem(at: dir.appendingPathComponent(old))   // older versions of the same icon
                }
                try? d.write(to: file, options: .atomic)
            }
            return img
        }
        loading[url] = t
        let img = await t.value
        loading[url] = nil
        if let img { memory[url] = img }
        return img
    }
}

/// A service's picture: its own app icon in a rounded square when the server has one, else the initials tile. While the icon loads,
/// a soft square in its colour holds the place, so the row does not jump.
struct ServiceIcon<Fallback: View>: View {
    let icon: String
    let small: String
    let color: String
    let size: CGFloat
    @ViewBuilder let fallback: () -> Fallback
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var image: PlatformImage?
    @State private var failed = false

    var body: some View {
        let url = ConnectorIconRules.pick(icon: icon, small: small, size: Double(size))
        let shape = RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
        Group {
            if url.isEmpty || failed {
                fallback()
            } else if let img = image ?? ConnectorIcons.shared.cached(url) {
                Image(platformImage: img).resizable().interpolation(.high).scaledToFill()
                    .frame(width: size, height: size).clipShape(shape)
                    .overlay(shape.stroke(c.line, lineWidth: 1))
            } else {
                shape.fill(tint).frame(width: size, height: size)
            }
        }
        .accessibilityHidden(true)
        .task(id: url) {
            guard !url.isEmpty, ConnectorIcons.shared.cached(url) == nil else { return }
            failed = false
            image = await ConnectorIcons.shared.load(url, api: app.api)
            failed = image == nil
        }
    }

    private var tint: Color {
        guard let (r, g, b) = ConnectorIconRules.rgb(color) else { return c.bubble }
        return Color(red: r, green: g, blue: b).opacity(0.18)
    }
}
