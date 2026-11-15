import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloChat

@Suite struct ModelRulesTests {
    @Test func namesAreTidiedNeverRaw() {
        #expect(ModelNames.display("claude-sonnet-5-5", label: "Claude Sonnet 5.5") == "Claude Sonnet 5.5")
        #expect(ModelNames.display("claude-sonnet-5-5", label: nil) == "Claude Sonnet 5.5")
        #expect(ModelNames.display("claude-sonnet-5-5", label: "claude-sonnet-5-5") == "Claude Sonnet 5.5")
        #expect(ModelNames.humanize("anthropic/claude-3-5-haiku-20241022") == "Claude 3.5 Haiku")
        #expect(ModelNames.humanize("gpt-5.1-mini") == "GPT-5.1 Mini")
        #expect(ModelNames.humanize("gpt-4o-2024-08-06") == "GPT-4o")
        #expect(ModelNames.humanize("models/gemini-3.8-flash") == "Gemini 3.8 Flash")
        #expect(ModelNames.humanize("qwen3:8b") == "Qwen3 8B")
        #expect(ModelNames.humanize("gpt-oss-120b") == "GPT-OSS 120B")
        #expect(ModelNames.humanize("o3") == "o3")
        #expect(ModelNames.humanize("meta-llama/llama-3.1-405b") == "Llama 3.1 405B")
        #expect(ModelNames.short("Claude Sonnet 5.5") == "Sonnet 5.5")
        #expect(ModelNames.short("Claude") == "Claude")
        let ids = ["gpt-4o", "gpt-4o-2024-08-06", "gpt-4o-20240513", "claude-sonnet-5-5"]
        let names = ModelNames.displayAll(ids, labels: ["claude-sonnet-5-5": "Claude Sonnet 5.5"])
        #expect(names["gpt-4o"] == "GPT-4o")
        #expect(names["gpt-4o-2024-08-06"] == "GPT-4o (2024-08-06)")
        #expect(names["gpt-4o-20240513"] == "GPT-4o (2024-05-13)")
        #expect(names["claude-sonnet-5-5"] == "Claude Sonnet 5.5")
    }

    @Test func traitsComeFromTierWordsAndFactsOnly() {
        #expect(ModelTraits.of("claude-haiku-5-5", facts: ModelEffort(images: true, context: 1_000_000)) == [.fast, .images, .long])
        #expect(ModelTraits.of("gemini-3.8-pro", facts: ModelEffort(images: true, context: 200_000)) == [.large, .images])
        #expect(ModelTraits.of("deepseek/deepseek-r1", facts: nil) == [.reasoning])
        #expect(ModelTraits.of("gemini-3.8-flash-pro", facts: nil) == [.fast])
        #expect(ModelTraits.of("llama3.2", facts: ModelEffort(images: false, context: 131_072)).isEmpty)
        #expect(ModelTraits.of("gemini-3", facts: nil).isEmpty)
    }

