import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 프로필: my name and the assistant's name (PUT /api/profile).
@MainActor @Observable final class ProfileModel {
    private(set) var saved = false
    private(set) var error = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private unowned let host: any ProfileHost
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, host: any ProfileHost) { self.api = api; self.host = host }

    var profile: Profile? { host.profile }

    /// The names being typed changed: the "saved" mark goes.
    func edited() { saved = false }

    func save(user: String, agent: String) {
        error = ""
        jobs.run { [self] in
            do {
                let p = try await api.updateProfile(["user_name": user.trimmingCharacters(in: .whitespaces),
                                                     "agent_name": agent.trimmingCharacters(in: .whitespaces)])
                host.setProfile(p)
                saved = true
            } catch {
                self.error = ApiMessage.text(error)
            }
        }
    }
}

/// 마이 → 에이전트: the tone (read again when the page opens, saved on a pick), the name and the agent's picture. A profile read
/// that began before a tone pick (or while one was saving) never puts the old tone back: a newer load cancels the older one, and
/// every pick starts a new `epoch` that makes an answer from before it stale.
@MainActor @Observable final class AgentPageModel {
    private(set) var working = false
    /// The tone being saved (shown as chosen at once; both rows wait until the server answers).
    private(set) var savingTone: String?
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private unowned let host: any ProfileHost
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, host: any ProfileHost) { self.api = api; self.host = host }

    var profile: Profile { host.profile ?? Profile() }
    /// The tone as shown: the one being saved, else the profile's.
    var shownTone: String { savingTone ?? ToneRules.selected(profile) }

    func clearNotice() { notice = nil }

    /// The agent may have changed the tone in the chat ("반말로 해 줘"): the server's profile as it is now.
    func load() async {
        loading?.cancel()
        let started = epoch
        let task = Task { @MainActor [self] in
            guard let p = try? await ToneRules.reload(api), !Task.isCancelled else { return }
            guard epoch == started, savingTone == nil else { return }   // a tone was picked meanwhile: this answer is old
            host.setProfile(p)
        }
        loading = task
        await task.value
    }

    /// A tone tapped: chosen at once, PUT /api/profile {"tone"}, then the saved profile; on failure the old choice comes back.
    func pickTone(_ tone: String) {
        guard savingTone == nil, tone != ToneRules.selected(profile) else { return }
        savingTone = tone
        epoch += 1
        loading?.cancel()
        jobs.run { [self] in
            defer { savingTone = nil; epoch += 1 }
            do {
                host.setProfile(try await ToneRules.save(api, tone: tone))
                Haptics.success()
            } catch {
                notice = L(R.string.my_agent_tone_failed)
            }
        }
    }

    func rename(_ name: String) {
        let name = name.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        jobs.run { [self] in
            do { host.setProfile(try await api.updateProfile(["agent_name": String(name.prefix(60))])) }
            catch { notice = ApiMessage.text(error) }
        }
    }

    /// A picture was picked (its bytes; nil when it could not be read): scaled, uploaded as the agent's.
    func upload(picked raw: Data?) {
        working = true
        jobs.run { [self] in
            defer { working = false }
            guard let raw, let img = PlatformImage(data: raw), let jpeg = AvatarImage.jpeg(img) else { notice = L(R.string.agent_avatar_failed); return }
            if jpeg.count > AvatarRules.maxBytes { notice = L(R.string.agent_avatar_too_large); return }
            do {
                var r = try api.makeRequest("/api/agent/avatar", method: "PUT")
                r.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
                r.httpBody = jpeg
                let saved = try KeloApi.decode(AvatarSaved.self, KeloApi.readJson(await api.send(r)))
                host.avatarChanged(PlatformImage(data: jpeg), version: saved.avatarVersion, data: jpeg)
                Haptics.success()
            } catch let e as ApiError {
                notice = e.status == 413 ? L(R.string.agent_avatar_too_large) : (e.isNetwork ? L(R.string.agent_avatar_failed) : e.detail)
            } catch {
                notice = L(R.string.agent_avatar_failed)
            }
        }
    }

    func removePicture() {
        jobs.run { [self] in
            do {
                try await api.request("DELETE", "/api/agent/avatar")
                host.avatarChanged(nil, version: 0, data: nil)
            } catch {
                notice = L(R.string.agent_avatar_failed)
            }
        }
    }
}
