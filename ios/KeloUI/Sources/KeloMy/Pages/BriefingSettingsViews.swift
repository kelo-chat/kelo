import SwiftUI
import KeloCore
import KeloShared

/// The briefing settings' small rules, apart from the UI (Android my/BriefingSettings.kt BriefingSettingsRules).
public enum BriefingSettingsRules {
    public static let offsets = [0, 10, 20, 30, 45, 60]
    public static let sections = ["weather", "calendar", "missed"]
    public static let providers = ["auto", "open_meteo", "kma", "weatherapi"]
    public static let keyed = ["kma", "weatherapi"]

    /// The offset choices, with the current one kept even when it is not a preset.
    public static func offsets(current: Int) -> [Int] { Array(Set(offsets + [min(60, max(0, current))])).sorted() }

    public static func sectionOn(_ b: BriefingSettings, _ id: String) -> Bool { b.sections[id] ?? true }

    /// A key the owner pastes: one line, no spaces, 8...512 characters (the server checks it with a real request).
    public static func keyOK(_ key: String) -> Bool {
        let t = key.trimmingCharacters(in: .whitespacesAndNewlines)
        return (8...512).contains(t.count) && !t.contains(where: \.isWhitespace)
    }

    /// The quiet "weather shows once location is allowed" line: the weather is in the briefing, and there is no location to use.
    public static func needsLocation(_ b: BriefingSettings, granted: Bool) -> Bool {
        sectionOn(b, "weather") && (!granted || b.weather.here == nil)
    }
}

/// The "what it reads" list's small rules (Android my/BriefingSources.kt BriefingSourcesRules).
public enum BriefingSourcesRules {
    public static let kinds = ["calendar", "mail"]

    public static func group(_ s: BriefingSources, _ kind: String) -> SourceGroup { kind == "mail" ? s.mail : s.calendar }
    /// The line that names the account: its address, or the provider's name when it has none (the phone's calendar).
    public static func title(_ a: SourceAccount) -> String { a.address.isBlank ? a.name : a.address }
    /// The line under it: the provider, when the title is the address.
    public static func subtitle(_ a: SourceAccount) -> String { a.address.isBlank ? "" : a.name }
    /// Groups worth showing: one with no account has nothing to choose.
    public static func shown(_ s: BriefingSources) -> [String] { kinds.filter { !group(s, $0).accounts.isEmpty } }

    public static func toggleBody(_ kind: String, _ id: String, _ on: Bool) -> JSON { [kind: ["accounts": [id: .bool(on)]]] }

    /// The list as it will be after one switch is flipped (shown at once, before the server answers).
    public static func flipped(_ s: BriefingSources, _ kind: String, _ id: String, _ on: Bool) -> BriefingSources {
        var out = s
        var g = group(s, kind)
        g.accounts = g.accounts.map { var a = $0; if a.id == id { a.on = on }; return a }
        if kind == "mail" { out.mail = g } else { out.calendar = g }
        return out
    }
}

/// 마이 → 알림 → 아침 브리핑 (Android BriefingBlock): on or off; when (at the start of the day's hours plus a few minutes, earlier on the
/// morning's first app open, or a fixed time); which sections (turning the weather on asks for location "while using the app": the
/// weather is always where the phone last was, sent when the app is opened; without any location the briefing leaves the weather out
/// and one quiet line offers to allow it); quiet days; the weather service and the owner's own keys; "Get one now".
struct BriefingBlock: View {
    let settings: BriefingSettings
    let onChanged: (BriefingSettings) -> Void
    let toast: (String) -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var picking = false
    @State private var pickTime = Date()
    @State private var keyFor: String?
    @State private var sent: Bool?
    @State private var granted = PhonePlace.granted
    @Environment(\.scenePhase) private var phase

