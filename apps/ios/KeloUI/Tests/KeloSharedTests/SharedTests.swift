import Testing
import Foundation
import CryptoKit
import KeloCore
import KeloTestSupport
@testable import KeloShared

@Suite struct StringsTests {
    @MainActor @Test func everyLanguageHasItsOwnWords() {
        let s = Strings(code: "ko")
        #expect(s.string(R.string.tab_chat) == "채팅")
        #expect(s.string(R.string.tab_goals) == "다짐")
        s.use("en")
        #expect(s.string(R.string.tab_chat) == "Chat")
        for lang in Languages.all {
            s.use(lang.code)
            #expect(s.code == lang.code)
            #expect(!s.string(R.string.tab_my).isEmpty)
            #expect(s.string(R.string.tab_my) != "tab_my", "missing tab_my in \(lang.code)")
        }
    }

    @MainActor @Test func untranslatableFallsBackToEnglish() {
        let s = Strings(code: "ja")
        #expect(s.string(R.string.app_name) == "Kelo")
    }

    @MainActor @Test func placeholdersAreFilled() {
        let s = Strings(code: "en")
        #expect(s.string(R.string.device_name_format, ["iPhone"]) == "iPhone · Kelo")
    }

    @MainActor @Test func pluralsPickTheCategory() {
        let s = Strings(code: "en")
        #expect(s.plural(R.plurals.count_new, 1, [1]) == "1 new item")
        #expect(s.plural(R.plurals.count_new, 3, [3]) == "3 new items")
        s.use("ko")
        #expect(s.plural(R.plurals.count_new, 3, [3]).contains("3"))
    }

    @MainActor @Test func arraysKeepTheirOrder() {
        let s = Strings(code: "en")
        let titles = s.array(R.array.privacy_titles)
        #expect(!titles.isEmpty)
        #expect(titles.allSatisfy { !$0.isEmpty })
    }

    @Test func formatterHandlesAndroidSyntax() {
        #expect(StringFormat.format("%1$s and %2$s", ["a", "b"]) == "a and b")
        #expect(StringFormat.format("%2$s before %1$s", ["a", "b"]) == "b before a")
        #expect(StringFormat.format("%d items, %s", [3, "x"]) == "3 items, x")
        #expect(StringFormat.format("100%% sure %1$d", [5]) == "100% sure 5")
        #expect(StringFormat.format("missing %2$s", ["a"]) == "missing %2$s")
        #expect(StringFormat.format("50% off", []) == "50% off")
    }

    @Test func pluralRules() {
        #expect(PluralRules.category(1, language: "en") == "one")
        #expect(PluralRules.category(0, language: "en") == "other")
        #expect(PluralRules.category(0, language: "fr") == "one")
        #expect(PluralRules.category(21, language: "ru") == "one")
        #expect(PluralRules.category(22, language: "ru") == "few")
        #expect(PluralRules.category(25, language: "ru") == "many")
        #expect(PluralRules.category(12, language: "uk") == "many")
        #expect(PluralRules.category(22, language: "pl") == "few")
        #expect(PluralRules.category(21, language: "pl") == "many")
        #expect(PluralRules.category(0, language: "ar") == "zero")
        #expect(PluralRules.category(2, language: "ar") == "two")
        #expect(PluralRules.category(105, language: "ar") == "few")
        #expect(PluralRules.category(111, language: "ar") == "many")
        #expect(PluralRules.category(100, language: "ar") == "other")
        #expect(PluralRules.category(1, language: "ko") == "other")
        #expect(PluralRules.category(1, language: "pt-BR") == "one")
        #expect(PluralRules.category(1_000_000, language: "es") == "many")
    }
}

@Suite struct LanguagesTests {
    @Test func matchesDeviceTags() {
        #expect(Languages.match("en-US") == "en")
        #expect(Languages.match("zh-Hant-TW") == "zh-Hant")
        #expect(Languages.match("zh-HK") == "zh-Hant")
        #expect(Languages.match("zh-CN") == "zh-Hans")
        #expect(Languages.match("zh") == "zh-Hans")
        #expect(Languages.match("pt-PT") == "pt-BR")
        #expect(Languages.match("in-ID") == "id")
        #expect(Languages.match("sv-SE") == nil)
        #expect(Languages.suggested(["sv-SE", "ko-KR"]) == "ko")
        #expect(Languages.suggested(["sv-SE"]) == "en")
        #expect(Languages.all.count == 20)
        #expect(Languages.override(Languages.system) == nil)
        #expect(Languages.override("de") == "de")
        #expect(Languages.isKnown("system"))
        #expect(!Languages.isKnown("xx"))
    }

