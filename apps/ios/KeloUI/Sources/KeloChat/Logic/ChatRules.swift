import Foundation
import KeloCore

/// What a long press on a chat bubble offers.
public enum MsgAction: Sendable { case copy, share, select, readAloud, retry, edit, report }

/// Which actions a bubble's menu shows, and what 다시 시도 sends (Android chat/MessageActions.kt).
public enum MessageActions {
    public static func actions(_ items: [ChatItem], key: String, busy: Bool) -> [MsgAction] {
        guard let item = items.first(where: { $0.key == key }) else { return [] }
        let retry = !busy && retryText(items, key: key) != nil
        switch item {
        case .msg(let m):
            if m.mine { return [.copy, .share, .select, .edit] }
            return [.copy, .share, .select, .readAloud] + (retry ? [.retry] : []) + [.report]
        case .error: return [.copy] + (retry ? [.retry] : [])
        default: return []
        }
    }

    /// The user message that 다시 시도 on `key` sends again: only for the newest reply, or an error with no user message after it.
    public static func retryText(_ items: [ChatItem], key: String) -> String? {
        guard let at = items.firstIndex(where: { $0.key == key }) else { return nil }
        let later = items[(at + 1)...]
        if later.contains(where: { $0.isMine }) { return nil }
        switch items[at] {
        case .msg(let m): if m.mine || later.contains(where: { $0.asMsg != nil }) { return nil }
        case .error: break
        default: return nil
        }
        guard let asked = items[..<at].last(where: { $0.isMine })?.asMsg else { return nil }
        return asked.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : asked.text
    }
}

/// Where the chat list is and what the "↓" pill shows (Android chat/ChatScroll.kt).
public enum ChatScroll {
    /// The list measures where it is from its own scroll geometry (iOS 18+), else from the newest row's sentinel appearing.
    public static var measures: Bool {
        if #available(iOS 18.0, macOS 15.0, *) { return true }
        return false
    }

    /// Whether new rows should pull the list down: only when the reader was near the newest, or just sent something.
    public static func follow(atBottom: Bool, lastIsMine: Bool) -> Bool { lastIsMine || atBottom }

    /// A drag on the list lets go of the newest row when it pulls the rows down (the finger moves down: older rows come in).
    public static func letsGo(dragY: Double) -> Bool { dragY > 0 }

    /// The dot on the pill: lit when the assistant wrote more while the reader was up the list, cleared at the bottom.
    public static func unread(_ was: Bool, atBottom: Bool, replyGrew: Bool) -> Bool {
        if atBottom { return false }
        return replyGrew ? true : was
    }

    /// Changes whenever the assistant's output grows; rows the user adds do not count.
    public static func replySignature(_ items: [ChatItem]) -> String {
        guard let last = items.last(where: { !$0.isMine }) else { return "" }
        return last.key + "#" + String(last.asMsg?.text.count ?? 0)
    }
}

/// Answer buttons under an agent message (Android chat/QuickReplyRules.kt, the same rules).
///
/// An answer may mean "I will write it" ("Other", "직접 입력할게요", "있어요 (직접 입력)"): the server lists those in the question's
/// `typed` (the app reads no words of any language). Picking one opens a text box under that question; what is typed is sent instead of
/// the button's words, and nothing is sent while a picked one is still empty. The server's options are the only answers the app shows as
/// buttons: the app adds no answer of its own; the user can type in the message box.
public enum QuickReplyRules {

    /// Buttons show only under the newest agent message, while the user has not written after it.
    public static func isOpen(_ items: [ChatItem], key: String) -> Bool {
        let msgs = items.compactMap(\.asMsg)
        guard let i = msgs.firstIndex(where: { $0.key == key }) else { return false }
        return !msgs[(i + 1)...].contains { $0.mine }
    }

    /// The buttons of a choice question: the server's options. Choices are suggestions: the user can always answer outside them.
    /// A field question has no buttons.
    public static func choices(_ q: ReplyQuestion) -> [String] { ReplyFields.isField(q) ? [] : q.options }

    /// One choice question: a tap sends the answer at once. Several, or a field: the answers go together with the send button.
    public static func sendsAtOnce(_ set: ReplySet) -> Bool { set.questions.count <= 1 && !set.questions.contains(where: ReplyFields.isField) }

    /// The answer `option` of `q` opens a text box instead of being the answer itself.
    public static func isTyped(_ q: ReplyQuestion, _ option: String) -> Bool { q.typed.contains(option) }

