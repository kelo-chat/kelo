import Testing
@testable import KeloCore

@Suite struct AiConsentTests {
    @Test func readsThe428Answer() {
        let e = ApiError(status: 428, detail: "x", detailJson: ["code": .string("ai_consent_required"), "company": .string("Google"),
                                                               "privacy_url": .string("https://kelo.chat/privacy")])
        #expect(AiConsent.of(e) == AiConsentNeed(company: "Google", privacyUrl: "https://kelo.chat/privacy"))
    }

    @Test func aMissingCompanyOrUrlStillAsks() {
        let e = ApiError(status: 428, detail: "x", detailJson: ["code": .string("ai_consent_required"), "company": .null])
        #expect(AiConsent.of(e) == AiConsentNeed(company: "", privacyUrl: AiConsent.privacyUrl))
    }

    @Test func otherAnswersAreNotTheQuestion() {
        #expect(AiConsent.of(ApiError(status: 428, detail: "x", detailJson: ["code": .string("consent_required")])) == nil)
        #expect(AiConsent.of(ApiError(status: 409, detail: "x", detailJson: ["code": .string("ai_consent_required")])) == nil)
    }

    @Test func whereChatsGo() {
        #expect(AiConsent.chatsGo(provider: "google", company: "Google") == .to("Google"))
        #expect(AiConsent.chatsGo(provider: "custom", company: " llm.example.com ") == .to("llm.example.com"))
        #expect(AiConsent.chatsGo(provider: "custom", company: nil) == .local)
        #expect(AiConsent.chatsGo(provider: "lmstudio", company: "") == .local)
        #expect(AiConsent.chatsGo(provider: "anthropic", company: nil) == nil)
        #expect(AiConsent.chatsGo(provider: "", company: "Anthropic") == nil)
    }

    @Test func everyTurnAsksTheServerToCheck() {
        #expect(TurnBody.of(text: "a", timezone: "UTC")["ai_consent"] == .string("check"))
    }
}