    @Test func aPickTellsTheServerTheLanguageTheAppShows() {
        #expect(Languages.forServer("ja", deviceTags: ["ko-KR"]) == "ja")
        #expect(Languages.forServer("zh-Hant", deviceTags: []) == "zh-Hant")
        #expect(Languages.forServer(Languages.system, deviceTags: ["sv-SE", "ko-KR"]) == "ko")
        #expect(Languages.forServer(Languages.system, deviceTags: ["pt-PT"]) == "pt-BR")
        #expect(Languages.forServer(Languages.system, deviceTags: ["sv-SE"]) == "en")
        #expect(Languages.forServer("xx", deviceTags: []) == "en")
    }
}

@Suite struct NavigationTests {
    @Test func tabTaps() {
        #expect(TabTap.of(current: .chat, depth: 0, tapped: .my) == .switchTo)
        #expect(TabTap.of(current: .connectors, depth: 1, tapped: .connectors) == .toRoot)
        #expect(TabTap.of(current: .connectors, depth: 0, tapped: .connectors) == .reset)
    }

    @Test func openTargetsRoundTrip() {
        let all: [OpenTarget] = [.serverUpdate, .operatorPage, .modelSettings(""), .modelSettings("antigravity-oauth"), .chat, .chatAt(7), .wallet, .browserTab("t1"), .ownTab, .work(.task(3)), .work(.approvals),
                                 .work(.schedule(9)), .work(.activity), .work(.schedules), .work(.watchers)]
        for t in all { #expect(OpenTarget.decode(t.encoded) == t) }
        #expect(OpenTarget.decode("nonsense") == nil)
        #expect(OpenTarget.decode("task:x") == nil)
    }

    @MainActor @Test func routerHidesChromeOnSubpages() {
        let r = Router()
        #expect(!r.barHidden)
        r.hideChrome(Router.keyboardAsker, true)
        #expect(r.barHidden && !r.headerHidden)
        r.hideChrome(Router.keyboardAsker, false)
        r.paths[.my] = {
            var p = NavigationPathBox.make(); p.append("x"); return p
        }()
        r.go(.my)
        #expect(r.headerHidden && r.barHidden)
        r.tap(.my)
        #expect(r.depth(.my) == 0)
        r.tap(.my)
        #expect(r.reselects[.my] == 1)
        r.openConnector("github")
        #expect(r.tab == .connectors && r.connectorOpen == "github")
        r.open(.work(.approvals))
        #expect(r.workOpen && r.workStart == .approvals)
    }
}

import SwiftUI
enum NavigationPathBox { static func make() -> NavigationPath { NavigationPath() } }

@Suite struct ComponentRulesTests {
    @Test func tokenText() {
        #expect(TokenText.middle("abcdefghij", max: 5) == "ab…ij")
        #expect(TokenText.fit("short words stay") == "short words stay")
        let long = String(repeating: "x", count: 40)
        #expect(TokenText.fit("model \(long)").count == "model ".count + TokenText.rowMax)
        #expect(TokenText.isToken("claude-opus"))
        #expect(!TokenText.isToken("two words"))
    }