    var body: some View {
        let b = settings
        ToggleRow(L(R.string.briefing_switch), detail: L(R.string.briefing_about), isOn: Binding(get: { b.enabled }, set: { on in
            Haptics.tick(); put(["enabled": .bool(on)])
        }))
        .accessibilityIdentifier("kelo.briefing.switch")
        if b.enabled {
            label(L(R.string.briefing_when))
            SelectField(title: L(R.string.briefing_when),
                        options: [SelectOption(value: "auto", label: L(R.string.briefing_when_auto)),
                                  SelectOption(value: "fixed", label: L(R.string.briefing_when_fixed))],
                        selected: b.timeMode) { v in put(["time_mode": .string(v)]) }
            if b.timeMode == "fixed" {
                SecondaryButton(L(R.string.briefing_fixed_at, timeWords(b.fixedTime)), fill: true) { openPicker(b.fixedTime) }
            } else {
                MutedText(L(R.string.briefing_offset), small: true)
                SelectField(title: L(R.string.briefing_offset),
                            options: BriefingSettingsRules.offsets(current: b.offsetMin).map { SelectOption(value: String($0), label: offsetLabel($0)) },
                            selected: String(b.offsetMin)) { v in if let n = Int(v) { put(["offset_min": .number(Double(n))]) } }
                ToggleRow(L(R.string.briefing_early_open), detail: L(R.string.briefing_early_open_about),
                          isOn: Binding(get: { b.earlyOpen }, set: { on in Haptics.tick(); put(["early_open": .bool(on)]) }))
            }

            // what
            label(L(R.string.briefing_sections)).padding(.top, 8)
            VStack(alignment: .leading, spacing: 2) {
                ForEach(BriefingSettingsRules.sections, id: \.self) { id in
                    let words: StringKey = id == "weather" ? R.string.briefing_weather : (id == "calendar" ? R.string.briefing_today : R.string.briefing_missed)
                    CheckLine(text: L(words), on: BriefingSettingsRules.sectionOn(b, id)) { on in
                        put(["sections": [id: .bool(on)]])
                        if id == "weather" && on && !PhonePlace.granted { allowPlace() }   // the weather needs to know where the phone is
                    }
                    if id == "weather" && BriefingSettingsRules.sectionOn(b, id) {
                        VStack(alignment: .leading, spacing: 6) {
                            if BriefingSettingsRules.needsLocation(b, granted: granted) {
                                MutedText(L(R.string.briefing_weather_needs_location), small: true)
                                SecondaryButton(L(R.string.briefing_location_allow)) { allowPlace() }
                                    .accessibilityIdentifier("kelo.briefing.location")
                            }
                            MutedText(L(R.string.briefing_location_privacy), small: true)
                        }
                        .padding(.leading, 34).padding(.bottom, 4)
                    }
                }
                CheckLine(text: L(R.string.briefing_quiet_skip), on: b.quietDays == "skip") { on in put(["quiet_days": .string(on ? "skip" : "short")]) }
            }

            // the weather service
            label(L(R.string.briefing_provider)).padding(.top, 8)
            SelectField(title: L(R.string.briefing_provider),
                        options: BriefingSettingsRules.providers.map { SelectOption(value: $0, label: providerLabel($0)) },
                        selected: b.weather.provider) { v in put(["provider": .string(v)]) }
            MutedText(L(R.string.briefing_provider_about), small: true)
            ForEach(BriefingSettingsRules.keyed, id: \.self) { id in
                let saved = b.weather.keys[id] == true
                HStack(spacing: 10) {
                    KeloIconView(.key, size: 18, tint: c.muted)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(providerLabel(id)).font(KeloFont.body).foregroundStyle(c.ink)
                        Text(L(saved ? R.string.briefing_key_saved : R.string.briefing_key_none)).font(KeloFont.small).foregroundStyle(c.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if saved {
                        SecondaryButton(L(R.string.briefing_key_delete)) {
                            call { try await $0.request("DELETE", "/api/briefing/keys/\(id)") }
                        }
                    } else {
                        SecondaryButton(L(R.string.briefing_key_add)) { keyFor = id }
                    }
                }
            }
            // the data's licence (CC BY 4.0) asks for this credit
            MutedText(b.weather.providers.first { $0.id == "open_meteo" }?.attribution ?? "Weather data by Open-Meteo.com", small: true)
            SecondaryButton(L(sent == true ? R.string.briefing_sent : R.string.briefing_try), enabled: sent == nil, fill: true) {
                sent = false
                Task {
                    do { _ = try await app.requireApi().request("POST", "/api/briefing/run"); sent = true } catch { sent = nil; toast(app.message(for: error)) }
                }
            }
            .accessibilityIdentifier("kelo.briefing.try")
        }
        Color.clear.frame(height: 0)
            // back from iOS Settings: location may be allowed now
            .onChange(of: phase) { _, now in
                guard now == .active else { return }
                let allowed = PhonePlace.granted
                if allowed && !granted { granted = true; allowPlace() } else { granted = allowed }
            }
            .keloSheet(isPresented: $picking, detents: [.medium]) {
                VStack(spacing: 12) {
                    SheetHeader(L(R.string.briefing_when_fixed)) { picking = false }
                    DatePicker("", selection: $pickTime, displayedComponents: .hourAndMinute).labelsHidden()
                        #if os(iOS)
                        .datePickerStyle(.wheel)
                        #endif
                    PrimaryButton(L(R.string.ok)) {
                        let comps = Calendar.current.dateComponents([.hour, .minute], from: pickTime)
                        put(["fixed_time": .string(NotifySettingsRules.formatTime(comps.hour ?? 7, comps.minute ?? 30))])
                        picking = false
                    }
                }
                .padding(.horizontal, 20).padding(.bottom, 24)
            }
            .keloSheet(item: Binding(get: { keyFor.map(KeyFor.init) }, set: { keyFor = $0?.id }), detents: [.medium, .large]) { k in
                BriefingKeySheet(id: k.id, name: providerLabel(k.id), onClose: { keyFor = nil }) { got in keyFor = nil; onChanged(got) }
            }
    }

    private struct KeyFor: Identifiable { let id: String }

    private func label(_ t: String) -> some View {
        Text(t).font(KeloFont.titleSmall).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
    }

    private func providerLabel(_ id: String) -> String {
        switch id {
        case "open_meteo": "Open-Meteo"
        case "kma": L(R.string.briefing_provider_kma)
        case "weatherapi": "WeatherAPI.com"
        default: L(R.string.briefing_provider_auto)
        }
    }

    private func offsetLabel(_ min: Int) -> String { min == 0 ? L(R.string.briefing_offset_none) : L(R.string.briefing_offset_min, min) }

    private func timeWords(_ text: String) -> String { NotifySettingsRules.parseTime(text).map { MyFormat.clock($0.hour, $0.minute) } ?? text }

    private func openPicker(_ value: String) {
        let t = NotifySettingsRules.parseTime(value) ?? (7, 30)
        pickTime = Calendar.current.date(bySettingHour: t.hour, minute: t.minute, second: 0, of: Date()) ?? Date()
        picking = true
    }

    private func put(_ body: JSON) { call { try await $0.put("/api/briefing", body: body, as: BriefingSettings.self) } }

    private func call(_ block: @escaping (KeloApi) async throws -> Any) {
        Task {
            do {
                let api = try app.requireApi()
                let got = try await block(api)
                if let s = got as? BriefingSettings { onChanged(s) }
                else if let s = try? await api.get("/api/briefing", as: BriefingSettings.self) { onChanged(s) }
            } catch { toast(app.message(for: error)) }
        }
    }

    /// Location "while using the app" (iOS lets the owner pick precise or approximate), asked right here; once allowed the place goes
    /// now and then whenever the app is opened. After a "no" only iOS Settings can allow it.
    private func allowPlace() {
        Task {
            if PhonePlace.refused { DeviceInfo.openAppSettings(); return }
            granted = await PhonePlace.ask()
            guard granted, let api = app.api else { return }
            await PhonePlace.maybeSend(api: api, force: true)
            if let s = try? await api.get("/api/briefing", as: BriefingSettings.self) { onChanged(s) }
        }
    }
}

/// A check box line (a briefing section, quiet days).
private struct CheckLine: View {
    let text: String
    let on: Bool
    let onChange: (Bool) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        Button { Haptics.tick(); onChange(!on) } label: {
            HStack(spacing: 10) {
                Image(systemName: on ? "checkmark.square.fill" : "square").font(.title3).foregroundStyle(on ? c.accent : c.muted)
                Text(text).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 6).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? [.isSelected, .isButton] : .isButton)
    }
}

