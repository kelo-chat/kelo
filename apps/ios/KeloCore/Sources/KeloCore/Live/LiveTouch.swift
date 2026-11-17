import Foundation

/*
 * What happens between a finger and the page while the owner controls a tab (the same rules as the Android/desktop shared code):
 * telling the server how big the live area is, what became of a touch, and when a movement is still a tap.
 */

/// Sends the live area's size (`view`) when it is worth sending: only to a server that speaks protocol 2, only while controlling, only
/// when the size really changed, and once more after the server newly says it speaks protocol 2 (the first frame comes after the
/// first offer). `reset()` when control ends so the next take-over reports again.
public struct ViewReporter: Sendable {
    private var last: [Int] = []
    public init() {}

    /// Physical pixels from view points and the screen scale (rounded; zero or negative sizes are never reported).
    public static func physical(points: Double, scale: Double) -> Int { Int((points * max(scale, 1)).rounded()) }

    public mutating func offer(widthPoints: Double, heightPoints: Double, scale: Double, liveV: Int, controlling: Bool) -> JSON? {
        guard controlling, LiveMessages.speaksV2(liveV), widthPoints >= 1, heightPoints >= 1 else { return nil }
        let w = ViewReporter.physical(points: widthPoints, scale: scale), h = ViewReporter.physical(points: heightPoints, scale: scale)
        let key = [w, h, Int((scale * 100).rounded())]
        guard key != last else { return nil }
        last = key
        return LiveMessages.view(width: w, height: h, density: scale)
    }

    public mutating func reset() { last = [] }
}

/// What a finished touch came to, for its mark on the picture.
public enum TouchOutcome: Equatable, Sendable {
    /// Sent to the page at this frame point.
    case sent(FramePoint)
    /// The finger lifted on a letterbox bar (outside the picture): nothing was sent.
    case outsidePicture
    /// The page was too busy and the server dropped it (reply busy / not ok).
    case notReached(FramePoint?)

    /// `mapped` = `LiveGeometry.toFrame` at the finger; `reply` = the server's answer when it came.
    public static func of(mapped: FramePoint?, reply: InputReply?) -> TouchOutcome {
        guard let mapped else { return .outsidePicture }
        if let reply, reply.busy || !reply.ok { return .notReached(mapped) }
        return .sent(mapped)
    }

    /// The "did not reach the page" note and the other mark go with these two.
    public var missed: Bool { if case .sent = self { return false }; return true }
}

public enum TouchRules {
    /// A finger that moved less than this (view points) from where it went down is still a tap / a hold.
    public static let slop = 10.0
    public static let longPressSeconds = 0.5
    /// A mark fades in this long.
    public static let markFadeSeconds = 0.35

    public static func isStill(dx: Double, dy: Double) -> Bool { abs(dx) <= slop && abs(dy) <= slop }
    public static func isLongPress(heldSeconds: Double) -> Bool { heldSeconds >= longPressSeconds }
}
