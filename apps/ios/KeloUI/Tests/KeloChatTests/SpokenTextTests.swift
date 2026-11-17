import Testing
import Foundation
@testable import KeloChat

@Suite struct SpokenTextTests {
    @Test func marksLinksCodeAndEmojiAreNotSpoken() {
        let md = "# Title\n\nSome **bold** and `code`, see [the docs](https://d.io) or https://x.y now 😀.\n\n- first\n- second item!\n\n```kt\nval x = 1\n```\n\n> quoted"
        #expect(SpokenText.from(md) == "Title.\n\nSome bold and code, see the docs or now.\n\nfirst.\nsecond item!\n\nquoted.")
    }

    @Test func aTableReadsRowByRow() {
        #expect(SpokenText.from("| Name | Age |\n|---|---|\n| Anna | 30 |") == "Name, Age.\nAnna, 30.")
    }

    @Test func messageBreaksBecomePauses() {
        #expect(SpokenText.from("One<message_break/>Two") == "One.\n\nTwo.")
    }

    @Test func onlyMarksGiveNothing() {
        #expect(SpokenText.from("---\n\n```\ncode\n```") == "")
    }

    @Test func theScriptTellsTheLanguage() {
        #expect(SpokenText.language("오늘 일정이 세 개 있어요", fallback: "en-US") == "ko")
        #expect(SpokenText.language("今日の予定は三つあります", fallback: "en-US") == "ja")
        #expect(SpokenText.language("今天有三个日程安排", fallback: "en-US") == "zh")
        #expect(SpokenText.language("Сегодня три встречи", fallback: "en-US") == "ru")
        #expect(SpokenText.language("لديك ثلاثة مواعيد اليوم", fallback: "en-US") == "ar")
        #expect(SpokenText.language("วันนี้มีนัดสามอย่าง", fallback: "en-US") == "th")
        #expect(SpokenText.language("आज तीन बैठकें हैं", fallback: "en-US") == "hi")
    }

    @Test func latinTextTakesTheAppsLanguage() {
        #expect(SpokenText.language("Vous avez trois rendez-vous aujourd'hui.", fallback: "fr-FR") == "fr-FR")
        #expect(SpokenText.language("12:30 – 14:00", fallback: "de-DE") == "de-DE")
    }

    @Test func aFewForeignWordsDoNotChangeTheLanguage() {
        #expect(SpokenText.language("The Korean word is 안녕 and it means hello, a common greeting in Seoul.", fallback: "en-US") == "en-US")
        #expect(SpokenText.language("메일 제목은 Weekly report 예요", fallback: "en-US") == "ko")
    }

    @Test func hanWithKanaIsJapaneseWithHangulKorean() {
        #expect(SpokenText.language("私は東京へ行きます", fallback: "ko-KR") == "ja")
        #expect(SpokenText.language("한국어 大韓民國 입니다", fallback: "en-US") == "ko")
    }

    @Test func ukrainianAndChineseAppLanguagesKeepTheirOwnTag() {
        #expect(SpokenText.language("Сьогодні три зустрічі", fallback: "uk-UA") == "uk-UA")
        #expect(SpokenText.language("今天有三個行程", fallback: "zh-TW") == "zh-TW")
    }

    @Test func piecesStayWithinTheLimitAndCutAtSentenceEnds() {
        #expect(SpokenText.chunks("  Hello there. ") == ["Hello there."])
        #expect(SpokenText.chunks("  \n ").isEmpty)
        let text = String(repeating: "This is a sentence of moderate length. ", count: 200).trimmingCharacters(in: .whitespaces)
        let parts = SpokenText.chunks(text, max: 500)
        #expect(parts.count > 1)
        #expect(parts.allSatisfy { $0.count <= 500 && $0.hasSuffix(".") })
        #expect(parts.joined(separator: " ") == text)
        let solid = SpokenText.chunks(String(repeating: "x", count: 1200), max: 500)
        #expect(solid.allSatisfy { $0.count <= 500 })
        #expect(solid.reduce(0) { $0 + $1.count } == 1200)
    }
}