    @Test func effortAndSections() {
        #expect(EffortRules.effort(ModelProvider(name: "a", effortLevels: ["low", "medium", "high"])) == .levels(["", "low", "medium", "high"]))
        #expect(EffortRules.effort(ModelProvider(name: "agy", effortNote: "set by the name")) == .fromModel("set by the name"))
        #expect(EffortRules.effort(ModelProvider(name: "anthropic")) == .none)
        #expect(EffortRules.effort(nil) == .none)
        // the levels the provider's model list states for the chosen model win; nil = it does not say
        let codex = ModelProvider(name: "openai-codex", effortLevels: ["low", "medium", "high"])
        #expect(EffortRules.effort(codex, facts: ModelEffort(effortLevels: ["low", "medium", "high", "xhigh"])) == .levels(["", "low", "medium", "high", "xhigh"]))
        #expect(EffortRules.effort(codex, facts: ModelEffort(effortLevels: [])) == .none)
        #expect(EffortRules.effort(codex, facts: ModelEffort()) == .levels(["", "low", "medium", "high"]))
        #expect(EffortRules.effort(ModelProvider(name: "anthropic"), facts: ModelEffort(effortLevels: ["low", "max"])) == .levels(["", "low", "max"]))
        let providers = [
            ModelProvider(name: "claude-subscription", displayName: "Claude", configured: true, unofficial: true),
            ModelProvider(name: "openrouter", displayName: "OpenRouter", configured: true),
            ModelProvider(name: "anthropic", displayName: "Anthropic", configured: true),
            ModelProvider(name: "gemini", displayName: "Gemini", configured: false),
            ModelProvider(name: "custom", displayName: "Custom", configured: nil),
        ]
        #expect(ModelChooserRules.sections(current: "openrouter", providers: providers) == ["openrouter", "anthropic", "claude-subscription"])
        #expect(ModelChooserRules.sections(current: "gemini", providers: providers) == ["gemini", "anthropic", "openrouter", "claude-subscription"])
        #expect(ModelChooserRules.sections(current: "", providers: providers) == ["anthropic", "openrouter", "claude-subscription"])
        let list = ModelList(provider: "p", models: ["a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: list, current: ModelChoice("p", "mine")) == ["mine", "a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: list, current: ModelChoice("p", "a")) == ["a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: list, current: ModelChoice("q", "mine")) == ["a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: nil, current: ModelChoice("p", "mine")) == ["mine"])
    }

    @Test func fallbackMustBeAnotherProvider() {
        #expect(ModelRules.initialFallback(Profile(provider: "a", fallbackProvider: "a", fallbackModel: "x")) == .none)
        #expect(ModelRules.initialFallback(Profile(provider: "a", fallbackProvider: "b", fallbackModel: "x")) == ModelChoice("b", "x"))
        let (fb, cleared) = ModelRules.afterMainChange(ModelChoice("b", "m"), ModelChoice("b", "x"))
        #expect(fb == .none && cleared)
        #expect(ModelRules.afterMainChange(ModelChoice("a", "m"), ModelChoice("b", "x")).1 == false)
        #expect(!ModelRules.complete(ModelChoice("a", " ")))
        #expect(ModelRules.complete(.none))
    }

    @Test func consentWordsAndRules() {
        #expect(ProviderConsentRules.needs(ModelProvider(name: "q", unofficial: true, consentVersion: 2)))
        #expect(!ProviderConsentRules.needs(ModelProvider(name: "q", unofficial: true, consent: ProviderConsentRecord(acceptedAt: 1, version: 2), consentVersion: 2)))
        #expect(ProviderConsentRules.needs(ModelProvider(name: "q", unofficial: true, consent: ProviderConsentRecord(acceptedAt: 1, version: 1), consentVersion: 2)))
        #expect(!ProviderConsentRules.needs(ModelProvider(name: "anthropic", consentVersion: 2)))
        #expect(ProviderConsentRules.service("claude-subscription") == .claudeSubscription)
        #expect(ProviderConsentRules.service("openai-codex") == .chatgptSubscription)
        #expect(ProviderConsentRules.subjectParticle("Anthropic") == "이")
        #expect(ProviderConsentRules.subjectParticle("OpenAI") == "가")
        #expect(ProviderConsentRules.subjectParticle("카카오") == "가")
        #expect(ProviderConsentRules.subjectParticle("네이버") == "가")
        #expect(ProviderConsentRules.subjectParticle("구글") == "이")
        #expect(AskWords.objectParticle("사이트 열기") == "를")
        #expect(AskWords.objectParticle("로그인 정보 입력") == "을")
        #expect(AskWords.objectParticle("Gmail") == "을")
        #expect(AskWords.askWord("Adding an event", language: "en") == "adding an event")
        #expect(AskWords.askWord("GitHub", language: "en") == "GitHub")
        #expect(AskWords.askWord("Adding an event", language: "ko") == "Adding an event")
        #expect(ProviderConsentRules.requiredBy(ApiError(status: 428, detail: "consent_required:qwen")) == "qwen")
        #expect(ProviderConsentRules.requiredBy(ApiError(status: 422, detail: "consent_required:qwen")) == nil)
    }