    /// A tap on `option` sends it straight away (a single question, and not an answer to write).
    public static func sendsOnTap(_ set: ReplySet, _ q: ReplyQuestion, _ option: String) -> Bool { sendsAtOnce(set) && !isTyped(q, option) }

    /// Questions whose picked answer is one to write: their text box shows.
    public static func writing(_ set: ReplySet, picks: [Int: String]) -> Set<Int> {
        Set(picks.compactMap { i, o in i < set.questions.count && isTyped(set.questions[i], o) ? i : nil })
    }

    /// The send button shows: several questions, a field, or a picked answer to write.
    public static func showsSend(_ set: ReplySet, picks: [Int: String]) -> Bool { !sendsAtOnce(set) || !writing(set, picks: picks).isEmpty }

    /// Something is answered, every picked answer to write has words, and every field that has something in it fits its kind.
    public static func canSend(_ set: ReplySet, picks: [Int: String], typed: [Int: String], values: [Int: String] = [:]) -> Bool {
        let filled = values.filter { i, v in !v.trimmingCharacters(in: .whitespaces).isEmpty && i < set.questions.count && ReplyFields.isField(set.questions[i]) }
        return (!picks.isEmpty || !filled.isEmpty) && writing(set, picks: picks).allSatisfy { oneLine(typed[$0]) != nil }
            && filled.allSatisfy { i, v in ReplyFields.check(set.questions[i], v) != nil }
    }

    /// The text box's hint: the server's, else the question itself, else nil (the app's own "write your answer").
    public static func placeholder(_ q: ReplyQuestion) -> String? {
        let h = q.hint.trimmingCharacters(in: .whitespacesAndNewlines)
        let t = h.isEmpty ? q.question.trimmingCharacters(in: .whitespacesAndNewlines) : h
        return t.isEmpty ? nil : t
    }

    /// Each answered question's answer: its place and its plain form (typed words for an answer to write, a field's checked value).
    public static func answers(_ set: ReplySet, picks: [Int: String], typed: [Int: String] = [:], values: [Int: String] = [:]) -> [ReplyAnswer] {
        let w = writing(set, picks: picks)
        return set.questions.indices.compactMap { i -> ReplyAnswer? in
            let q = set.questions[i]
            let plain: String?
            if ReplyFields.isField(q) { plain = ReplyFields.check(q, values[i]) }
            else {
                guard let pick = picks[i] else { return nil }
                plain = w.contains(i) ? oneLine(typed[i]) : pick
            }
            return plain.map { ReplyAnswer(i, $0) }
        }
    }

    /// The whole sentence a picked option sends, or null (no sentence for it, a typed answer, or a field).
    public static func sentence(_ q: ReplyQuestion, pick: String) -> String? {
        guard let i = q.options.firstIndex(of: pick), i < q.says.count else { return nil }
        let s = q.says[i].trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : s
    }

    /// The message sent: one line per answered question ("question: answer"; a lone question just the answer), typed words in place
    /// of an answer to write, a field's value in the user's words (`show`: `ReplyFields.display`).
    public static func compose(_ set: ReplySet, picks: [Int: String], typed: [Int: String] = [:], values: [Int: String] = [:],
                               show: (ReplyQuestion, String) -> String = { $1 }) -> String {
        answers(set, picks: picks, typed: typed, values: values).map { a in
            let q = set.questions[a.question]
            if !ReplyFields.isField(q), let s = sentence(q, pick: a.value) { return s }
            let answer = ReplyFields.isField(q) ? show(q, a.value) : a.value
            let title = q.question.trimmingCharacters(in: .whitespaces)
            return title.isEmpty || set.questions.count == 1 ? answer : "\(title): \(answer)"
        }.joined(separator: "\n")
    }

    static func oneLine(_ text: String?) -> String? {
        guard let text else { return nil }
        let t = text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }.joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
}

/// Shared text joining what is already typed.
public enum DraftText {
    public static func append(_ draft: String, _ text: String) -> String {
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return draft }
        if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return text }
        return draft.replacingOccurrences(of: "\\s+$", with: "", options: .regularExpression) + "\n" + text
    }
}

/// The words a message uses for its attachments (from the strings, so they follow the app's language).
public struct AttachmentWords: Sendable {
    public var line: @Sendable (String) -> String
    public var ask: @Sendable (String) -> String
    public var separator: String
    public init(line: @escaping @Sendable (String) -> String, ask: @escaping @Sendable (String) -> String, separator: String) {
        self.line = line; self.ask = ask; self.separator = separator
    }
}

