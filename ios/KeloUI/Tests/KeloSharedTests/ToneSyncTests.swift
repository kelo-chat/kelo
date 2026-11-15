import Foundation
import Testing
import KeloCore
import KeloTestSupport
@testable import KeloShared

/// 마이 → 에이전트 → 말투: the status poll brings a tone the agent changed in the chat into the shared profile, so the page shows it
/// without a reload (Android MainShell's ToneRules.synced).
@Suite struct ToneSyncTests {
    @MainActor private func app() -> AppModel {
        AppModel(store: MemorySessionStore(), prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-tone-test-\(UUID().uuidString)")!),
                 strings: Strings(code: "en"), cache: ScreenCache(directory: nil, key: { nil }))
    }

    @MainActor @Test func thePolledToneReachesTheProfileOnlyWhenItDiffers() throws {
        let a = app()
        a.setProfile(Profile(agentName: "Kelo", tone: "polite"))
        let hook = try #require(a.activity.onStatus)
        hook(AgentStatus(agentName: "Kelo"))                      // an older server says nothing
        #expect(a.profile?.tone == "polite")
        hook(AgentStatus(tone: "shouty"))                         // unknown: ignored
        #expect(a.profile?.tone == "polite")
        hook(AgentStatus(tone: "friendly"))                       // "반말로 해 줘" in the chat
        #expect(a.profile?.tone == "friendly")
        #expect(a.profile?.agentName == "Kelo")                   // nothing else changes
        a.statusPolled(AgentStatus(tone: "polite"))               // switched back (here or on another device)
        #expect(ToneRules.selected(try #require(a.profile)) == ToneRules.polite)
    }

    @MainActor @Test func noProfileYetNothingHappens() {
        let a = app()
        a.statusPolled(AgentStatus(tone: "friendly"))
        #expect(a.profile == nil)
    }
}
