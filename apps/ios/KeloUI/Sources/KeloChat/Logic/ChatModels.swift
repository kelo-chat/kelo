import Foundation
import KeloCore

// The chat's own server models (Android chat/ChatModels.kt, SignIn.kt, SignOut.kt, vault/VaultModels.kt): KeloCore's generated
// models lack a few fields the chat needs (answer buttons on a message, a provider's sign-in state, a model's facts).

/// GET /api/messages items (oldest first). role is "user" or "assistant". `replies`: answers to tap under an agent message.
public struct ChatMessage: Codable, Equatable, Sendable {
    public var id: Int64
    public var role: String
    public var content: String
    public var createdAt: Double
    public var replies: ReplySet?
    /// The emoji on a user message (Reactions): 👀 while Kelo works on it, then one that fits; "" none (older servers send none).
    public var reaction: String
    /// On the morning briefing's message: its card (server briefing.py); the text is the same in plain words.
    public var briefing: BriefingCard?
    /// A comparison card under an agent message (server comparison.py).
    public var comparison: ComparisonCard?
    /// Screenshots Kelo showed with an agent reply (server chat_images.py); none on older servers.
    public var images: [ChatImage]
    /// The files the user sent with a message (server chat_attachments.py); none on older servers.
    public var attachments: [MessageFile]
    /// The text is the app's own sentence (nothing was typed): the bubble shows only the files.
    public var autoText: Bool
    /// The channel the message arrived by (e.g. "call"); empty string for standard chat.
    public var via: String
    /// The in-app call this message belongs to (server "call"); "" on other messages and from older servers.
    public var callId: String

    public init(id: Int64, role: String = "", content: String = "", createdAt: Double = 0, replies: ReplySet? = nil, reaction: String = "",
                briefing: BriefingCard? = nil, comparison: ComparisonCard? = nil, images: [ChatImage] = [], attachments: [MessageFile] = [], autoText: Bool = false,
                via: String = "", callId: String = "") {
        self.id = id; self.role = role; self.content = content; self.createdAt = createdAt; self.replies = replies; self.reaction = reaction
        self.briefing = briefing; self.comparison = comparison; self.images = images; self.attachments = attachments; self.autoText = autoText
        self.via = via; self.callId = callId
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optInt64("id"))
        role = j.str("role")
        content = j.str("content")
        createdAt = j.double("created_at")
        replies = j.decode("replies", as: ReplySet.self)
        reaction = j.str("reaction")
        briefing = j.decode("briefing", as: BriefingCard.self)
        comparison = j.decode("comparison", as: ComparisonCard.self)
        images = j.list("images", of: ChatImage.self)
        attachments = MessageFile.parseList(j["attachments"])
        autoText = j.bool("auto_text")
        via = j.str("via")
        callId = j.str("call")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(id, forKey: AnyKey("id"))
        try c.encode(role, forKey: AnyKey("role"))
        try c.encode(content, forKey: AnyKey("content"))
        try c.encode(createdAt, forKey: AnyKey("created_at"))
        try c.encodeIfPresent(replies, forKey: AnyKey("replies"))
        if !reaction.isEmpty { try c.encode(reaction, forKey: AnyKey("reaction")) }
        try c.encodeIfPresent(briefing, forKey: AnyKey("briefing"))
        try c.encodeIfPresent(comparison, forKey: AnyKey("comparison"))
        if !images.isEmpty { try c.encode(images, forKey: AnyKey("images")) }
        if !attachments.isEmpty { try c.encode(attachments, forKey: AnyKey("attachments")) }
        if autoText { try c.encode(true, forKey: AnyKey("auto_text")) }
        if !via.isEmpty { try c.encode(via, forKey: AnyKey("via")) }
        if !callId.isEmpty { try c.encode(callId, forKey: AnyKey("call")) }
    }

    /// The server sends the list bare; anything without an id is left out.
    public static func parseList(_ e: JSON) -> [ChatMessage] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap { try? KeloApi.decode(ChatMessage.self, $0) }
    }
}

/// Questions in an agent message with answers to tap; `goalId`: the goal the answers are for, if any.
public struct ReplySet: Codable, Equatable, Sendable {
    public var questions: [ReplyQuestion]
    public var goalId: Int64?

