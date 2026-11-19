import Foundation
import Testing
@testable import KeloCore

/// The capture field's diff (Android agent/RemoteTypingTest.kt), read from the vectors every app shares (apps/shared/test-vectors/remote-typing.json).
@Suite struct RemoteTypingVectorTests {
    static func vectors() throws -> [JSON] {
        // …/apps/ios/KeloCore/Tests/KeloCoreTests/Live/RemoteTypingTests.swift -> the repository root is seven levels up
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<7 { root.deleteLastPathComponent() }
        let file = root.appendingPathComponent("apps/shared/test-vectors/remote-typing.json")
        return try JSON.parse(try Data(contentsOf: file)).arrayValue ?? []
    }

    static func typed(_ j: JSON?) -> Typed {
        let composing = j?["composing"]?.stringValue ?? ""
        let cursor = j?["cursor"]?.doubleValue.map { Int($0) } ?? composing.utf16.count
        return Typed(j?["committed"]?.stringValue ?? "", composing: composing, cursor: cursor)
    }

    static func step(_ j: JSON) -> EditStep {
        let a = j.arrayValue ?? []
        switch a[0].stringValue ?? "" {
        case "text": return .text(a[1].stringValue ?? "")
        case "compose": return .compose(a[1].stringValue ?? "", cursor: Int(a[2].doubleValue ?? 0))
        case "back": return .back(Int(a[1].doubleValue ?? 0))
        default: return .key(a[1].stringValue ?? "")
        }
    }

    @Test func everyVectorGivesItsSteps() throws {
        let rows = try Self.vectors()
        #expect(rows.count >= 20)
        for row in rows {
            let want = (row["steps"]?.arrayValue ?? []).map(Self.step)
            let got = RemoteTyping.steps(Self.typed(row["old"]), Self.typed(row["new"]))
            #expect(got == want, "\(row["name"]?.stringValue ?? "?")")
        }
    }

    @Test func typedTakesACompositionAtTheEndAsComposing() {
        let m = RemoteTyping.mark
        #expect(RemoteTyping.typed(text: m + "구", selEnd: 2, compStart: 1, compEnd: 2) == Typed(m, composing: "구", cursor: 1))
        #expect(RemoteTyping.typed(text: m + "구", selEnd: 2, compStart: nil, compEnd: nil) == Typed(m + "구"))
        #expect(RemoteTyping.typed(text: "ab", selEnd: 1, compStart: 0, compEnd: 1) == Typed("ab"))   // not at the end: all committed
        #expect(RemoteTyping.typed(text: m + "中", selEnd: 1, compStart: 1, compEnd: 2) == Typed(m, composing: "中", cursor: 0))
        // a character outside the BMP counts two UTF-16 units: the offsets are the system text field's
        #expect(RemoteTyping.typed(text: m + "𠮷", selEnd: 3, compStart: 1, compEnd: 3) == Typed(m, composing: "𠮷", cursor: 2))
    }

    @Test func theFieldStartsAgainPastSixtyFourGraphemesWhenNothingComposes() {
        #expect(!RemoteTyping.mustReset(Typed(RemoteTyping.mark + String(repeating: "a", count: 63))))
        #expect(RemoteTyping.mustReset(Typed(RemoteTyping.mark + String(repeating: "a", count: 64))))
        #expect(!RemoteTyping.mustReset(Typed(RemoteTyping.mark + String(repeating: "a", count: 64), composing: "ㄱ")))
    }

    @Test func theEventIsTheServersEditMessageAndLongTextIsCut() throws {
        let e = RemoteTyping.json([.compose("구", cursor: 1), .back(2), .text("x"), .key("Enter")])
        #expect(e["type"]?.stringValue == "edit" && e["ack"]?.boolValue == true)
        #expect((e["steps"]?.arrayValue ?? []).count == 4)
        let long = RemoteTyping.events([.text(String(repeating: "a", count: 4500))])
        #expect(long.count == 1 && (long[0]["steps"]?.arrayValue ?? []).count == 3)
        #expect(RemoteTyping.events(Array(repeating: .key("Tab"), count: 65)).count == 2)
    }
}

@MainActor @Suite struct RemoteTypingSenderTests {
    let m = RemoteTyping.mark
    static func names(_ e: JSON) -> String {
        (e["steps"]?.arrayValue ?? []).map { ($0.arrayValue ?? []).map { $0.stringValue ?? String(Int($0.doubleValue ?? 0)) }.joined(separator: ",") }.joined(separator: " ")
    }

