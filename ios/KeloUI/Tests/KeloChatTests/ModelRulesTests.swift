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
        #expect(ModelTraits.of("claude-haiku-5-5", facts: ModelFacts(images: true, context: 1_000_000)) == [.fast, .images, .long])
        #expect(ModelTraits.of("gemini-3.8-pro", facts: ModelFacts(images: true, context: 200_000)) == [.large, .images])
        #expect(ModelTraits.of("deepseek/deepseek-r1", facts: nil) == [.reasoning])
        #expect(ModelTraits.of("gemini-3.8-flash-pro", facts: nil) == [.fast])
        #expect(ModelTraits.of("llama3.2", facts: ModelFacts(images: false, context: 131_072)).isEmpty)
        #expect(ModelTraits.of("gemini-3", facts: nil).isEmpty)
    }

    @Test func effortAndSections() {
        #expect(ModelChooserRules.effort(ChatProvider(name: "a", effortLevels: ["low", "medium", "high"])) == .levels(["", "low", "medium", "high"]))
        #expect(ModelChooserRules.effort(ChatProvider(name: "agy", effortNote: "set by the name")) == .fromModel("set by the name"))
        #expect(ModelChooserRules.effort(ChatProvider(name: "anthropic")) == .none)
        #expect(ModelChooserRules.effort(nil) == .none)
        // the levels the provider's model list states for the chosen model win; nil = it does not say
        let codex = ChatProvider(name: "openai-codex", effortLevels: ["low", "medium", "high"])
        #expect(ModelChooserRules.effort(codex, facts: ModelFacts(effortLevels: ["low", "medium", "high", "xhigh"])) == .levels(["", "low", "medium", "high", "xhigh"]))
        #expect(ModelChooserRules.effort(codex, facts: ModelFacts(effortLevels: [])) == .none)
        #expect(ModelChooserRules.effort(codex, facts: ModelFacts()) == .levels(["", "low", "medium", "high"]))
        #expect(ModelChooserRules.effort(ChatProvider(name: "anthropic"), facts: ModelFacts(effortLevels: ["low", "max"])) == .levels(["", "low", "max"]))
        let providers = [
            ChatProvider(name: "claude-subscription", displayName: "Claude", configured: true, unofficial: true),
            ChatProvider(name: "openrouter", displayName: "OpenRouter", configured: true),
            ChatProvider(name: "anthropic", displayName: "Anthropic", configured: true),
            ChatProvider(name: "gemini", displayName: "Gemini", configured: false),
            ChatProvider(name: "custom", displayName: "Custom", configured: nil),
        ]
        #expect(ModelChooserRules.sections(current: "openrouter", providers: providers) == ["openrouter", "anthropic", "claude-subscription"])
        #expect(ModelChooserRules.sections(current: "gemini", providers: providers) == ["gemini", "anthropic", "openrouter", "claude-subscription"])
        #expect(ModelChooserRules.sections(current: "", providers: providers) == ["anthropic", "openrouter", "claude-subscription"])
        let list = ChatModelList(provider: "p", models: ["a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: list, current: ModelChoice("p", "mine")) == ["mine", "a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: list, current: ModelChoice("p", "a")) == ["a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: list, current: ModelChoice("q", "mine")) == ["a", "b"])
        #expect(ModelChooserRules.rows(provider: "p", list: nil, current: ModelChoice("p", "mine")) == ["mine"])
    }

    @Test func fallbackMustBeAnotherProvider() {
        #expect(ModelSheetRules.initialFallback(Profile(provider: "a", fallbackProvider: "a", fallbackModel: "x")) == .none)
        #expect(ModelSheetRules.initialFallback(Profile(provider: "a", fallbackProvider: "b", fallbackModel: "x")) == ModelChoice("b", "x"))
        let (fb, cleared) = ModelSheetRules.afterMainChange(ModelChoice("b", "m"), ModelChoice("b", "x"))
        #expect(fb == .none && cleared)
        #expect(ModelSheetRules.afterMainChange(ModelChoice("a", "m"), ModelChoice("b", "x")).1 == false)
        #expect(!ModelSheetRules.complete(ModelChoice("a", " ")))
        #expect(ModelSheetRules.complete(.none))
    }

    @Test func consentWordsAndRules() {
        #expect(ProviderConsentRules.needs(ChatProvider(name: "q", unofficial: true, consentVersion: 2)))
        #expect(!ProviderConsentRules.needs(ChatProvider(name: "q", unofficial: true, consent: ProviderConsentRecord(acceptedAt: 1, version: 2), consentVersion: 2)))
        #expect(ProviderConsentRules.needs(ChatProvider(name: "q", unofficial: true, consent: ProviderConsentRecord(acceptedAt: 1, version: 1), consentVersion: 2)))
        #expect(!ProviderConsentRules.needs(ChatProvider(name: "anthropic", consentVersion: 2)))
        #expect(ProviderConsentRules.service("claude-subscription") == .claudeSubscription)
        #expect(ProviderConsentRules.service("openai-codex") == .chatgptSubscription)
        #expect(ProviderConsentRules.subjectParticle("Anthropic") == "이")
        #expect(ProviderConsentRules.subjectParticle("OpenAI") == "가")
        #expect(ProviderConsentRules.subjectParticle("카카오") == "가")
        #expect(ProviderConsentRules.subjectParticle("네이버") == "가")
        #expect(ProviderConsentRules.subjectParticle("구글") == "이")
        #expect(ProviderConsentRules.objectParticle("사이트 열기") == "를")
        #expect(ProviderConsentRules.objectParticle("로그인 정보 입력") == "을")
        #expect(ProviderConsentRules.objectParticle("Gmail") == "을")
        #expect(ProviderConsentRules.askWord("Adding an event", language: "en") == "adding an event")
        #expect(ProviderConsentRules.askWord("GitHub", language: "en") == "GitHub")
        #expect(ProviderConsentRules.askWord("Adding an event", language: "ko") == "Adding an event")
        #expect(ProviderConsentRules.requiredBy(ApiError(status: 428, detail: "consent_required:qwen")) == "qwen")
        #expect(ProviderConsentRules.requiredBy(ApiError(status: 422, detail: "consent_required:qwen")) == nil)
    }

    @Test func selectRules() {
        let none = SelectOption("", "서버 기본값", keep: true)
        let providers = [
            none,
            SelectOption("zai", "Z.ai (설정 필요)", sortKey: "Z.ai"),
            SelectOption("openai", "OpenAI ✓", rank: 0, sortKey: "OpenAI"),
            SelectOption("anthropic", "Anthropic (설정 필요)", sortKey: "Anthropic"),
            SelectOption("gemini", "Gemini ✓", rank: 0, sortKey: "Gemini"),
            SelectOption("deepseek", "deepseek · 기본 모델로 쓰는 중", enabled: false, sortKey: "deepseek"),
        ]
        #expect(SelectRules.arrange(providers, query: "", sorted: true).map(\.value) == ["", "gemini", "openai", "anthropic", "deepseek", "zai"])
        #expect(SelectRules.arrange(providers, query: "", sorted: false).map(\.value) == providers.map(\.value))
        #expect(SelectRules.arrange(providers, query: " OPEN", sorted: true).map(\.value) == ["", "openai"])
        #expect(SelectRules.arrange(providers, query: "deep", sorted: true).map(\.value) == ["", "deepseek"])
        let nothing = SelectRules.arrange(providers, query: "qqq", sorted: true)
        #expect(nothing.map(\.value) == [""])
        #expect(SelectRules.nothingFound(nothing, query: "qqq"))
        #expect(!SelectRules.useSheet(10))
        #expect(SelectRules.useSheet(11))
        let grouped = [none, SelectOption("q", "Qwen", rank: 0, group: 1), SelectOption("a", "Anthropic", rank: 1)]
        let arranged = SelectRules.arrange(grouped, query: "", sorted: true)
        #expect(arranged.map(\.value) == ["", "a", "q"])
        #expect(SelectRules.dividerBefore(arranged, 2))
        #expect(!SelectRules.dividerBefore(arranged, 1))
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
        let p = ChatProvider.parseList(try JSON.parse(#"[{"name": "claude-subscription", "signin": true, "signed_in": false, "unofficial": true, "consent": {"version": 1}, "consent_version": 2}]"#))
        #expect(p.first?.signin == true && p.first?.signedIn == false && p.first?.consent?.version == 1)
        let ml = try KeloApi.decode(ChatModelList.self, try JSON.parse(#"{"provider": "p", "models": ["a"], "labels": {"a": "A"}, "info": {"a": {"images": true, "context": 1000000}}}"#))
        #expect(ml.labels["a"] == "A" && ml.info["a"]?.images == true && ml.info["a"]?.context == 1_000_000)
        #expect(ml.stale == false && ml.info["a"]?.effortLevels == nil)
        let old = try KeloApi.decode(ChatModelList.self, try JSON.parse(#"{"provider": "p", "models": ["a"], "info": {"a": {"effort_levels": []}}, "stale": true, "fetched_at": 1700000000}"#))
        #expect(old.stale && old.fetchedAt == 1_700_000_000 && old.info["a"]?.effortLevels == [])
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
        #expect(KeloMotionSpec.enter(true) == nil && KeloMotionSpec.exit(true) == nil)
    }

    @Test func aCustomServerHasItsLevelsUntilItRefusesThem() throws {
        let custom = ChatProvider(name: "custom", effortLevels: ["low", "medium", "high", "max"])
        #expect(ModelChooserRules.effort(custom, facts: ModelFacts()) == .levels(["", "low", "medium", "high", "max"]))
        let refused = ModelChooserRules.effort(custom, facts: ModelFacts(effortLevels: [], effortNote: "이 서버는 생각 수준 설정을 받지 않아요"))
        #expect(refused == .refused("이 서버는 생각 수준 설정을 받지 않아요"))
        #expect(ModelChooserRules.effortNote(refused) == "이 서버는 생각 수준 설정을 받지 않아요")
        #expect(ModelChooserRules.effortNote(.fromModel("set by the name")) == "set by the name")
        #expect(ModelChooserRules.effortNote(.levels(["", "low"])) == nil)
        #expect(ModelChooserRules.closeAfterPick(refused))
        let facts = try JSONDecoder().decode(ModelFacts.self, from: Data(#"{"images":false,"effort_levels":[],"effort_note":"no"}"#.utf8))
        #expect(ModelChooserRules.effort(custom, facts: facts) == .refused("no"))
    }

    @Test func theShownLevelIsWhatTheServerSends() {
        let custom = ["", "low", "medium", "high", "max"]
        #expect(ModelChooserRules.shownLevel(custom, "max") == "max")
        #expect(ModelChooserRules.shownLevel(custom, "") == "")
        #expect(ModelChooserRules.shownLevel(custom, "xhigh") == "high")   // no xhigh here: the next lower one, like effort.clamp
        #expect(ModelChooserRules.shownLevel(["", "low", "medium", "high"], "max") == "high")
        #expect(ModelChooserRules.shownLevel(custom, "turbo") == "")
    }

    @Test func aLongThinkIsSaidAfterAMinute() {
        let t = Phase(.thinking, since: 1)
        #expect(!ChatReducer.longThinking(t, now: 1 + ChatReducer.longThinkingSeconds - 0.5))
        #expect(ChatReducer.longThinking(t, now: 1 + ChatReducer.longThinkingSeconds))
        #expect(!ChatReducer.longThinking(Phase(.writing, since: 0), now: 600))
        #expect(ChatReducer.phase(t, .thinking, now: 90) == t)   // more reasoning keeps the phase and its start
    }
}
