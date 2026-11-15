import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 알림: follow-ups (다시 묻기), "Kelo가 먼저 알려주기" with the hours it may write and its list of things to keep an eye on.
/// Each save answers with the whole setting; only the newest answer of a kind is kept, so two quick taps never leave the older one.
@MainActor @Observable final class NotifySettingsModel {
    private(set) var proactive: ProactiveSettings?
    private(set) var checklist: ChecklistSettings?
    private(set) var followups: FollowupSettings?
    private(set) var followupsMissing = false
    private(set) var proactiveMissing = false
    private(set) var checklistMissing = false
    private(set) var error = ""
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var proactiveSeq = Latest()
    @ObservationIgnored private var checklistSeq = Latest()
    @ObservationIgnored private var followupsSeq = Latest()
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearNotice() { notice = nil }

    func load() async {
        do { proactive = try await api.get("/api/proactive") } catch let e as ApiError { if e.status == 404 { proactiveMissing = true } else { error = ApiMessage.text(e) } } catch {}
        do { checklist = try await api.get("/api/checklist") } catch let e as ApiError { if e.status == 404 { checklistMissing = true } else { error = ApiMessage.text(e) } } catch {}
        do { followups = try await api.get("/api/followups/settings") } catch let e as ApiError { if e.status == 404 { followupsMissing = true } else { error = ApiMessage.text(e) } } catch {}
    }

    func putProactive(_ b: [String: JSON]) {
        let n = proactiveSeq.begin()
        jobs.run { [self] in
            do {
                let p: ProactiveSettings = try await api.put("/api/proactive", body: .object(b))
                if proactiveSeq.accept(n) { proactive = p }
            } catch { notice = ApiMessage.text(error) }
        }
    }

    func postHours(_ path: String) {
        let n = proactiveSeq.begin()
        jobs.run { [self] in
            do {
                let p = try await api.post(path, as: ProactiveSettings.self)
                if proactiveSeq.accept(n) { proactive = p }
            } catch { notice = ApiMessage.text(error) }
        }
    }

    func putChecklist(_ items: [String]) {
        let n = checklistSeq.begin()
        jobs.run { [self] in
            do {
                let c: ChecklistSettings = try await api.put("/api/checklist", body: ["items": .array(items.map { .string($0) })])
                if checklistSeq.accept(n) { checklist = c }
            } catch { notice = ApiMessage.text(error) }
        }
    }

    func putFollowups(_ b: [String: JSON]) {
        let n = followupsSeq.begin()
        jobs.run { [self] in
            do {
                let f: FollowupSettings = try await api.put("/api/followups/settings", body: .object(b))
                if followupsSeq.accept(n) { followups = f }
            } catch { notice = ApiMessage.text(error) }
        }
    }
}

/// 마이 → 알림 → 아침 브리핑: the briefing's settings, the owner's own weather keys, "Get one now", and the location the weather needs.
@MainActor @Observable final class BriefingSettingsModel {
    private(set) var settings: BriefingSettings?
    private(set) var missing = false
    /// "Get one now": nil = not asked, false = on its way, true = sent
    private(set) var sent: Bool?
    private(set) var granted = PhonePlace.granted
    private(set) var servicesOn = PhonePlace.servicesOn
    private(set) var notice: String?
    /// The weather key sheet
    private(set) var keyBusy = false
    private(set) var keyError = ""
    /// Goes up when a key was saved (the sheet closes)
    private(set) var keySaved = 0
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var seq = Latest()
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearNotice() { notice = nil }

    func load() async {
        do { settings = try await api.get("/api/briefing", as: BriefingSettings.self) } catch let e as ApiError { if e.status == 404 { missing = true } } catch {}
    }

    func put(_ body: JSON) { call { try await $0.put("/api/briefing", body: body, as: BriefingSettings.self) } }

    func deleteKey(_ id: String) { call { try await $0.request("DELETE", "/api/briefing/keys/\(id)"); return nil } }

    /// A call that answers with the settings (or nothing: then they are read again).
    private func call(_ block: @escaping @Sendable (KeloApi) async throws -> BriefingSettings?) {
        let n = seq.begin()
        jobs.run { [self] in
            do {
                let got = try await block(api)
                let fresh: BriefingSettings?
                if let got { fresh = got } else { fresh = try? await api.get("/api/briefing", as: BriefingSettings.self) }
                if let fresh, seq.accept(n) { settings = fresh }
            } catch { notice = ApiMessage.text(error) }
        }
    }

    func sendNow() {
        sent = false
        jobs.run { [self] in
            do { _ = try await api.request("POST", "/api/briefing/run"); sent = true } catch { sent = nil; notice = ApiMessage.text(error) }
        }
    }

    func saveKey(_ id: String, key: String) {
        keyBusy = true; keyError = ""
        jobs.run { [self] in
            defer { keyBusy = false }
            do {
                settings = try await api.post("/api/briefing/keys/\(id)", body: ["key": .string(key.trimmingCharacters(in: .whitespacesAndNewlines))],
                                              as: BriefingSettings.self)
                keySaved += 1
            } catch { keyError = ApiMessage.text(error) }
        }
    }

    func keySheetOpened() { keyError = "" }

    /// Back from iOS Settings: location may be allowed now.
    func becameActive() {
        servicesOn = PhonePlace.servicesOn
        let allowed = PhonePlace.granted
        if allowed && !granted { granted = true; allowPlace() } else { granted = allowed }
    }

    /// Location "while using the app", asked right here; once allowed the place goes now and then whenever the app is opened.
    /// After a "no" only iOS Settings can allow it.
    func allowPlace() {
        jobs.run { [self] in
            if !PhonePlace.servicesOn { servicesOn = false; return }
            if PhonePlace.refused { DeviceInfo.openAppSettings(); return }
            granted = await PhonePlace.ask()
            guard granted else { return }
            await PhonePlace.maybeSend(api: api, force: true)
            if let s = try? await api.get("/api/briefing", as: BriefingSettings.self) { settings = s }
        }
    }
}

/// 마이 → 알림 → 읽는 곳: every connected calendar and mailbox with one switch each.
@MainActor @Observable final class BriefingSourcesModel {
    private(set) var sources: BriefingSources?
    private(set) var checking = false
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var seq = Latest()
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearNotice() { notice = nil }

    func load() async {
        let n = seq.begin()
        let s = try? await api.get("/api/briefing/sources", as: BriefingSources.self)
        if let s, seq.accept(n) { sources = s }
    }

    func check() {
        checking = true
        let n = seq.begin()
        jobs.run { [self] in
            defer { checking = false }
            do { let s = try await api.post("/api/briefing/sources/check", as: BriefingSources.self); if seq.accept(n) { sources = s } }
            catch { notice = ApiMessage.text(error) }
        }
    }

    /// One switch: shown at once, then saved; the server's answer replaces it.
    func toggle(_ kind: String, _ id: String, _ on: Bool) {
        guard let s = sources else { return }
        sources = BriefingSourcesRules.flipped(s, kind, id, on)
        let n = seq.begin()
        jobs.run { [self] in
            do { let got = try await api.put("/api/briefing/sources", body: BriefingSourcesRules.toggleBody(kind, id, on), as: BriefingSources.self); if seq.accept(n) { sources = got } }
            catch { notice = ApiMessage.text(error) }
        }
    }
}
