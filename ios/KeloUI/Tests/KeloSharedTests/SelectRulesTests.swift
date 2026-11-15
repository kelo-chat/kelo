import Testing
@testable import KeloShared

/// Port of android chat/SelectRulesTest.kt: the same vectors.
@Suite struct SelectRulesTests {
    @Test func arrangeSearchAndGroups() {
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
        let grouped = [none, SelectOption("q", "Qwen", rank: 0, group: 1), SelectOption("a", "Anthropic", rank: 1)]
        let arranged = SelectRules.arrange(grouped, query: "", sorted: true)
        #expect(arranged.map(\.value) == ["", "a", "q"])
        #expect(SelectRules.dividerBefore(arranged, 2))
        #expect(!SelectRules.dividerBefore(arranged, 1))
    }

    @Test func aShortListIsAMenuALongOneASheet() {
        #expect(!SelectRules.useSheet(10))
        #expect(SelectRules.useSheet(11))
        #expect(SelectRules.useSheet(40))
    }
}
