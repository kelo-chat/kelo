import Foundation

/// The capture field as the page should have it: the committed text (it starts with `RemoteTyping.mark`) and the composition the keyboard
/// still shows after it. `cursor` counts UTF-16 units inside `composing`.
public struct Typed: Equatable, Sendable {
    public var committed: String
    public var composing: String
    public var cursor: Int
    public init(_ committed: String, composing: String = "", cursor: Int? = nil) {
        self.committed = committed
        self.composing = composing
        self.cursor = cursor ?? composing.utf16.count
    }
}

/// One thing to do to the page's focused field (the server's `edit` steps).
public enum EditStep: Equatable, Sendable {
    case text(String)
    case compose(String, cursor: Int)
    case back(Int)
    case key(String)
}

/// Turns what the owner's keyboard did to a hidden capture field into the steps that make the page's field show the same: a letter, a Korean
/// syllable forming (ㄱ -> 구 -> 군), a deletion, a replacement, Enter. The same logic as Android's agent/RemoteTyping.kt; the vectors in
/// apps/shared/test-vectors/remote-typing.json are read by both.
public enum RemoteTyping {
    /// A zero-width space the field never loses, so Backspace on an empty-looking field still changes it (and reaches the page).
    public static let mark = "\u{200B}"
    public static let empty = Typed(mark)

    /// Most characters of one `text` step (the server's MAX_TEXT) and steps in one `edit` event.
    public static let maxText = 2000
    public static let maxSteps = 64
    /// More graphemes than this (not composing): the field starts again from `empty` (no steps).
    public static let maxKept = 64

    /// The field's text, the selection end and the composition (all UTF-16 offsets) as a `Typed`: a composition that ends at the end of the
    /// text is `composing`.
    public static func typed(text: String, selEnd: Int, compStart: Int?, compEnd: Int?) -> Typed {
        let units = Array(text.utf16)
        if let cs = compStart, let ce = compEnd, cs >= 0, cs < ce, ce == units.count {
            let composing = String(decoding: units[cs...], as: UTF16.self)
            let committed = String(decoding: units[..<cs], as: UTF16.self)
            return Typed(committed, composing: composing, cursor: min(max(selEnd - cs, 0), composing.utf16.count))
        }
        return Typed(text)
    }

    /// The steps that turn the page's field from `old` into `new`; empty = nothing to send.
    public static func steps(_ old: Typed, _ new: Typed) -> [EditStep] {
        var out: [EditStep] = []
        var composing = !old.composing.isEmpty
        let a = Array(old.committed), b = Array(new.committed)
        var p = 0
        while p < min(a.count, b.count) && a[p] == b[p] { p += 1 }
        let back = a.count - p
        let ins = String(b[p...])
        if back > 0 {
            if composing { out.append(.compose("", cursor: 0)) }
            composing = false
            out.append(.back(back))
        }
        var piece = ""
        func flushText() {
            if !piece.isEmpty { out.append(.text(piece)); piece = ""; composing = false }
        }
        for ch in ins.unicodeScalars {
            if ch == "\n" || ch == "\t" {
                flushText()
                if composing { out.append(.text(old.composing)); composing = false }
                out.append(.key(ch == "\n" ? "Enter" : "Tab"))
            } else {
                piece.unicodeScalars.append(ch)
            }
        }
        flushText()
        let shown = composing ? old.composing : ""
        let shownCursor = composing ? old.cursor : 0
        if new.composing != shown || (!new.composing.isEmpty && new.cursor != shownCursor) {
            out.append(.compose(new.composing, cursor: new.composing.isEmpty ? 0 : new.cursor))
        }
        return merge(out)
    }

    private static func merge(_ steps: [EditStep]) -> [EditStep] {
        var out: [EditStep] = []
        for s in steps {
            switch (out.last, s) {
            case (.back(let x)?, .back(let y)): out[out.count - 1] = .back(x + y)
            case (.text(let x)?, .text(let y)): out[out.count - 1] = .text(x + y)
            default: out.append(s)
            }
        }
        return out
    }