    @Test func phoneSignInOnlyWhenTheServerOffersIt() {
        #expect(SignInRules.offered(signin: true, signedIn: false))
        #expect(SignInRules.offered(signin: true, signedIn: nil))
        #expect(!SignInRules.offered(signin: true, signedIn: true))
        #expect(!SignInRules.offered(signin: false, signedIn: false))   // a server that does not enable it never shows it
        #expect(SignInRules.pasteReady(" abc "))
        #expect(!SignInRules.pasteReady("a b"))
        #expect(!SignInRules.pasteReady(""))
        #expect(SignInRules.problem(ApiError(status: 428, detail: "")) == .consent)
        #expect(SignInRules.problem(ApiError(status: 409, detail: "already_signed_in: yes")) == .already)
        #expect(SignInRules.problem(ApiError(status: 503, detail: "")) == .off)
        #expect(SignInRules.readable(ApiError(status: 409, detail: "already_signed_in: You are in")) == "You are in")
        #expect(SignInRules.readable(ApiError(status: 0, detail: "x")) == "")
        #expect(SignInRules.isDeviceCode(SignInSession(kind: "device_code")))
        #expect(SignInRules.ended(SignInSession(state: "expired")))
    }

    @Test func signOutOutcomes() {
        #expect(SignOutRules.outcome(nil, ApiError(status: 409, detail: "")) == .mainModel)
        #expect(SignOutRules.outcome(nil, ApiError(status: 404, detail: "")) == .done)
        #expect(SignOutRules.outcome(nil, ApiError(status: 500, detail: "")) == .failed)
        #expect(SignOutRules.outcome(SignOutResult(stillSignedIn: true), nil) == .doneEnvLeft)
        #expect(SignOutRules.outcome(SignOutResult(fallbackCleared: true), nil) == .doneFallbackCleared)
        #expect(SignOutRules.done(.doneEnvLeft) && !SignOutRules.done(.mainModel))
        #expect(SignOutRules.isMain("a", mainProvider: "a") && !SignOutRules.isMain("a", mainProvider: ""))
    }