@Suite struct HoldToTalkTests {
    private func play(_ events: [HoldToTalk.Event]) -> (actions: [HoldToTalk.Action], phase: HoldToTalk.Phase) {
        var phase = HoldToTalk.Phase.idle
        var actions: [HoldToTalk.Action] = []
        for e in events {
            let s = HoldToTalk.step(phase, e)
            phase = s.phase
            if s.action != .none { actions.append(s.action) }
        }
        return (actions, phase)
    }

    @Test func aShortTouchIsATap() {
        #expect(play([.down(listening: false), .up]).actions == [.tap])
        #expect(play([.down(listening: true), .up]).actions == [.tap])   // the tap that stops a listening started by a tap
    }

    @Test func holdingStartsAndLettingGoOnTheButtonFinishes() {
        let r = play([.down(listening: false), .holdElapsed, .up])
        #expect(r.actions == [.start, .finish])
        #expect(r.phase == .idle)
    }

    @Test func lettingGoAfterSlidingOffCancels() {
        #expect(play([.down(listening: false), .holdElapsed, .move(onButton: false), .up]).actions == [.start, .cancel])
    }

    @Test func slidingBackOntoTheButtonFinishesAgain() {
        #expect(play([.down(listening: false), .holdElapsed, .move(onButton: false), .move(onButton: true), .up]).actions == [.start, .finish])
    }

    @Test func thePhaseShowsWhetherTheFingerIsOnTheButtonWhileHeld() {
        let held = HoldToTalk.step(.pressed, .holdElapsed)
        #expect(held.phase == .holding)
        #expect(HoldToTalk.step(held.phase, .move(onButton: false)).phase == .holdingAway)
        #expect(HoldToTalk.step(.holdingAway, .move(onButton: true)).phase == .holding)
    }

    @Test func theSystemTakingTheTouchAwayCancelsOnlyWhenHeld() {
        #expect(play([.down(listening: false), .holdElapsed, .cancel]).actions == [.start, .cancel])
        #expect(play([.down(listening: false), .cancel]).actions.isEmpty)
    }

    @Test func slidingOffBeforeTheHoldTimeDoesNothing() {
        #expect(play([.down(listening: false), .move(onButton: false), .up]).actions.isEmpty)
        #expect(play([.down(listening: false), .move(onButton: true), .up]).actions == [.tap])
    }

    @Test func aHoldWhileAlreadyListeningDoesNotStartAgain() {
        let s = HoldToTalk.step(.pressedListening, .holdElapsed)
        #expect(s.action == .none)
        #expect(HoldToTalk.step(s.phase, .up).action == .tap)
    }

    @Test func onTheButtonMeansInsideItsBoundsPlusTheSlack() {
        #expect(HoldToTalk.onButton(x: 20, y: 20, width: 40, height: 40, slack: 0))
        #expect(!HoldToTalk.onButton(x: 60, y: 20, width: 40, height: 40, slack: 0))
        #expect(HoldToTalk.onButton(x: 60, y: 20, width: 40, height: 40, slack: 24))
        #expect(!HoldToTalk.onButton(x: 20, y: -40, width: 40, height: 40, slack: 24))
    }
}

@Suite struct SpeechFailureTests {
    @Test func aLostConnectionIsTold() {
        #expect(SpeechFailure.classify(domain: NSURLErrorDomain, code: -1009, heard: false) == .offline)
        #expect(SpeechFailure.classify(domain: NSURLErrorDomain, code: -1005, heard: true) == .offline)
    }

    @Test func noSpeechIsNoMatchAndAnErrorAfterWordsIsJustTheEnd() {
        #expect(SpeechFailure.classify(domain: "kAFAssistantErrorDomain", code: 1110, heard: false) == .noMatch)
        #expect(SpeechFailure.classify(domain: "kAFAssistantErrorDomain", code: 1110, heard: true) == .none)
        #expect(SpeechFailure.classify(domain: "kAFAssistantErrorDomain", code: 301, heard: false) == .none)
        #expect(SpeechFailure.classify(domain: "x", code: 1, heard: true) == .none)
    }

    @Test func anyOtherErrorWithoutWordsIsAFailure() {
        #expect(SpeechFailure.classify(domain: "x", code: 7, heard: false) == .failed)
    }
}