/// The owner's own key for a weather service: pasted, checked by the server with one real request, sealed in the vault.
private struct BriefingKeySheet: View {
    let id: String
    let name: String
    let onClose: () -> Void
    let onSaved: (BriefingSettings) -> Void
    @Environment(AppModel.self) private var app
    @State private var key = ""
    @State private var error = ""
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SheetHeader(name, onClose: onClose)
            MutedText(L(id == "kma" ? R.string.briefing_key_help_kma : R.string.briefing_key_help_weatherapi), small: true)
            KeloTextField(L(R.string.briefing_key_placeholder), text: $key, secure: true, keyboard: .ascii, maxLength: 512)
            if !error.isEmpty { ErrorText(error, small: true) }
            PrimaryButton(L(R.string.briefing_key_save), enabled: !busy && BriefingSettingsRules.keyOK(key), busy: busy) {
                busy = true
                Task {
                    defer { busy = false }
                    do {
                        onSaved(try await app.requireApi().post("/api/briefing/keys/\(id)", body: ["key": .string(key.trimmingCharacters(in: .whitespacesAndNewlines))],
                                                                  as: BriefingSettings.self))
                    } catch { self.error = app.message(for: error) }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.bottom, 24)
        .keloSensitive()
    }
}

/// "What it reads": every connected calendar and mailbox, each with one switch, in two groups. All are on by default and a newly
/// connected account starts on. An account that could not be read shows one quiet line with what to do.
struct BriefingSourcesBlock: View {
    let toast: (String) -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var sources: BriefingSources?
    @State private var checking = false