    @Test func modelsDecodeLeniently() throws {
        let m = try KeloApi.decode(ChatMessage.self, try JSON.parse(#"{"id": "7", "role": "assistant", "content": "hi", "replies": {"questions": [{"question": "Q", "options": ["a", 2]}], "goal_id": 3}}"#))
        #expect(m.id == 7)
        #expect(m.replies?.questions.first?.options == ["a", "2"])
        #expect(m.replies?.goalId == 3)
        let list = ChatMessage.parseList(try JSON.parse(#"[{"id": 1, "content": "a"}, {"content": "no id"}]"#))
        #expect(list.count == 1)
        let p = ModelProvider.list(try JSON.parse(#"[{"name": "claude-subscription", "signin": true, "signed_in": false, "unofficial": true, "consent": {"version": 1}, "consent_version": 2}]"#))
        #expect(p.first?.signin == true && p.first?.signedIn == false && p.first?.consent?.version == 1)
        let ml = try KeloApi.decode(ModelList.self, try JSON.parse(#"{"provider": "p", "models": ["a"], "labels": {"a": "A"}, "info": {"a": {"images": true, "context": 1000000}}}"#))
        #expect(ml.labels["a"] == "A" && ml.info["a"]?.images == true && ml.info["a"]?.context == 1_000_000)
        #expect(ml.stale == false && ml.info["a"]?.effortLevels == nil)
        let old = try KeloApi.decode(ModelList.self, try JSON.parse(#"{"provider": "p", "models": ["a"], "info": {"a": {"effort_levels": []}}, "stale": true, "fetched_at": 1700000000}"#))
        #expect(old.stale && old.info["a"]?.effortLevels == [])
        let trimmed = ChatHistory.trim((1...250).map { ChatMessage(id: Int64($0), content: "x") })
        #expect(trimmed.count == 200 && trimmed.first?.id == 51)
    }

    @Test func modelListFoldsLikeTheLevel() {
        #expect(ModelChooserRules.listStartsOpen(currentModel: "", sections: ["a"]))
        #expect(!ModelChooserRules.listStartsOpen(currentModel: "m", sections: ["a"]))
        #expect(!ModelChooserRules.listStartsOpen(currentModel: "", sections: []))
        #expect(!ModelChooserRules.closeAfterPick(.levels(["", "low"])))
        #expect(ModelChooserRules.closeAfterPick(.fromModel("n")))
        #expect(ModelChooserRules.closeAfterPick(.none))
        // one open at a time
        #expect(ModelChooserRules.toggle(listOpen: false, levelsOpen: true, .list) == (true, false))
        #expect(ModelChooserRules.toggle(listOpen: true, levelsOpen: false, .list) == (false, false))
        #expect(ModelChooserRules.toggle(listOpen: true, levelsOpen: false, .levels) == (false, true))
        #expect(ModelChooserRules.toggle(listOpen: false, levelsOpen: true, .levels) == (false, false))
    }

    @Test func foldingAnimatesUnlessMotionIsReduced() {
        #expect(ModelChooserRules.chevronTurn(false) == 0)
        #expect(ModelChooserRules.chevronTurn(true) == 90)
        // the grow / shrink of the folding lists: animated normally, instant with Reduce Motion
        #expect(KeloMotionSpec.standard(false) != nil)
        #expect(KeloMotionSpec.standard(true) == nil)
    }

    @Test func aCustomServerHasItsLevelsUntilItRefusesThem() throws {
        let custom = ModelProvider(name: "custom", effortLevels: ["low", "medium", "high", "max"])
        #expect(EffortRules.effort(custom, facts: ModelEffort()) == .levels(["", "low", "medium", "high", "max"]))
        let refused = EffortRules.effort(custom, facts: ModelEffort(effortLevels: [], effortNote: "이 서버는 생각 수준 설정을 받지 않아요"))
        #expect(refused == .refused("이 서버는 생각 수준 설정을 받지 않아요"))
        #expect(EffortRules.effortNote(refused) == "이 서버는 생각 수준 설정을 받지 않아요")
        #expect(EffortRules.effortNote(.fromModel("set by the name")) == "set by the name")
        #expect(EffortRules.effortNote(.levels(["", "low"])) == nil)
        #expect(ModelChooserRules.closeAfterPick(refused))
        let facts = try JSONDecoder().decode(ModelEffort.self, from: Data(#"{"images":false,"effort_levels":[],"effort_note":"no"}"#.utf8))
        #expect(EffortRules.effort(custom, facts: facts) == .refused("no"))
    }

    @Test func theShownLevelIsWhatTheServerSends() {
        let custom = ["", "low", "medium", "high", "max"]
        #expect(EffortRules.shownLevel(custom, "max") == "max")
        #expect(EffortRules.shownLevel(custom, "") == "")
        #expect(EffortRules.shownLevel(custom, "xhigh") == "high")   // no xhigh here: the next lower one, like effort.clamp
        #expect(EffortRules.shownLevel(["", "low", "medium", "high"], "max") == "high")
        #expect(EffortRules.shownLevel(custom, "turbo") == "")
    }

    @Test func aLongThinkIsSaidAfterAMinute() {
        let t = Phase(.thinking, since: 1)
        #expect(!ChatReducer.longThinking(t, now: 1 + ChatReducer.longThinkingSeconds - 0.5))
        #expect(ChatReducer.longThinking(t, now: 1 + ChatReducer.longThinkingSeconds))
        #expect(!ChatReducer.longThinking(Phase(.writing, since: 0), now: 600))
        #expect(ChatReducer.phase(t, .thinking, now: 90) == t)   // more reasoning keeps the phase and its start
    }
}
