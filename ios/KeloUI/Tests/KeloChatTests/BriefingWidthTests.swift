#if os(macOS)
import Testing
import SwiftUI
import AppKit
import KeloCore
import KeloShared
@testable import KeloChat

/// The smarter briefing card drawn at 320 / 375 / 414 pt (the one simulator is 402 pt wide): with a long text and spaceless long
/// labels it must keep to the width and grow in height, never wider. KELO_BRIEFING_SHOTS=<folder> also writes the PNGs
/// (briefing-smart-<ko|en>-<width>[-long].png); this is the macOS rendering of the real view, not a simulator screenshot.
@MainActor @Suite struct BriefingWidthTests {
    private static func card(_ lang: String, long: Bool) throws -> BriefingCard {
        let ko = lang == "ko"
        func w(_ en: String, _ k: String) -> String { ko ? k : en }
        let spaceless = long ? w("Donaudampfschifffahrtsgesellschaftskapitaensbesprechung", "팀주간회의및분기계획공유세션입니다만길게이어지는이름") : w("Design review", "디자인 리뷰")
        let headline = long
            ? w("Rain from 3 PM, and with the weekly meeting and the dentist so close together there is hardly any travel time: the day looks tight, so it is worth a look at the order of things.",
                "오늘은 오후 3시부터 비가 오고, 3층 회의실 주간 회의와 치과 예약 사이에 이동 시간이 거의 없어서 하루가 빠듯해 보이니 일정 순서를 한번 확인해 보시면 좋겠어요.")
            : w("Rain this afternoon, take an umbrella. Don't forget the dentist at 3.", "오후에 비가 오니 우산 챙기세요. 3시 치과 잊지 마세요.")
        let note1 = long
            ? w("The dentist visit lands right when the rain starts, and the flight question from last night is still waiting for your answer.",
                "치과 예약 시간에 비가 시작되는데, 어제 보낸 항공권 질문도 아직 답을 기다리고 있어서 우산과 함께 한꺼번에 챙기는 게 좋아 보여요.")
            : w("Rain starts around the dentist visit: take an umbrella.", "치과 예약 시간쯤 비가 시작돼요. 우산을 챙기세요.")
        let step1 = long
            ? w("Shall I draft a reply to Kim about the contract review request right now, or collect everything for this evening?",
                "김 팀장님께 계약서 검토 요청에 대한 답장 초안을 지금 바로 써 드릴까요, 아니면 저녁에 한꺼번에 정리해 드릴까요?")
            : w("Shall I draft a reply to Kim about the contract?", "김 팀장님께 계약서 검토 답장 초안을 써 드릴까요?")
        let json: [String: Any] = [
            "day": "2026-11-10", "summary": headline, "headline": headline, "line": headline,
            "place": ["name": w("Seoul", "서울")],
            "weather": ["id": "w", "high": 11, "low": 4, "headline": w("Cloudy · high 11° · low 4°", "구름 조금 · 최고 11° · 최저 4°"),
                        "notes": [["kind": "rain_start", "text": w("Rain from 3 PM", "15:00부터 비")]],
                        "attribution": "Open-Meteo.com", "attribution_url": "https://open-meteo.com"],
            "events": [["id": "e1", "title": spaceless, "start": 1_762_736_400, "where": w("Room 3F", "3층 회의실"), "open": ["kind": "event", "start": 1_762_736_400]],
                       ["id": "e2", "title": w("Dentist", "치과"), "start": 1_762_754_400, "open": ["kind": "event", "start": 1_762_754_400]]],
            "missed": [["id": "m1", "kind": "followup", "title": w("Flight question", "항공권 질문"), "detail": w("Waiting since last night", "어젯밤부터 기다리는 중")],
                       ["id": "m2", "kind": "mail", "title": w("Contract review request", "계약서 검토 요청"), "source": "Gmail", "open": ["kind": "mail", "subject": "x"]]],
            "connections": [["text": note1, "refs": ["w", "e2", "m1"]],
                            ["text": w("The contract mail and the exercise goal are both due by tomorrow.", "계약서 검토 메일과 운동 목표가 모두 내일까지예요."), "refs": ["m2", "e1"]]],
            "suggestions": [["text": step1, "refs": ["m2"]],
                            ["text": w("Shall I remind you about the umbrella before the dentist?", "치과 가기 전에 우산 챙기라고 알려 드릴까요?"), "refs": []]],
            "hours": ["id": "h1", "day_type": "weekend", "wake_start": "10:00", "wake_end": "23:30"],
        ]
        let data = try JSONSerialization.data(withJSONObject: json)
        return try JSON.decode(BriefingCard.self, from: try JSON.parse(String(decoding: data, as: UTF8.self)))
    }

    @Test func theCardKeepsToTheWidth() throws {
        for lang in ["en", "ko"] { for long in [false, true] { for width in [320.0, 375.0, 414.0] {
            try draw(lang: lang, long: long, width: width)
        } } }
    }

    private func draw(lang: String, long: Bool, width: Double) throws {
        let saved = Strings.shared.code
        Strings.shared.use(lang)
        defer { Strings.shared.use(saved) }
        let card = try Self.card(lang, long: long)
        #expect(BriefingRules.sections(card) == ["headline", "notes", "weather", "today", "missed", "steps", "hours"])
        let view = BriefingCardView(card: card, onTap: { _ in }, onHours: { _, _ in })
            .keloTheme()
            .padding(.horizontal, 16)
            .frame(width: width)
            .padding(.vertical, 12)
            .background(Color(white: 0.96))
            .environment(\.colorScheme, .light)
        let r = ImageRenderer(content: view)
        r.scale = 2
        let cg = try #require(r.cgImage)
        #expect(cg.width == Int(width * 2))   // never wider than the screen
        #expect(cg.height > 300)
        if let dir = ProcessInfo.processInfo.environment["KELO_BRIEFING_SHOTS"] {
            try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            let png = try #require(NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]))
            try png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("briefing-smart-\(lang)-\(Int(width))\(long ? "-long" : "").png"))
        }
    }
}
#endif