    var body: some View {
        Group {
            if let s = sources, !BriefingSourcesRules.shown(s).isEmpty {
                SectionLabel(L(R.string.briefing_sources_title)).padding(.horizontal, -16)
                VStack(alignment: .leading, spacing: 8) {
                    MutedText(L(R.string.briefing_sources_about), small: true)
                    ForEach(BriefingSourcesRules.shown(s), id: \.self) { kind in group(s, kind) }
                    MutedText(L(R.string.briefing_sources_chat), small: true)
                    SecondaryButton(L(checking ? R.string.briefing_sources_checking : R.string.briefing_sources_check), enabled: !checking, fill: true) {
                        checking = true
                        Task {
                            defer { checking = false }
                            do { sources = try await app.requireApi().post("/api/briefing/sources/check", as: BriefingSources.self) }
                            catch { toast(app.message(for: error)) }
                        }
                    }
                }
                .accessibilityIdentifier("kelo.briefing.sources")
            } else {
                // a view of no height, so the load below runs (an empty group never appears)
                Color.clear.frame(height: 0)
            }
        }
        .task { sources = try? await app.api?.get("/api/briefing/sources", as: BriefingSources.self) }
    }

    @ViewBuilder private func group(_ s: BriefingSources, _ kind: String) -> some View {
        let g = BriefingSourcesRules.group(s, kind)
        let name = L(kind == "mail" ? R.string.briefing_sources_mail : R.string.briefing_sources_calendars)
        Text(name).font(KeloFont.titleSmall).foregroundStyle(c.ink).padding(.top, 6)
        ForEach(Array(g.accounts.enumerated()), id: \.offset) { i, a in
            if i > 0 { Hairline() }
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 1) {
                    // one line each: a long address is cut in its middle, a long name at its end (never wrapped mid-word)
                    Text(BriefingSourcesRules.title(a)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(1).truncationMode(.middle)
                    let sub = BriefingSourcesRules.subtitle(a)
                    if !sub.isBlank { Text(sub).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1) }
                    if let p = a.problem { Text(p).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true).padding(.top, 2) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Toggle("", isOn: Binding(get: { a.on }, set: { on in
                    Haptics.tick()
                    sources = BriefingSourcesRules.flipped(s, kind, a.id, on)
                    send(BriefingSourcesRules.toggleBody(kind, a.id, on))
                }))
                .labelsHidden().tint(c.accent)
                .accessibilityLabel(BriefingSourcesRules.title(a))
            }
            .padding(.vertical, 4)
        }
    }

    private func send(_ body: JSON) {
        Task {
            do { sources = try await app.requireApi().put("/api/briefing/sources", body: body, as: BriefingSources.self) }
            catch { toast(app.message(for: error)) }
        }
    }
}