public enum AttachmentText {
    /// The message sent: the typed text with a line per attachment under it, or (nothing typed) a request to read them.
    public static func compose(_ text: String, names: [String], words: AttachmentWords) -> String {
        let typed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if names.isEmpty { return typed }
        if typed.isEmpty { return words.ask(names.joined(separator: words.separator)) }
        return typed + "\n\n" + names.map(words.line).joined(separator: "\n")
    }
}

/// A photo or file picked with "+", shown above the composer while it uploads and sent with the next message.
public struct Attachment: Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var image: Bool
    public var state: AttachState = .uploading
    public var sent: Int64 = 0
    public var total: Int64 = 0
    public var stored: String? = nil
    public var error: AttachError? = nil
    public var thumbnail: Data? = nil

    public init(id: String, name: String, image: Bool) { self.id = id; self.name = name; self.image = image }

    /// 0...1, or nil while the size is not known yet.
    public var progress: Double? { total > 0 ? min(max(Double(sent) / Double(total), 0), 1) : nil }
}

public enum AttachState: Sendable { case uploading, done, failed }

public enum AttachError: Equatable, Sendable {
    case server(String)
    /// More than `limit` bytes: the most the server takes on this route, or for a photo
    case tooLarge(Int64)
    case network
    case unreadable
}

/// Photo scaling and naming (Android ImageScale). The size limits are not here: they come from the server (`UploadLimits`).
public enum UploadRules {
    public static let maxPhotos = 5
    public static let maxEdge = 2048
    public static let jpegQuality = 0.85

    /// The name a re-encoded photo is stored under: its own name with .jpg, or photo-<time>.jpg.
    public static func jpegName(_ display: String?, now: Int64) -> String {
        var base = display ?? ""
        if let slash = base.lastIndex(of: "/") { base = String(base[base.index(after: slash)...]) }
        if let dot = base.lastIndex(of: ".") { base = String(base[..<dot]) }
        base = base.trimmingCharacters(in: .whitespaces)
        return (base.isEmpty ? "photo-\(now)" : base) + ".jpg"
    }

    /// The stored name without the "uploads/" prefix the server reports.
    public static func stored(_ path: String, fallback: String) -> String {
        let s = path.hasPrefix("uploads/") ? String(path.dropFirst("uploads/".count)) : path
        return s.isEmpty ? fallback : s
    }

    /// The failure an upload error means: the server's own words (507 storage full, 409 too many large uploads open, 413 …).
    public static func error(_ e: Error) -> AttachError {
        guard let a = e as? ApiError else { return .unreadable }
        if a.isNetwork { return .network }
        return .server(a.detail)
    }
}

/// Why a reply is reported; `rawValue` is what the server takes.
public enum ReportReason: String, CaseIterable, Sendable { case harmful, inaccurate, privacy, other }

/// What the report sheet holds; kept when sending fails or the sheet is closed.
public struct ReportDraft: Equatable, Sendable {
    public var reason: ReportReason?
    public var comment = ""
    public var includeAsked = false
    public init(reason: ReportReason? = nil, comment: String = "", includeAsked: Bool = false) {
        self.reason = reason; self.comment = comment; self.includeAsked = includeAsked
    }
}

/// The report's texts and request body (Android ReportRules). Nothing but these fields leaves the phone.
public enum ReportRules {
    public static let maxReply = 8000
    public static let maxAsked = 4000
    public static let maxComment = 1000

    public static func replyText(_ items: [ChatItem], key: String) -> String? {
        guard let m = items.first(where: { $0.key == key })?.asMsg, !m.mine else { return nil }
        let t = String(Markdown.splitBubbles(m.text).map { Markdown.plain($0) }.joined(separator: "\n\n")
            .trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxReply))
        return t.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : t
    }

    public static func askedBefore(_ items: [ChatItem], key: String) -> String? {
        guard let at = items.firstIndex(where: { $0.key == key }), at > 0,
              let asked = items[..<at].last(where: { $0.isMine })?.asMsg else { return nil }
        let t = String(asked.text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxAsked))
        return t.isEmpty ? nil : t
    }

    public static func canSend(_ d: ReportDraft) -> Bool { d.reason != nil }

    public static func body(_ d: ReportDraft, reply: String, asked: String?, appVersion: String) -> JSON {
        var o: [String: JSON] = [
            "reason": .string((d.reason ?? .other).rawValue),
            "reply": .string(String(reply.prefix(maxReply))),
            "app_version": .string(appVersion),
        ]
        let comment = String(d.comment.trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxComment))
        if !comment.isEmpty { o["comment"] = .string(comment) }
        if d.includeAsked, let asked, !asked.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            o["asked"] = .string(String(asked.prefix(maxAsked)))
        }
        return .object(o)
    }
}