    @Test func oneKeystrokeGoesAtOnceAndABurstBecomesTwoEvents() async throws {
        var sent: [JSON] = []
        let s = RemoteTypingSender(windowMs: 20) { sent.append($0) }
        s.update(Typed(m, composing: "ㄱ"))
        #expect(sent.map(Self.names) == ["compose,ㄱ,1"])
        sent.removeAll()
        s.update(Typed(m, composing: "구")); s.update(Typed(m, composing: "군")); s.update(Typed(m + "구", composing: "니"))
        #expect(sent.isEmpty)
        try await Task.sleep(nanoseconds: 150_000_000)
        #expect(sent.count == 1)
        #expect(sent.map(Self.names) == RemoteTyping.events(RemoteTyping.steps(Typed(m, composing: "ㄱ"), Typed(m + "구", composing: "니"))).map(Self.names))
    }

    @Test func flushKeepsTheOwnersOrderBeforeAnotherInput() {
        var sent: [JSON] = []
        let s = RemoteTypingSender(windowMs: 500) { sent.append($0) }
        s.update(Typed(m + "a")); s.update(Typed(m + "ab"))
        #expect(sent.count == 1)
        s.flush()
        #expect(sent.map(Self.names) == ["text,a", "text,b"])
        s.flush()
        #expect(sent.count == 2)
    }

    @Test func commitSendsWhatStillComposesAsText() {
        var sent: [JSON] = []
        let s = RemoteTypingSender(windowMs: 500) { sent.append($0) }
        s.update(Typed(m, composing: "ㄱ"))
        sent.removeAll()
        s.commit()
        #expect(sent.map(Self.names) == ["text,ㄱ"])
        #expect(s.lastSent == Typed(m + "ㄱ"))
        sent.removeAll()
        s.commit()
        #expect(sent.isEmpty)
    }

    @Test func resetDoesNothingWhileComposingAndStartsAgainOtherwise() {
        let s = RemoteTypingSender(windowMs: 500) { _ in }
        s.update(Typed(m, composing: "ㄱ"))
        #expect(!s.reset())
        #expect(s.lastSent == Typed(m, composing: "ㄱ"))
        s.commit()
        #expect(s.reset() && s.lastSent == RemoteTyping.empty)
        s.update(Typed(m, composing: "ㄴ"))
        s.forceReset()
        #expect(s.lastSent == RemoteTyping.empty)
    }
}

@Suite struct LiveTypingRulesTests {
    @Test func liveTypingIsOnForLiveV3AndNotBefore() {
        #expect(!LiveHeader.liveTyping(meta: nil, frameLiveV: nil))
        #expect(!LiveHeader.liveTyping(meta: nil, frameLiveV: 2))
        #expect(LiveHeader.liveTyping(meta: nil, frameLiveV: 3))
        #expect(LiveHeader.liveTyping(meta: LiveMeta(liveV: 3), frameLiveV: 2))
        #expect(!LiveHeader.liveTyping(meta: LiveMeta(liveV: 0), frameLiveV: nil))
    }

    @Test func theMetaLineAndTheHeaderReadTheNewFieldsAndOldOnesWithout() {
        guard case .meta(let m) = LiveMessages.parse(#"{"type":"meta","url":"u","title":"t","editing":true,"kind":"tel","field":"12345678","live_v":3}"#) else { Issue.record("not meta"); return }
        #expect(m == LiveMeta(url: "u", title: "t", editing: true, kind: "tel", field: "12345678", liveV: 3))
        guard case .meta(let old) = LiveMessages.parse(#"{"type":"meta","url":"u","title":"t","editing":true}"#) else { Issue.record("not meta"); return }
        #expect(old == LiveMeta(url: "u", title: "t", editing: true, kind: "text", field: "", liveV: 0))
        guard case .frame(let h) = LiveMessages.parse(#"{"type":"frame","seq":2,"editing":true,"kind":"email","field":"aa","live_v":3}"#) else { Issue.record("not frame"); return }
        #expect(h.kind == "email" && h.field == "aa" && h.liveV == 3)
        #expect(LiveHeader.typing(meta: nil, frame: h) == Typing(kind: "email", field: "aa"))
        #expect(LiveHeader.typing(meta: LiveMeta(editing: false), frame: h) == nil)
    }
}