    @Test func profileRules() {
        let p = Profile(agentName: "Kelo")
        #expect(ProfileRules.afterFailure(.ready(p), unauthorized: true) == .signedOut)
        #expect(ProfileRules.afterFailure(.ready(p), unauthorized: false) == .ready(p))
        #expect(ProfileRules.afterFailure(.loading, unauthorized: false) == .failed)
        #expect(!ProfileRules.needsModel(Profile()))
        #expect(ProfileRules.needsModel(Profile(modelReady: false)))
    }
}

@Suite struct ScreenCacheTests {
    @Test func keepsPerServerAndEncrypts() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-cache-\(UUID().uuidString)")
        let key = SymmetricKey(size: .bits256)
        let a = ScreenCache(directory: dir, key: { key })
        a.use(server: "https://one.example")
        a.put("profile", Profile(agentName: "Secret-Name"))
        // a fresh instance reads it back from disk
        let b = ScreenCache(directory: dir, key: { key })
        b.use(server: "https://one.example")
        #expect(b.get("profile", as: Profile.self)?.agentName == "Secret-Name")
        // another server sees nothing
        b.use(server: "https://two.example")
        #expect(b.get("profile", as: Profile.self) == nil)
        // the file on disk is not plain text
        for f in try FileManager.default.contentsOfDirectory(atPath: dir.path) {
            let data = try Data(contentsOf: dir.appendingPathComponent(f))
            #expect(String(decoding: data, as: UTF8.self).contains("Secret-Name") == false)
        }
        // a different key cannot read it
        let c = ScreenCache(directory: dir, key: { SymmetricKey(size: .bits256) })
        c.use(server: "https://one.example")
        #expect(c.get("profile", as: Profile.self) == nil)
        try? FileManager.default.removeItem(at: dir)
    }
}

@Suite struct ActivityModelTests {
    @MainActor @Test func workChangedStartsAtZero() {
        let m = AgentActivityModel()
        #expect(m.workChanged == 0)
    }

    @MainActor @Test func moodFollowsTheReply() {
        let m = AgentActivityModel()
        m.night = false
        #expect(m.mood() == .idle)
        m.offline = true
        #expect(m.mood() == .offline)
        m.offline = false
        m.startFollowing()
        #expect(m.following)
        m.stopFollowing(finished: true)
        #expect(m.mood() == .done)
        #expect(m.mood(now: ProcessInfo.processInfo.systemUptime + 5) == .idle)
    }

    @MainActor @Test func atNightItSleepsOnlyAfterAQuietWhileAndWakesOnTyping() {
        let m = AgentActivityModel()
        m.night = true
        let t0 = 1_000.0
        m.touch(now: t0)                                   // the reply ended
        let quiet = Double(AgentActivityRules.sleepAfterIdleMs) / 1000
        #expect(m.mood(now: t0 + 2) == .idle)              // the cheer is over: rest, not sleep
        #expect(m.mood(now: t0 + quiet - 1) == .idle)      // 4:59
        #expect(m.mood(now: t0 + quiet) == .sleeping)      // 5:00
        m.touch(now: t0 + quiet + 10)                      // a key typed
        #expect(m.mood(now: t0 + quiet + 10) == .idle)
        m.touch(now: t0 + quiet + 10.5)                    // within a second: no change
        #expect(m.activeAt == t0 + quiet + 10)
        m.night = false
        #expect(m.mood(now: t0 + 100 * quiet) == .idle)    // daytime never sleeps
        m.reset()
        #expect(m.activeAt == nil && m.sleepHours == nil)
    }
}

/// No language step on first launch: a new install follows the phone; a pick kept from an earlier version (or 마이 → 언어) stays.
@Suite struct StartLanguageTests {
    @MainActor private func app(saved: String?) -> AppModel {
        let defaults = UserDefaults(suiteName: "kelo-lang-test-\(UUID().uuidString)")!
        let prefs = Prefs(defaults: defaults)
        prefs.language = saved
        return AppModel(store: MemorySessionStore(), prefs: prefs, strings: Strings(code: "en"),
                        cache: ScreenCache(directory: nil, key: { nil }))
    }

    @MainActor @Test func aNewInstallStartsInThePhonesLanguageWithNothingSaved() {
        let a = app(saved: nil)
        #expect(a.languageChoice == nil)
        #expect(a.prefs.language == nil)
        #expect(a.strings.code == Languages.suggested(Locale.preferredLanguages))
    }

    @MainActor @Test func aKeptPickStillWins() {
        let a = app(saved: "ko")
        #expect(a.languageChoice == "ko")
        #expect(a.strings.code == "ko")
        a.chooseLanguage(Languages.system)
        #expect(a.prefs.language == Languages.system)
        #expect(a.strings.code == Languages.suggested(Locale.preferredLanguages))
    }
}
