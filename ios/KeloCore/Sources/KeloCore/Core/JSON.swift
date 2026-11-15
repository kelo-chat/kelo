import Foundation

/// Any JSON value, read leniently the way the Android app does (kotlinx `ignoreUnknownKeys`, `coerceInputValues`): a number sent as
/// a string still reads as a number, an id sent as a number still reads as text, a missing or null field takes the default.
public enum JSON: Sendable, Equatable, Hashable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSON])
    case object([String: JSON])

    // ---- reading ----------------------------------------------------------------------------------------------------------------

    public subscript(key: String) -> JSON? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    public var isNull: Bool { if case .null = self { return true }; return false }
    public var objectValue: [String: JSON]? { if case .object(let o) = self { return o }; return nil }
    public var arrayValue: [JSON]? { if case .array(let a) = self { return a }; return nil }

    /// Only a real JSON string (`isString` in kotlinx).
    public var stringValue: String? { if case .string(let s) = self { return s }; return nil }

    /// Like kotlinx `JsonPrimitive.content` for anything but null: strings as they are, numbers without a needless ".0", booleans
    /// as "true"/"false". Nil for null, arrays and objects.
    public var text: String? {
        switch self {
        case .string(let s): return s
        case .number(let d): return JSON.format(d)
        case .bool(let b): return b ? "true" : "false"
        default: return nil
        }
    }

    public var doubleValue: Double? {
        switch self {
        case .number(let d): return d
        case .string(let s): return Double(s.trimmingCharacters(in: .whitespaces))
        default: return nil
        }
    }

    /// Whole numbers only (kotlinx `longOrNull`: "3.5" is not a Long).
    public var int64Value: Int64? {
        switch self {
        case .number(let d): return d.rounded() == d && abs(d) < 9.2e18 ? Int64(d) : nil
        case .string(let s): return Int64(s.trimmingCharacters(in: .whitespaces))
        default: return nil
        }
    }

    public var intValue: Int? { int64Value.flatMap { Int(exactly: $0) } }

    public var boolValue: Bool? {
        switch self {
        case .bool(let b): return b
        case .string(let s): return s == "true" ? true : (s == "false" ? false : nil)
        default: return nil
        }
    }

    static func format(_ d: Double) -> String {
        if d.rounded() == d && abs(d) < 1e15 { return String(Int64(d)) }
        return String(d)
    }

    // ---- writing ----------------------------------------------------------------------------------------------------------------

    /// Compact JSON with sorted keys (stable for tests), like kotlinx `toString()`.
    public var compactString: String {
        let data = (try? JSONEncoder.kelo.encode(self)) ?? Data("null".utf8)
        return String(decoding: data, as: UTF8.self)
    }

    public static func parse(_ data: Data) throws -> JSON { try JSONDecoder().decode(JSON.self, from: data) }
    public static func parse(_ text: String) throws -> JSON { try parse(Data(text.utf8)) }
}

extension JSON: Codable {
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let d = try? c.decode(Double.self) { self = .number(d) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSON].self) { self = .array(a) }
        else if let o = try? c.decode([String: JSON].self) { self = .object(o) }
        else { throw DecodingError.dataCorruptedError(in: c, debugDescription: "not JSON") }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let d):
            if d.rounded() == d && abs(d) < 1e15 { try c.encode(Int64(d)) } else { try c.encode(d) }
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
}

extension JSON: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral, ExpressibleByBooleanLiteral,
    ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral, ExpressibleByNilLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
    public init(integerLiteral value: Int) { self = .number(Double(value)) }
    public init(floatLiteral value: Double) { self = .number(value) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(arrayLiteral elements: JSON...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSON)...) { self = .object(Dictionary(elements, uniquingKeysWith: { $1 })) }
    public init(nilLiteral: ()) { self = .null }
}

extension JSONEncoder {
    /// Sorted keys, no escaped slashes: what request bodies and tests compare.
    static var kelo: JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return e
    }
}

// ---- lenient Decodable support --------------------------------------------------------------------------------------------------

/// A coding key made from any string.
public struct AnyKey: CodingKey, Hashable, Sendable {
    public let stringValue: String
    public var intValue: Int? { nil }
    public init(_ s: String) { stringValue = s }
    public init?(stringValue: String) { self.stringValue = stringValue }
    public init?(intValue: Int) { stringValue = String(intValue) }
}

/// Reads one JSON object leniently, for the hand-written `init(from:)` of the API models: every getter takes a default for a missing,
/// null or mistyped field, so a newer or older server never makes a screen fail to load.
public struct Lenient {
    public let object: [String: JSON]

    public init(_ decoder: Decoder) throws {
        let value = try JSON(from: decoder)
        object = value.objectValue ?? [:]
    }

    public init(_ value: JSON?) { object = value?.objectValue ?? [:] }

    public subscript(key: String) -> JSON? {
        guard let v = object[key], !v.isNull else { return .none }
        return v
    }
    public func has(_ key: String) -> Bool { self[key] != nil }

    public func str(_ k: String, _ def: String = "") -> String { self[k]?.text ?? def }
    /// Text, null when missing / null / blank.
    public func optStr(_ k: String) -> String? { self[k]?.text }
    public func int(_ k: String, _ def: Int = 0) -> Int { self[k]?.intValue ?? def }
    public func optInt(_ k: String) -> Int? { self[k]?.intValue }
    public func int64(_ k: String, _ def: Int64 = 0) -> Int64 { self[k]?.int64Value ?? def }
    public func optInt64(_ k: String) -> Int64? { self[k]?.int64Value }
    public func double(_ k: String, _ def: Double = 0) -> Double { self[k]?.doubleValue ?? def }
    public func optDouble(_ k: String) -> Double? { self[k]?.doubleValue }
    public func bool(_ k: String, _ def: Bool = false) -> Bool { self[k]?.boolValue ?? def }
    public func optBool(_ k: String) -> Bool? { self[k]?.boolValue }
    public func obj(_ k: String) -> Lenient { Lenient(self[k]) }
    public func strings(_ k: String) -> [String] { (self[k]?.arrayValue ?? []).compactMap { $0.text } }

    /// A field the model cannot do without (an id): missing makes the entry unreadable (a list then leaves it out).
    public func require<T>(_ k: String, _ value: T?) throws -> T {
        guard let value else { throw DecodingError.keyNotFound(AnyKey(k), .init(codingPath: [], debugDescription: "missing \(k)")) }
        return value
    }

    /// A nested model, null when missing or unreadable.
    public func decode<T: Decodable>(_ k: String, as type: T.Type = T.self) -> T? {
        guard let v = self[k] else { return nil }
        return try? JSON.decode(T.self, from: v)
    }

    /// A list of models; unreadable entries are left out.
    public func list<T: Decodable>(_ k: String, of type: T.Type = T.self) -> [T] {
        (self[k]?.arrayValue ?? []).compactMap { try? JSON.decode(T.self, from: $0) }
    }
}

extension JSON {
    /// Decodes a model from an already parsed value.
    public static func decode<T: Decodable>(_ type: T.Type, from value: JSON) throws -> T {
        if T.self == JSON.self { return value as! T }
        let data = try JSONEncoder().encode(value)
        return try JSONDecoder().decode(T.self, from: data)
    }
}
