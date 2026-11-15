import Testing
import Foundation
import KeloCore
@testable import KeloChat

@Suite struct ComparisonCardTests {
    @Test func aStoredMessageCarriesItsComparisonCard() throws {
        let json = """
        {"id": 10, "role": "assistant", "content": "여기 요금 비교입니다.", "created_at": 1730000000,
         "comparison": {
             "title": "인터넷 + TV 요금 비교",
             "basis": "월 요금 기준 · 3년 약정",
             "rows": ["월 요금", "사은품", "실체감가"],
             "options": [
                 {"name": "KT", "values": ["38,500원", "45만원", "26,000원"], "says": "KT로 진행해줘", "recommended": true},
                 {"name": "SKB", "values": ["37,400원", "40만원", "26,280원"], "says": "", "recommended": false}
             ],
             "note": "결합 할인 여부를 먼저 확인하세요.",
             "open": true
         }}
        """
        let m = try JSON.decode(ChatMessage.self, from: try JSON.parse(json))
        let card = try #require(m.comparison)
        #expect(card.title == "인터넷 + TV 요금 비교")
        #expect(card.basis == "월 요금 기준 · 3년 약정")
        #expect(card.rows == ["월 요금", "사은품", "실체감가"])
        #expect(card.options.count == 2)
        #expect(card.options[0].name == "KT")
        #expect(card.options[0].recommended == true)
        #expect(card.options[0].says == "KT로 진행해줘")
        #expect(card.options[1].name == "SKB")
        #expect(card.options[1].recommended == false)
        #expect(card.note == "결합 할인 여부를 먼저 확인하세요.")
        #expect(card.open == true)

        if case .msg(let row) = ChatReducer.item(m) {
            #expect(row.comparison == card)
        } else {
            Issue.record("not a message row")
        }
    }

    @Test func comparisonRulesChoiceUsesFallbackWhenSaysIsBlank() {
        let withSays = ComparisonOption(name: "Option A", values: ["10"], says: "I choose Option A", recommended: true)
        #expect(ComparisonRules.choice(withSays) { "Fallback for \($0)" } == "I choose Option A")

        let blankSays = ComparisonOption(name: "Option B", values: ["20"], says: "", recommended: false)
        #expect(ComparisonRules.choice(blankSays) { "Fallback for \($0)" } == "Fallback for Option B")

        let whitespaceSays = ComparisonOption(name: "Option C", values: ["30"], says: "   ", recommended: false)
        #expect(ComparisonRules.choice(whitespaceSays) { "Let's go with \($0)" } == "Let's go with Option C")
    }
}
