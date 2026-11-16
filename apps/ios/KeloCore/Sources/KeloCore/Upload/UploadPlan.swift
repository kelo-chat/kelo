import Foundation

/// What the server takes in one upload on THIS route (GET /api/uploads/limits).
/// `singleMax`: the most one PUT may carry (nil: no per-request limit, e.g. Tailscale or a direct own domain); `partSize`: the size of one
/// part of a part-upload (nil: the server has no parts); `imageMax`: the most a photo may be.
/// The same table, the same rules and the same test vectors (apps/shared/test-vectors/upload-plan.json) are in the Android and desktop apps
/// (apps/shared/.../upload/UploadPlan.kt).
public struct UploadLimits: Sendable, Equatable {
    public var singleMax: Int64?
    public var partSize: Int64?
    public var imageMax: Int64

    public init(singleMax: Int64?, partSize: Int64?, imageMax: Int64) {
        self.singleMax = singleMax; self.partSize = partSize; self.imageMax = imageMax
    }

    /// An old server (the limits route answers 404): one request up to 99 MB, no parts, photos up to 15 MB: as before parts existed.
    public static let legacy = UploadLimits(singleMax: 99_000_000, partSize: nil, imageMax: 15_000_000)

    /// The server's answer, or nil when it cannot be read (the caller then uses `legacy`).
    public init?(json: JSON) {
        guard json.objectValue != nil, let image = json["image_max"]?.int64Value, image > 0 else { return nil }
        // an unreadable or non-positive size is "none", never a division by zero
        func number(_ key: String) -> Int64? { json[key]?.int64Value.flatMap { $0 > 0 ? $0 : nil } }
        self.init(singleMax: number("single_max"), partSize: number("part_size"), imageMax: image)
    }
}

/// How one file goes up: decided from its size and the route's `UploadLimits`, with no I/O (so it is tested with plain numbers).
public enum UploadPlan: Sendable, Equatable {
    /// One PUT /api/uploads/{name}.
    case single
    /// A part-upload: `sizes` are the byte counts of the parts in order (all `partSize` but the last).
    case parts(partSize: Int64, sizes: [Int64])
    /// Refused here, before anything is sent: more than `limit` bytes.
    case tooLarge(limit: Int64)
    /// The size is not known and the route has a per-request limit with parts: the size must be found out first (a copy to a temp file).
    case needsSize

    /// `size` < 0 is "not known". `isImage`: a photo, which has its own limit.
    public static func of(size: Int64, isImage: Bool, limits: UploadLimits) -> UploadPlan {
        if isImage, size > limits.imageMax { return .tooLarge(limit: limits.imageMax) }
        let single = limits.singleMax
        let part = limits.partSize
        if size < 0 {
            guard let single else { return .single }
            guard part != nil else { return .tooLarge(limit: single) }
            return .needsSize
        }
        guard let single, size > single else { return .single }
        guard let part else { return .tooLarge(limit: single) }
        return .parts(partSize: part, sizes: partSizes(size: size, part: part))
    }

    /// The byte counts of the parts of a `size`-byte file cut in `part`-byte parts: all `part` but the last (the rest).
    public static func partSizes(size: Int64, part: Int64) -> [Int64] {
        let count = Int((size + part - 1) / part)
        return (0..<count).map { $0 < count - 1 ? part : size - part * Int64(count - 1) }
    }
}

/// A file over what this route takes, found out on the phone before (or while) it goes up.
public struct UploadTooLarge: Error, Equatable, Sendable {
    public let limit: Int64
    public init(limit: Int64) { self.limit = limit }
}

/// A size limit in words for a message: 99_000_000 -> "99 MB" (decimal, like the server's own "Files can be up to 100 MB").
public enum UploadSizeText {
    public static func of(_ bytes: Int64) -> String {
        bytes >= 1_000_000_000 && bytes % 1_000_000_000 == 0 ? "\(bytes / 1_000_000_000) GB" : "\(bytes / 1_000_000) MB"
    }
}