public enum CardCountdown {
    /// 272 -> "4:32"
    public static func text(_ seconds: Int64) -> String {
        let s = max(seconds, 0)
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// The proportions of the phone card's pictures.
public enum PhoneShape {
    /// Width over height of the device's picture, kept between a very tall and a squarish screen; 9:19.5 when the size is unknown.
    public static func aspect(width: Int, height: Int) -> CGFloat {
        guard width > 0, height > 0 else { return 9.0 / 19.5 }
        return min(0.8, max(0.4, CGFloat(width) / CGFloat(height)))
    }
}

/// The letter on a site's tile: the host's first letter or digit ("www." skipped), else the title's.
public enum SiteTile {
    public static func letter(host: String, title: String) -> String {
        let h = host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
        let ch = h.first { $0.isLetter || $0.isNumber } ?? title.first { $0.isLetter || $0.isNumber } ?? "?"
        return String(ch).uppercased()
    }
}

public enum BrowserImage {
    /// The bytes of a base64 data: URL, or nil when it is not one.
    public static func bytes(_ dataUrl: String) -> Data? {
        guard dataUrl.hasPrefix("data:image/"), let comma = dataUrl.firstIndex(of: ",") else { return nil }
        guard dataUrl[..<comma].hasSuffix(";base64") else { return nil }
        return Data(base64Encoded: String(dataUrl[dataUrl.index(after: comma)...]), options: .ignoreUnknownCharacters)
    }
}

/// Putting spoken words into a text at the cursor (Android voice/SpeechInput.kt SpeechMerge). iOS's TextField gives no cursor, so
/// the words go at the end of what is typed.
public enum SpeechMerge {
    public struct Anchor: Equatable, Sendable { public var before: String, after: String }

    public static func anchor(_ text: String) -> Anchor { Anchor(before: text, after: "") }

    public static func merge(_ anchor: Anchor, spoken: String) -> String {
        let words = spoken.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        if words.isEmpty { return anchor.before + anchor.after }
        let lead = !anchor.before.isEmpty && !(anchor.before.last?.isWhitespace ?? true) ? " " : ""
        let noSpaceBefore: Set<Character> = [".", ",", "!", "?", ":", ";", ")", "]", "}", "。", "、", "？", "！"]
        let trail = !anchor.after.isEmpty && !(anchor.after.first?.isWhitespace ?? true) && !noSpaceBefore.contains(anchor.after.first!) ? " " : ""
        return anchor.before + lead + words + trail + anchor.after
    }
}

/// How fast a reply's text is revealed while it streams in (Android chat/RevealPacing.kt, same constants).
public enum RevealPacing {
    public static let readPerMs = 0.14
    public static let catchUpMs = 700.0
    public static let maxLagMs = 1000.0

    public struct State: Equatable, Sendable {
        public var shown: Double
        public var target: Int
        public var perMs: Double
        public var done: Bool { shown >= Double(target) }
        public init(shown: Double, target: Int, perMs: Double) { self.shown = shown; self.target = target; self.perMs = perMs }
    }

    public static func start(_ target: Int, animate: Bool) -> State {
        animate ? retarget(State(shown: 0, target: 0, perMs: 0), target) : State(shown: Double(target), target: target, perMs: 0)
    }

    public static func speed(_ chars: Double) -> Double { max(readPerMs, chars / catchUpMs) }

    public static func retarget(_ s: State, _ target: Int) -> State {
        if Double(target) <= s.shown { return State(shown: Double(target), target: target, perMs: 0) }
        return State(shown: s.shown, target: target, perMs: speed(Double(target) - s.shown))
    }

    public static func advance(_ s: State, dtMs: Double) -> State {
        if s.done || dtMs <= 0 { return s.shown > Double(s.target) ? State(shown: Double(s.target), target: s.target, perMs: s.perMs) : s }
        let backlog = Double(s.target) - s.shown
        let perMs = max(s.perMs, backlog / maxLagMs, readPerMs)
        return State(shown: min(s.shown + perMs * dtMs, Double(s.target)), target: s.target, perMs: perMs)
    }
}

public enum ChatRules {
    /// Only the server's "no model chosen" answer (400 with `code: model_required`) shows the plain pick-a-model words; any other
    /// 400 says its own words (Android ChatViewModel).
    public static func modelRequired(_ e: ApiError) -> Bool {
        e.status == 400 && e.detailJson?["code"]?.stringValue == "model_required"
    }
}
