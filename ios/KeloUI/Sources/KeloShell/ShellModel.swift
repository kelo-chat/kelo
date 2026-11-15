import SwiftUI
import Observation
import KeloCore
import KeloShared

/// What the signed-in shell keeps going while the app is in the foreground (Android ShellViewModel): the agent status poll behind
/// the header (its words, mood and badge live in `AgentActivityModel`), the agent's picture, and the phone's place. Each of these is
/// an async function the shell's `.task(id: phase == .active)` runs, so it ends with the shell and never outlives it.
@MainActor @Observable
final class ShellModel {
    @ObservationIgnored private let activity: AgentActivityModel
    @ObservationIgnored private let avatar: AgentAvatarModel
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let profile: () -> Profile?

    init(activity: AgentActivityModel, avatar: AgentAvatarModel, cache: ScreenCache, api: @escaping () -> KeloApi?, profile: @escaping () -> Profile?) {
        self.activity = activity; self.avatar = avatar; self.cache = cache; self.api = api; self.profile = profile
    }

    /// Asks the agent's status every few seconds until the task ends.
    func pollStatus() async { await activity.poll(api: api) }

    /// The picture follows the status poll's version (the newest word), else the profile's.
    func syncAvatar() async {
        let polled = activity.status.map(\.avatarVersion).flatMap { $0 >= 0 ? $0 : nil }
        guard let v = polled ?? profile()?.avatarVersion else { return }
        await avatar.sync(v, api: api(), cache: cache)
    }

    /// The phone's place, only when allowed, only now that the app is open.
    func sendPlace() async {
        guard let api = api() else { return }
        await PhonePlace.maybeSend(api: api)
    }
}