    public init(questions: [ReplyQuestion] = [], goalId: Int64? = nil) { self.questions = questions; self.goalId = goalId }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        questions = j.list("questions", of: ReplyQuestion.self)
        goalId = j.optInt64("goal_id")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(questions, forKey: AnyKey("questions"))
        try c.encodeIfPresent(goalId, forKey: AnyKey("goal_id"))
    }
}

/// `says`: per option, the whole sentence a tap sends (the button shows the option); empty or "" = the option itself.
/// `typed`: the options that mean the user writes the answer (a text box opens); `hint`: what to write there. Both may be missing
/// (an older server): see `QuickReplyRules.isTyped`. `kind`: the kind of answer (empty = choice; `ReplyFields`); `defaultValue`: where
/// its picker starts (plain form); `min`/`max`/`unit`: a number's.
public struct ReplyQuestion: Codable, Equatable, Sendable {
    public var question: String
    public var options: [String]
    public var says: [String]
    public var typed: [String]
    public var hint: String
    public var kind: String
    public var defaultValue: String
    public var min: Double?
    public var max: Double?
    public var unit: String
    /// A picture card per option, same order (`cardList` checks the length); none on older servers and for plain choices.
    public var cards: [ReplyCard]

    public init(question: String = "", options: [String] = [], says: [String] = [], typed: [String] = [], hint: String = "", kind: String = "",
                defaultValue: String = "", min: Double? = nil, max: Double? = nil, unit: String = "", cards: [ReplyCard] = []) {
        self.question = question; self.options = options; self.says = says; self.typed = typed; self.hint = hint
        self.kind = kind; self.defaultValue = defaultValue; self.min = min; self.max = max; self.unit = unit; self.cards = cards
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        question = j.str("question")
        options = j.strings("options")
        says = j.strings("says")
        typed = j.strings("typed")
        hint = j.str("hint")
        kind = j.str("kind")
        defaultValue = j.str("default")
        min = j.optDouble("min")
        max = j.optDouble("max")
        unit = j.str("unit")
        // one card per entry, in order: an unreadable entry becomes an empty card so the options still line up
        cards = (j["cards"]?.arrayValue ?? []).map { (try? JSON.decode(ReplyCard.self, from: $0)) ?? ReplyCard() }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(question, forKey: AnyKey("question"))
        try c.encode(options, forKey: AnyKey("options"))
        if !says.isEmpty { try c.encode(says, forKey: AnyKey("says")) }
        if !typed.isEmpty { try c.encode(typed, forKey: AnyKey("typed")) }
        if !hint.isEmpty { try c.encode(hint, forKey: AnyKey("hint")) }
        if !kind.isEmpty { try c.encode(kind, forKey: AnyKey("kind")) }
        if !defaultValue.isEmpty { try c.encode(defaultValue, forKey: AnyKey("default")) }
        try c.encodeIfPresent(min, forKey: AnyKey("min"))
        try c.encodeIfPresent(max, forKey: AnyKey("max"))
        if !unit.isEmpty { try c.encode(unit, forKey: AnyKey("unit")) }
        if !cards.isEmpty { try c.encode(cards, forKey: AnyKey("cards")) }
    }
}

/// What a choice option shows besides its words (server quick_replies `cards`): a picture, a detail line (price, delivery) and the
/// mark Kelo suggests it by. `{}` = nothing for that option.
public struct ReplyCard: Codable, Equatable, Sendable {
    public var image: ReplyCardImage?
    public var detail: String
    public var recommended: Bool

    public init(image: ReplyCardImage? = nil, detail: String = "", recommended: Bool = false) {
        self.image = image; self.detail = detail; self.recommended = recommended
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        image = j.decode("image", as: ReplyCardImage.self)
        detail = j.str("detail")
        recommended = j.bool("recommended")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encodeIfPresent(image, forKey: AnyKey("image"))
        if !detail.isEmpty { try c.encode(detail, forKey: AnyKey("detail")) }
        if recommended { try c.encode(recommended, forKey: AnyKey("recommended")) }
    }

    public var isEmpty: Bool { image == nil && detail.trimmingCharacters(in: .whitespaces).isEmpty && !recommended }
}

/// A card's picture: `url` is the server's signed-in API path (/api/chat-images/{id}), `width` x `height` in pixels.
public struct ReplyCardImage: Codable, Equatable, Sendable {
    public var imageId: String
    public var url: String
    public var width: Int
    public var height: Int