    /// True when the field has grown past `maxKept` graphemes and nothing is composing: it should start again from `empty`.
    public static func mustReset(_ t: Typed) -> Bool { t.composing.isEmpty && t.committed.count > maxKept }

    /// The `edit` events for `steps`: a long text is cut at `maxText` and an event holds at most `maxSteps` steps.
    public static func events(_ steps: [EditStep]) -> [JSON] {
        var flat: [EditStep] = []
        for s in steps {
            if case .text(let t) = s, t.count > maxText {
                var rest = Substring(t)
                while !rest.isEmpty { flat.append(.text(String(rest.prefix(maxText)))); rest = rest.dropFirst(maxText) }
            } else { flat.append(s) }
        }
        return stride(from: 0, to: flat.count, by: maxSteps).map { json(Array(flat[$0..<min($0 + maxSteps, flat.count)])) }
    }

    /// One `edit` event: {"type":"edit","ack":true,"steps":[["compose","구",1],["back",2],["text","x"],["key","Enter"]]}.
    public static func json(_ steps: [EditStep]) -> JSON {
        ["type": "edit", "ack": .bool(true), "steps": .array(steps.map { s -> JSON in
            switch s {
            case .text(let t): return .array([.string("text"), .string(t)])
            case .compose(let t, let c): return .array([.string("compose"), .string(t), .number(Double(c))])
            case .back(let n): return .array([.string("back"), .number(Double(n))])
            case .key(let k): return .array([.string("key"), .string(k)])
            }
        })]
    }
}

/// Sends what the capture field changes, in the owner's order: a keystroke goes at once (leading edge); changes inside `windowMs` after it
/// are merged into one trailing event (an IME that replaces several characters, a paste). Typed text lives only in `lastSent` and the event
/// on its way: never logged.
@MainActor
public final class RemoteTypingSender {
    /// What the page has by now.
    public private(set) var lastSent = RemoteTyping.empty
    private var pending: Typed?
    private var window: Task<Void, Never>?
    private let windowMs: UInt64
    private let send: (JSON) -> Void

    public init(windowMs: UInt64 = 30, send: @escaping (JSON) -> Void) {
        self.windowMs = windowMs
        self.send = send
    }

    public func update(_ t: Typed) {
        if window != nil { pending = t; return }
        emit(t)
        window = Task { [weak self, windowMs] in
            try? await Task.sleep(nanoseconds: windowMs * 1_000_000)
            guard let self, !Task.isCancelled else { return }
            let p = self.pending
            self.pending = nil
            self.window = nil
            if let p { self.emit(p) }
        }
    }

    private func emit(_ t: Typed) {
        let steps = RemoteTyping.steps(lastSent, t)
        lastSent = t
        for e in RemoteTyping.events(steps) { send(e) }
    }

    /// Sends what is pending now. Called before any other input the app sends, so the socket keeps the owner's order.
    public func flush() {
        guard let p = pending else { return }
        pending = nil
        emit(p)
    }

    /// Flushes, then commits what still composes (the page keeps what the owner saw): on hand-back, tab switch, page close.
    public func commit() {
        flush()
        if !lastSent.composing.isEmpty {
            let t = lastSent
            lastSent = Typed(t.committed + t.composing)
            send(RemoteTyping.json([.text(t.composing)]))
        }
    }

    /// The field starts again from `empty` with no steps (focus moved, a key was sent by hand, a paste...). False while composing.
    @discardableResult
    public func reset() -> Bool {
        if !lastSent.composing.isEmpty || !(pending?.composing.isEmpty ?? true) { return false }
        pending = nil
        lastSent = RemoteTyping.empty
        return true
    }

    /// A reset that also drops a composition (a failed `edit`: what the page has is no longer known).
    public func forceReset() {
        pending = nil
        lastSent = RemoteTyping.empty
    }
}
