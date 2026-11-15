import Foundation
import Observation
import SwiftUI
import KeloCore

/// The agent's picture (GET /api/agent/avatar), by version (Android agent/AgentAvatar.kt). 0 = none: the header shows the
/// character instead. Kept in the screen cache so the header shows it at once next time.
@MainActor @Observable
public final class AgentAvatarModel {
    public private(set) var image: PlatformImage?
    @ObservationIgnored private var shown: Int64 = -1

    public init() {}

    private struct Blob: Codable { var version: Int64; var data: Data }

    /// Brings the picture in step with `version`.
    public func sync(_ version: Int64, app: AppModel) async {
        guard version != shown else { return }
        if version <= 0 {
            image = nil; shown = 0
            app.cache.put("avatar", Blob(version: 0, data: Data()))
            return
        }
        if let kept: Blob = app.cache.get("avatar"), kept.version == version, let img = PlatformImage(data: kept.data) {
            image = img; shown = version
            return
        }
        guard let api = app.api else { return }
        do {
            let bytes = try await api.avatar()
            guard let img = PlatformImage(data: bytes) else { return }
            image = img; shown = version
            app.cache.put("avatar", Blob(version: version, data: bytes))
        } catch let e as ApiError {
            if e.status == 404 { image = nil; shown = 0 }
        } catch {}
    }

    /// After this phone saved a new picture (the My page's agent page).
    public func set(_ img: PlatformImage?, version: Int64, data: Data?, app: AppModel) {
        image = img; shown = version
        app.cache.put("avatar", Blob(version: version, data: data ?? Data()))
    }

    public func reset() { image = nil; shown = -1 }
}

/// The agent's photo in a circle, or Kelo's character (in `mood`) when it has none (Android agent/AgentParts.kt AgentPicture).
public struct AgentPicture: View {
    let name: String
    let image: PlatformImage?
    let size: CGFloat
    let mood: KeloMood

    public init(name: String, image: PlatformImage?, size: CGFloat, mood: KeloMood = .idle) {
        self.name = name; self.image = image; self.size = size; self.mood = mood
    }

    public var body: some View {
        Group {
            if let image {
                Image(platformImage: image).resizable().scaledToFill()
                    .frame(width: size, height: size)
                    .clipShape(Circle())
            } else {
                KeloCharacterView(mood)   // as Android's AgentPicture: Kelo's character, unclipped, when there is no picture
                    .frame(width: size, height: size)
            }
        }
        .accessibilityHidden(true)
    }
}

/// The language list (Android start/LanguageScreen.kt LanguageList): following the phone first, then every language in its own
/// name and script. Used by 마이 → 언어.
public struct LanguageList: View {
    @Binding var value: String
    @Environment(\.kelo) private var c

    public init(value: Binding<String>) { _value = value }

    public var body: some View {
        MenuCard {
            RadioRow(L(R.string.language_system), detail: Languages.name(Languages.suggested(Locale.preferredLanguages)),
                     selected: value == Languages.system) { value = Languages.system }
            ForEach(Languages.all) { lang in
                Hairline()
                RadioRow(lang.name, selected: value == lang.code) { value = lang.code }
                    .environment(\.layoutDirection, Languages.isRightToLeft(lang.code) ? .rightToLeft : .leftToRight)
                    .environment(\.locale, Locale(identifier: lang.code))
            }
        }
        .accessibilityLabel(L(R.string.language_title))
    }
}