    public init(imageId: String = "", url: String = "", width: Int = 0, height: Int = 0) {
        self.imageId = imageId; self.url = url; self.width = width; self.height = height
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        imageId = j.str("image_id")
        url = j.str("url")
        width = j.int("width")
        height = j.int("height")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(imageId, forKey: AnyKey("image_id"))
        try c.encode(url, forKey: AnyKey("url"))
        try c.encode(width, forKey: AnyKey("width"))
        try c.encode(height, forKey: AnyKey("height"))
    }
}

/// How much chat history is kept on the phone for the next open (Android ChatHistoryCache, same limits).
public enum ChatHistory {
    public static let maxMessages = ChatHistoryCache.maxMessages
    public static let maxChars = ChatHistoryCache.maxChars

    public static func trim(_ messages: [ChatMessage]) -> [ChatMessage] {
        let last = messages.count > maxMessages ? Array(messages.suffix(maxMessages)) : messages
        var total = last.reduce(0) { $0 + $1.content.utf16.count }
        var from = 0
        while total > maxChars && from < last.count - 1 { total -= last[from].content.utf16.count; from += 1 }
        return from == 0 ? last : Array(last[from...])
    }
}

/// GET /api/vault items. kind is "api", "login" or "email". Values never come back from the server.
public struct ChatVaultItem: Decodable, Equatable, Sendable {
    public var name: String
    public var label: String
    public var kind: String
    public var hosts: [String]
    /// The user name the agent knew for this login ("" when none; absent on an older server): shown prefilled and editable.
    public var usernameHint: String

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = try j.require("name", j.optStr("name"))
        label = j.str("label"); kind = j.str("kind"); hosts = j.strings("hosts")
        usernameHint = UsernameHint.clean(j.str("username_hint"))
    }

    public var isLogin: Bool { kind == "login" }
}

/// GET /api/vault: `items` hold a value; `pending` are requests without one yet.
public struct ChatVaultInfo: Decodable, Equatable, Sendable {
    public var items: [ChatVaultItem]
    public var pending: [ChatVaultItem]

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = j.list("items", of: ChatVaultItem.self)
        pending = j.list("pending", of: ChatVaultItem.self)
    }

    public func find(_ name: String) -> ChatVaultItem? { (items + pending).first { $0.name == name } }
}

/// A comparison card under an agent message (server comparison.py); `open`: the owner has not written since (the buttons show).
public struct ComparisonCard: Codable, Equatable, Sendable {
    public var title: String
    public var basis: String
    public var rows: [String]
    public var options: [ComparisonOption]
    public var note: String
    public var open: Bool

    public init(title: String = "", basis: String = "", rows: [String] = [], options: [ComparisonOption] = [], note: String = "", open: Bool = false) {
        self.title = title; self.basis = basis; self.rows = rows; self.options = options; self.note = note; self.open = open
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        title = j.str("title")
        basis = j.str("basis")
        rows = j.strings("rows")
        options = j.list("options", of: ComparisonOption.self)
        note = j.str("note")
        open = j.bool("open")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        if !title.isEmpty { try c.encode(title, forKey: AnyKey("title")) }
        if !basis.isEmpty { try c.encode(basis, forKey: AnyKey("basis")) }
        if !rows.isEmpty { try c.encode(rows, forKey: AnyKey("rows")) }
        if !options.isEmpty { try c.encode(options, forKey: AnyKey("options")) }
        if !note.isEmpty { try c.encode(note, forKey: AnyKey("note")) }
        if open { try c.encode(open, forKey: AnyKey("open")) }
    }
}

public struct ComparisonOption: Codable, Equatable, Sendable {
    public var name: String
    public var values: [String]
    public var says: String
    public var recommended: Bool

    public init(name: String = "", values: [String] = [], says: String = "", recommended: Bool = false) {
        self.name = name; self.values = values; self.says = says; self.recommended = recommended
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = j.str("name")
        values = j.strings("values")
        says = j.str("says")
        recommended = j.bool("recommended")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(name, forKey: AnyKey("name"))
        try c.encode(values, forKey: AnyKey("values"))
        if !says.isEmpty { try c.encode(says, forKey: AnyKey("says")) }
        if recommended { try c.encode(recommended, forKey: AnyKey("recommended")) }
    }
}

public typealias StoredMessage = ChatMessage
