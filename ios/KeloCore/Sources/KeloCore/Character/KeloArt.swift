// Generated from android/design/character/kelo_*.svg by ios/tools/gen_kelo_art.py. Do not edit by hand: change the SVGs and
// generate again (from the repository root):
//   python3 ios/tools/gen_kelo_art.py android/design/character ios/KeloCore/Sources/KeloCore/Character/KeloArt.swift
// Every shape in SVG order with its fill/stroke resolved from the groups around it, and every CSS-animated element as a KeloGroup
// carrying its KeloAnim (duration, delay, timing function, direction, transform-origin and @keyframes). The same data as the
// Android app's KeloArt.kt (android/design/character/gen_art.py).

// swiftlint:disable all
public enum KeloArt {
    /// The SVGs' viewBox is 0 0 120 120.
    public static let viewBox: Double = 120.0

    /// The drawing of a mood.
    public static func of(_ mood: KeloMood) -> KeloMoodArt {
        switch mood {
        case .idle: return idle
        case .working: return working
        case .browser: return browser
        case .approval: return approval
        case .thinking: return thinking
        case .done: return done
        case .error: return error
        case .offline: return offline
        case .sleeping: return sleeping
        case .listening: return listening
        case .reading: return reading
        }
    }

    // MARK: kelo_idle.svg

    private static let idleShapes: [KeloShape] = [
        KeloShape("M60 28 C50 14, 58 8, 64 16 C68 21, 63 26, 60 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M30 66 C20 72, 20 82, 28 80", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M90 66 C100 72, 100 82, 92 80", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M60 28 C36 28, 26 48, 28 72 C29 88, 42 96, 60 96 C78 96, 91 88, 92 72 C94 48, 84 28, 60 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M48 96 L48 104 M43 104 L53 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M72 96 L72 104 M67 104 L77 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M48 94 L48 104 M43 104 L53 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M72 94 L72 104 M67 104 L77 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M60 29 C48 15, 57 7, 65 15 C69 20, 64 27, 60 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 1),
        KeloShape("M60 28 C36 28, 26 48, 28 72 C29 88, 42 96, 60 96 C78 96, 91 88, 92 72 C94 48, 84 28, 60 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M60 64 C44 64, 38 78, 41 92 C46 95, 53 96, 60 96 C67 96, 74 95, 79 92 C82 78, 76 64, 60 64 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M60 28 C36 28, 26 48, 28 72 C29 88, 42 96, 60 96 C78 96, 91 88, 92 72 C94 48, 84 28, 60 28 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M29 60 C19 68, 19 82, 29 79", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M91 60 C101 68, 101 82, 91 79", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M35.5 63A4.5 2.5 0 1 0 44.5 63A4.5 2.5 0 1 0 35.5 63Z", fill: 0xFF81D4FA, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M75.5 63A4.5 2.5 0 1 0 84.5 63A4.5 2.5 0 1 0 75.5 63Z", fill: 0xFF81D4FA, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M41.8 57A4.2 4.2 0 1 0 50.2 57A4.2 4.2 0 1 0 41.8 57Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 2),
        KeloShape("M69.8 57A4.2 4.2 0 1 0 78.2 57A4.2 4.2 0 1 0 69.8 57Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 2),
        KeloShape("M43.5 55.5A1.3 1.3 0 1 0 46.1 55.5A1.3 1.3 0 1 0 43.5 55.5Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 2),
        KeloShape("M71.5 55.5A1.3 1.3 0 1 0 74.1 55.5A1.3 1.3 0 1 0 71.5 55.5Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 2),
        KeloShape("M54 60A6 3.5 0 1 0 66 60A6 3.5 0 1 0 54 60Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
    ]

    private static let idleGroups: [KeloGroup] = [
        KeloGroup(
            name: "kelo-breathe", parent: nil, shapes: 0..<21,
            anim: KeloAnim(
                durationMs: 2400, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 60.0, y: 95.0),
                ops: [.translate, .scale],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [0.0, 0.0, 1.0, 1.0, 0.0, -2.0, 1.02, 0.98, 0.0, 0.0, 1.0, 1.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "kelo-leaf", parent: 0, shapes: 8..<9,
            anim: KeloAnim(
                durationMs: 2400, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 60.0, y: 28.0),
                ops: [.rotate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [-4.0, 0.0, 6.0, 0.0, -4.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "kelo-eyes", parent: 0, shapes: 16..<20,
            anim: KeloAnim(
                durationMs: 3600, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 60.0, y: 58.0),
                ops: [.scale],
                tKeys: [0.0, 0.44, 0.46, 0.48, 0.92, 0.94, 0.96, 1.0],
                tVals: [1.0, 1.0, 1.0, 1.0, 1.0, 0.1, 1.0, 1.0, 1.0, 1.0, 1.0, 0.1, 1.0, 1.0, 1.0, 1.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_idle.svg
    public static let idle: KeloMoodArt = KeloMoodArt(mood: .idle, shapes: idleShapes, groups: idleGroups)

    // MARK: kelo_working.svg

    private static let workingShapes: [KeloShape] = [
        KeloShape("M52 28 C42 14, 50 8, 56 16 C60 21, 55 26, 52 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M52 28 C30 28, 20 48, 22 72 C23 88, 35 96, 52 96 C69 96, 81 88, 82 72 C84 48, 74 28, 52 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M41 96 L41 104 M63 96 L63 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M41 94 L41 104 M36 104 L46 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M63 94 L63 104 M58 104 L68 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M52 29 C40 15, 49 7, 57 15 C61 20, 56 27, 52 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M52 28 C30 28, 20 48, 22 72 C23 88, 35 96, 52 96 C69 96, 81 88, 82 72 C84 48, 74 28, 52 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M52 64 C38 64, 32 78, 35 92 C40 95, 46 96, 52 96 C58 96, 64 95, 69 92 C72 78, 66 64, 52 64 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M52 28 C30 28, 20 48, 22 72 C23 88, 35 96, 52 96 C69 96, 81 88, 82 72 C84 48, 74 28, 52 28 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M35 57A4 4 0 1 0 43 57A4 4 0 1 0 35 57Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M61 57A4 4 0 1 0 69 57A4 4 0 1 0 61 57Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M46.5 60A5.5 3.2 0 1 0 57.5 60A5.5 3.2 0 1 0 46.5 60Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M62 74 L106 66 L112 78 L72 88Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M74 84H80V102H74Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M98 78H104V96H98Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M46 28 C36 14, 44 8, 50 16 C54 21, 49 26, 46 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M46 28 C24 28, 16 48, 18 72 C19 88, 31 96, 48 96 C65 96, 76 88, 77 72 C78 48, 68 28, 46 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M18 80 L10 75 L14 86 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M38 96 L38 104 M58 96 L58 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M74 83H80V102H74Z", fill: 0xFFA1887F, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .round, group: 1),
        KeloShape("M98 77H104V96H98Z", fill: 0xFFA1887F, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .round, group: 1),
        KeloShape("M60 74 L104 65 L112 77 L70 87Z", fill: 0xFFD7CCC8, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .round, group: 1),
        KeloShape("M72 73 L96 68 L102 76 L78 82Z", fill: 0xFFFFFFFF, stroke: 0xFF1A1A1A, width: 3.0, cap: .butt, join: .round, group: 1),
        KeloShape("M38 94 L38 104 M34 104 L43 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 2),
        KeloShape("M58 94 L58 104 M54 104 L63 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 2),
        KeloShape("M46 29 C34 15, 43 7, 51 15 C55 20, 50 27, 46 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 2),
        KeloShape("M46 28 C24 28, 16 48, 18 72 C19 88, 31 96, 48 96 C65 96, 76 88, 77 72 C78 48, 68 28, 46 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 2),
        KeloShape("M20 78 C10 73, 9 84, 21 86", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 2),
        KeloShape("M30 42 C33 36, 40 34, 44 34", fill: nil, stroke: 0xFF81D4FA, width: 4.0, cap: .round, join: .miter, group: 2),
        KeloShape("M84 52 L89 74 L84 75 L79 53 Z", fill: 0xFFFFEE58, stroke: 0xFF1A1A1A, width: 3.0, cap: .butt, join: .round, group: 3),
        KeloShape("M89 74 L84 75 L87 80Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 3),
        KeloShape("M78 49 L83.8689 47.7525 L84.9084 52.6433 L79.0396 53.8907Z", fill: 0xFFEF5350, stroke: 0xFF1A1A1A, width: 2.5, cap: .butt, join: .miter, group: 3),
        KeloShape("M64 62 C78 60, 88 66, 84 74 C79 79, 68 78, 62 73", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 3),
        KeloShape("M84 42 C87 39, 91 43, 94 40 C97 37, 100 41, 103 38", fill: nil, stroke: 0xFF1A1A1A, width: 2.5, cap: .round, join: .miter, group: 4),
        KeloShape("M88 49 C91 46, 95 50, 98 47", fill: nil, stroke: 0xFF1A1A1A, width: 2.5, cap: .round, join: .miter, group: 4),
        KeloShape("M12 54 L8 51", fill: nil, stroke: 0xFF1A1A1A, width: 3.0, cap: .round, join: .miter, group: 4),
        KeloShape("M10 62 L5 62", fill: nil, stroke: 0xFF1A1A1A, width: 3.0, cap: .round, join: .miter, group: 4),
    ]

    private static let workingGroups: [KeloGroup] = [
        KeloGroup(
            name: "pose-front", parent: nil, shapes: 0..<12,
            anim: KeloAnim(
                durationMs: 4500, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 50.0, y: 65.0),
                ops: [.scale],
                tKeys: [0.0, 0.2, 0.24, 0.92, 0.96, 1.0],
                tVals: [1.0, 1.0, 1.0, 1.0, 0.15, 1.0, 0.15, 1.0, 1.0, 1.0, 1.0, 1.0],
                oKeys: [0.0, 0.2, 0.24, 0.92, 0.96, 1.0],
                oVals: [1.0, 1.0, 0.0, 0.0, 1.0, 1.0],
                baseOpacity: 0.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "pose-back", parent: nil, shapes: 12..<37,
            anim: KeloAnim(
                durationMs: 4500, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 50.0, y: 65.0),
                ops: [.scale],
                tKeys: [0.0, 0.21, 0.25, 0.91, 0.95, 1.0],
                tVals: [0.15, 1.0, 0.15, 1.0, 1.0, 1.0, 1.0, 1.0, 0.15, 1.0, 0.15, 1.0],
                oKeys: [0.0, 0.21, 0.25, 0.91, 0.95, 1.0],
                oVals: [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "body-bob", parent: 1, shapes: 23..<33,
            anim: KeloAnim(
                durationMs: 300, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 48.0, y: 90.0),
                ops: [.translate],
                tKeys: [0.0, 1.0],
                tVals: [0.0, 0.0, 0.0, 1.5],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "writing-wing", parent: 2, shapes: 29..<33,
            anim: KeloAnim(
                durationMs: 300, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 68.0, y: 68.0),
                ops: [.rotate, .translate],
                tKeys: [0.0, 1.0],
                tVals: [-8.0, 0.0, 0.0, 0.0, 10.0, 0.0, 3.0, -2.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "scribble-lines", parent: 1, shapes: 33..<37,
            anim: KeloAnim(
                durationMs: 600, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [0.0, 1.0, 0.0, -2.0, 0.0, 1.0],
                oKeys: [0.0, 0.5, 1.0],
                oVals: [0.35, 1.0, 0.35],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_working.svg
    public static let working: KeloMoodArt = KeloMoodArt(mood: .working, shapes: workingShapes, groups: workingGroups)

    // MARK: kelo_browser.svg

    private static let browserShapes: [KeloShape] = [
        KeloShape("M52 28 C42 14, 50 8, 56 16 C60 21, 55 26, 52 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M52 28 C30 28, 20 48, 22 72 C23 88, 35 96, 52 96 C69 96, 81 88, 82 72 C84 48, 74 28, 52 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M41 96 L41 104 M63 96 L63 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M41 94 L41 104 M36 104 L46 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M63 94 L63 104 M58 104 L68 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M52 29 C40 15, 49 7, 57 15 C61 20, 56 27, 52 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M52 28 C30 28, 20 48, 22 72 C23 88, 35 96, 52 96 C69 96, 81 88, 82 72 C84 48, 74 28, 52 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M52 64 C38 64, 32 78, 35 92 C40 95, 46 96, 52 96 C58 96, 64 95, 69 92 C72 78, 66 64, 52 64 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M52 28 C30 28, 20 48, 22 72 C23 88, 35 96, 52 96 C69 96, 81 88, 82 72 C84 48, 74 28, 52 28 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M35 57A4 4 0 1 0 43 57A4 4 0 1 0 35 57Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M61 57A4 4 0 1 0 69 57A4 4 0 1 0 61 57Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M46.5 60A5.5 3.2 0 1 0 57.5 60A5.5 3.2 0 1 0 46.5 60Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M74 38H104A4 4 0 0 1 108 42V62A4 4 0 0 1 104 66H74A4 4 0 0 1 70 62V42A4 4 0 0 1 74 38Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M62 76 L108 68 L114 80 L70 90Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M76 86H82V102H76Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M100 80H106V96H100Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M44 28 C34 14, 42 8, 48 16 C52 21, 47 26, 44 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M44 28 C22 28, 14 48, 16 72 C17 88, 29 96, 46 96 C63 96, 74 88, 75 72 C76 48, 66 28, 44 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M16 80 L8 75 L12 86 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M36 96 L36 104 M56 96 L56 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 1),
        KeloShape("M76 85H82V102H76Z", fill: 0xFFA1887F, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M100 79H106V96H100Z", fill: 0xFFA1887F, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M62 76 L108 68 L114 80 L70 90Z", fill: 0xFFD7CCC8, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M85 64H93V72H85Z", fill: 0xFF90A4AE, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M81 72A8 2.5 0 1 0 97 72A8 2.5 0 1 0 81 72Z", fill: 0xFF78909C, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M74 38H104A4 4 0 0 1 108 42V62A4 4 0 0 1 104 66H74A4 4 0 0 1 70 62V42A4 4 0 0 1 74 38Z", fill: 0xFF546E7A, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M76 42H102A2 2 0 0 1 104 44V60A2 2 0 0 1 102 62H76A2 2 0 0 1 74 60V44A2 2 0 0 1 76 42Z", fill: 0xFFE1F5FE, stroke: 0xFF1A1A1A, width: 2.5, cap: .butt, join: .round, group: 2),
        KeloShape("M77 46H87V53H77Z", fill: 0xFF4FC3F7, stroke: nil, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M89 46H101V49H89Z", fill: 0xFF0288D1, stroke: nil, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M89 51H99V54H89Z", fill: 0xFF81D4FA, stroke: nil, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M77 55H101V59H77Z", fill: 0xFFFFFFFF, stroke: nil, width: 3.5, cap: .butt, join: .round, group: 1),
        KeloShape("M72 77 L94 73 L98 79 L76 84Z", fill: 0xFFCFD8DC, stroke: 0xFF1A1A1A, width: 2.8, cap: .butt, join: .round, group: 1),
        KeloShape("M36 94 L36 104 M32 104 L41 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 1),
        KeloShape("M56 94 L56 104 M52 104 L61 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 1),
        KeloShape("M44 29 C32 15, 41 7, 49 15 C53 20, 48 27, 44 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 1),
        KeloShape("M72 56 L80 59 L72 63Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.2, cap: .butt, join: .round, group: 1),
        KeloShape("M44 28 C22 28, 14 48, 16 72 C17 88, 29 96, 46 96 C63 96, 74 88, 75 72 C76 48, 66 28, 44 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 1),
        KeloShape("M18 78 C8 73, 7 84, 19 86", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 1),
        KeloShape("M61.2 54A3.8 3.8 0 1 0 68.8 54A3.8 3.8 0 1 0 61.2 54Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M64.8 52.8A1.2 1.2 0 1 0 67.2 52.8A1.2 1.2 0 1 0 64.8 52.8Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M28 42 C31 36, 38 34, 42 34", fill: nil, stroke: 0xFF81D4FA, width: 4.0, cap: .round, join: .miter, group: 1),
        KeloShape("M60 66 C74 64, 84 70, 80 78 C75 82, 64 80, 58 75", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 3),
        KeloShape("M106 30 L111 26", fill: nil, stroke: 0xFF29B6F6, width: 3.0, cap: .round, join: .miter, group: 4),
        KeloShape("M112 36 L117 35", fill: nil, stroke: 0xFF29B6F6, width: 3.0, cap: .round, join: .miter, group: 4),
        KeloShape("M98 28 L99 22", fill: nil, stroke: 0xFF29B6F6, width: 3.0, cap: .round, join: .miter, group: 4),
    ]

    private static let browserGroups: [KeloGroup] = [
        KeloGroup(
            name: "pose-front", parent: nil, shapes: 0..<12,
            anim: KeloAnim(
                durationMs: 4500, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 50.0, y: 65.0),
                ops: [.scale],
                tKeys: [0.0, 0.2, 0.24, 0.92, 0.96, 1.0],
                tVals: [1.0, 1.0, 1.0, 1.0, 0.15, 1.0, 0.15, 1.0, 1.0, 1.0, 1.0, 1.0],
                oKeys: [0.0, 0.2, 0.24, 0.92, 0.96, 1.0],
                oVals: [1.0, 1.0, 0.0, 0.0, 1.0, 1.0],
                baseOpacity: 0.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "pose-back", parent: nil, shapes: 12..<45,
            anim: KeloAnim(
                durationMs: 4500, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 50.0, y: 65.0),
                ops: [.scale],
                tKeys: [0.0, 0.21, 0.25, 0.91, 0.95, 1.0],
                tVals: [0.15, 1.0, 0.15, 1.0, 1.0, 1.0, 1.0, 1.0, 0.15, 1.0, 0.15, 1.0],
                oKeys: [0.0, 0.21, 0.25, 0.91, 0.95, 1.0],
                oVals: [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "screen-glow", parent: 1, shapes: 26..<27,
            anim: KeloAnim(
                durationMs: 800, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [],
                tKeys: [],
                tVals: [],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [0.0, 1.0],
                fVals: [0xFFE1F5FE, 0xFFB3E5FC]
            )
        ),
        KeloGroup(
            name: "typing-wing", parent: 1, shapes: 41..<42,
            anim: KeloAnim(
                durationMs: 220, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 62.0, y: 70.0),
                ops: [.rotate, .translate],
                tKeys: [0.0, 1.0],
                tVals: [-6.0, 0.0, 0.0, 0.0, 8.0, 0.0, 0.0, 3.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "click-spark", parent: 1, shapes: 42..<45,
            anim: KeloAnim(
                durationMs: 600, delayMs: 0, easing: .easeOut, alternate: false, origin: KeloPoint(x: 96.0, y: 40.0),
                ops: [.scale],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [0.8, 0.8, 1.15, 1.15, 0.8, 0.8],
                oKeys: [0.0, 0.5, 1.0],
                oVals: [0.2, 1.0, 0.2],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_browser.svg
    public static let browser: KeloMoodArt = KeloMoodArt(mood: .browser, shapes: browserShapes, groups: browserGroups)

    // MARK: kelo_approval.svg

    private static let approvalShapes: [KeloShape] = [
        KeloShape("M54 28 C44 14, 52 8, 58 16 C62 21, 57 26, 54 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M26 66 C16 72, 16 82, 24 80", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M54 28 C30 28, 20 48, 22 72 C23 88, 36 96, 54 96 C72 96, 85 88, 86 72 C88 48, 78 28, 54 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M42 96 L42 104 M37 104 L47 104 M66 96 L66 104 M61 104 L71 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M80 16H100A8 8 0 0 1 108 24V40A8 8 0 0 1 100 48H80A8 8 0 0 1 72 40V24A8 8 0 0 1 80 16Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M90 48 L84 72", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M42 94 L42 104 M37 104 L47 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M66 94 L66 104 M61 104 L71 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M54 29 C42 15, 51 7, 59 15 C63 20, 58 27, 54 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M54 28 C30 28, 20 48, 22 72 C23 88, 36 96, 54 96 C72 96, 85 88, 86 72 C88 48, 78 28, 54 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M54 64 C38 64, 32 78, 35 92 C40 95, 47 96, 54 96 C61 96, 68 95, 73 92 C76 78, 70 64, 54 64 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M54 28 C30 28, 20 48, 22 72 C23 88, 36 96, 54 96 C72 96, 85 88, 86 72 C88 48, 78 28, 54 28 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M23 60 C13 68, 13 82, 23 79", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M35.5 56A4.5 4.5 0 1 0 44.5 56A4.5 4.5 0 1 0 35.5 56Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M63.5 56A4.5 4.5 0 1 0 72.5 56A4.5 4.5 0 1 0 63.5 56Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M37.3 54.2A1.5 1.5 0 1 0 40.3 54.2A1.5 1.5 0 1 0 37.3 54.2Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M65.3 54.2A1.5 1.5 0 1 0 68.3 54.2A1.5 1.5 0 1 0 65.3 54.2Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M48 60A6 3.5 0 1 0 60 60A6 3.5 0 1 0 48 60Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M90 46 L84 72", fill: nil, stroke: 0xFF8D6E63, width: 5.0, cap: .round, join: .miter, group: 1),
        KeloShape("M80 16H100A8 8 0 0 1 108 24V40A8 8 0 0 1 100 48H80A8 8 0 0 1 72 40V24A8 8 0 0 1 80 16Z", fill: 0xFFFFD54F, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M86 26 C86 22, 94 22, 94 27 C94 30, 90 31, 90 34", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 1),
        KeloShape("M87.8 40A2.2 2.2 0 1 0 92.2 40A2.2 2.2 0 1 0 87.8 40Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M78 68 C88 60, 96 66, 86 74 C80 78, 74 74, 78 68 Z", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 1),
    ]

    private static let approvalGroups: [KeloGroup] = [
        KeloGroup(
            name: "kelo-hop", parent: nil, shapes: 0..<23,
            anim: KeloAnim(
                durationMs: 1000, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 55.0, y: 95.0),
                ops: [.translate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [0.0, 0.0, 0.0, -3.0, 0.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "sign-wave", parent: 0, shapes: 18..<23,
            anim: KeloAnim(
                durationMs: 600, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 82.0, y: 70.0),
                ops: [.rotate],
                tKeys: [0.0, 1.0],
                tVals: [-10.0, 0.0, 12.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_approval.svg
    public static let approval: KeloMoodArt = KeloMoodArt(mood: .approval, shapes: approvalShapes, groups: approvalGroups)

    // MARK: kelo_thinking.svg

    private static let thinkingShapes: [KeloShape] = [
        KeloShape("M80 10H98A12 12 0 0 1 110 22V22A12 12 0 0 1 98 34H80A12 12 0 0 1 68 22V22A12 12 0 0 1 80 10Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M69 39A3 3 0 1 0 75 39A3 3 0 1 0 69 39Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M58 34 C48 20, 56 14, 62 22 C66 27, 61 32, 58 34 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M28 70 C18 76, 18 86, 26 84", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M58 34 C34 34, 24 54, 26 76 C27 90, 40 98, 58 98 C76 98, 89 90, 90 76 C92 54, 82 34, 58 34 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M46 98 L46 105 M41 105 L51 105 M70 98 L70 105 M65 105 L75 105", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M46 96 L46 105 M41 105 L51 105", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M70 96 L70 105 M65 105 L75 105", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M58 35 C46 21, 55 13, 63 21 C67 26, 62 33, 58 35 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M58 34 C34 34, 24 54, 26 76 C27 90, 40 98, 58 98 C76 98, 89 90, 90 76 C92 54, 82 34, 58 34 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M58 68 C42 68, 36 82, 39 94 C44 97, 51 98, 58 98 C65 98, 72 97, 77 94 C80 82, 74 68, 58 68 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M58 34 C34 34, 24 54, 26 76 C27 90, 40 98, 58 98 C76 98, 89 90, 90 76 C92 54, 82 34, 58 34 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M27 64 C17 72, 17 86, 27 83", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M40 58A4 4 0 1 0 48 58A4 4 0 1 0 40 58Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M68 58A4 4 0 1 0 76 58A4 4 0 1 0 68 58Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M43.8 56.2A1.4 1.4 0 1 0 46.6 56.2A1.4 1.4 0 1 0 43.8 56.2Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M71.8 56.2A1.4 1.4 0 1 0 74.6 56.2A1.4 1.4 0 1 0 71.8 56.2Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M52.5 63A5.5 3.2 0 1 0 63.5 63A5.5 3.2 0 1 0 52.5 63Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M84 74 C76 64, 62 64, 62 72 C62 78, 76 82, 84 74 Z", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M68.8 39A3.2 3.2 0 1 0 75.2 39A3.2 3.2 0 1 0 68.8 39Z", fill: 0xFFFFFFFF, stroke: 0xFF1A1A1A, width: 3.0, cap: .butt, join: .miter, group: nil),
        KeloShape("M80 10H98A12 12 0 0 1 110 22V22A12 12 0 0 1 98 34H80A12 12 0 0 1 68 22V22A12 12 0 0 1 80 10Z", fill: 0xFFFFFFFF, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .miter, group: nil),
        KeloShape("M75.8 22A3.2 3.2 0 1 0 82.2 22A3.2 3.2 0 1 0 75.8 22Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M85.8 22A3.2 3.2 0 1 0 92.2 22A3.2 3.2 0 1 0 85.8 22Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 2),
        KeloShape("M95.8 22A3.2 3.2 0 1 0 102.2 22A3.2 3.2 0 1 0 95.8 22Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 3),
    ]

    private static let thinkingGroups: [KeloGroup] = [
        KeloGroup(
            name: "kelo-tilt", parent: nil, shapes: 6..<19,
            anim: KeloAnim(
                durationMs: 2600, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 60.0, y: 90.0),
                ops: [.rotate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [-2.0, 0.0, 3.0, 0.0, -2.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "dot1", parent: nil, shapes: 21..<22,
            anim: KeloAnim(
                durationMs: 1500, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.2, 0.5, 1.0],
                tVals: [0.0, 0.0, 0.0, 0.0, 0.0, -2.0, 0.0, 0.0],
                oKeys: [0.0, 0.2, 0.5, 1.0],
                oVals: [0.25, 0.25, 1.0, 0.25],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "dot2", parent: nil, shapes: 22..<23,
            anim: KeloAnim(
                durationMs: 1500, delayMs: 300, easing: .ease, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.2, 0.5, 1.0],
                tVals: [0.0, 0.0, 0.0, 0.0, 0.0, -2.0, 0.0, 0.0],
                oKeys: [0.0, 0.2, 0.5, 1.0],
                oVals: [0.25, 0.25, 1.0, 0.25],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "dot3", parent: nil, shapes: 23..<24,
            anim: KeloAnim(
                durationMs: 1500, delayMs: 600, easing: .ease, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.2, 0.5, 1.0],
                tVals: [0.0, 0.0, 0.0, 0.0, 0.0, -2.0, 0.0, 0.0],
                oKeys: [0.0, 0.2, 0.5, 1.0],
                oVals: [0.25, 0.25, 1.0, 0.25],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_thinking.svg
    public static let thinking: KeloMoodArt = KeloMoodArt(mood: .thinking, shapes: thinkingShapes, groups: thinkingGroups)

    // MARK: kelo_done.svg

    private static let doneShapes: [KeloShape] = [
        KeloShape("M60 30 C50 16, 58 10, 64 18 C68 23, 63 28, 60 30 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M28 62 C14 50, 12 66, 26 74", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M92 62 C106 50, 108 66, 94 74", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M60 30 C36 30, 26 50, 28 74 C29 90, 42 98, 60 98 C78 98, 91 90, 92 74 C94 50, 84 30, 60 30 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M48 98 L45 105 M72 98 L75 105", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M48 96 L45 105 M41 105 L49 105", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M72 96 L75 105 M71 105 L79 105", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M60 31 C48 17, 57 9, 65 17 C69 22, 64 29, 60 31 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M29 64 C13 48, 11 66, 27 74", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M91 64 C107 48, 109 66, 93 74", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M60 30 C36 30, 26 50, 28 74 C29 90, 42 98, 60 98 C78 98, 91 90, 92 74 C94 50, 84 30, 60 30 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M60 66 C44 66, 38 80, 41 94 C46 97, 53 98, 60 98 C67 98, 74 97, 79 94 C82 80, 76 66, 60 66 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M60 30 C36 30, 26 50, 28 74 C29 90, 42 98, 60 98 C78 98, 91 90, 92 74 C94 50, 84 30, 60 30 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M34 64A5 2.8 0 1 0 44 64A5 2.8 0 1 0 34 64Z", fill: 0xFFFF8A80, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M76 64A5 2.8 0 1 0 86 64A5 2.8 0 1 0 76 64Z", fill: 0xFFFF8A80, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M41 58 Q46 51 51 58", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M69 58 Q74 51 79 58", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M54 60 L66 60 Q60 69 54 60 Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 0),
        KeloShape("M22 24 L25 31 L32 34 L25 37 L22 44 L19 37 L12 34 L19 31 Z", fill: 0xFFFFD54F, stroke: 0xFF1A1A1A, width: 2.5, cap: .butt, join: .round, group: 1),
        KeloShape("M98 18 L101 25 L108 28 L101 31 L98 38 L95 31 L88 28 L95 25 Z", fill: 0xFFFFD54F, stroke: 0xFF1A1A1A, width: 2.5, cap: .butt, join: .round, group: 1),
    ]

    private static let doneGroups: [KeloGroup] = [
        KeloGroup(
            name: "kelo-jump", parent: nil, shapes: 0..<18,
            anim: KeloAnim(
                durationMs: 800, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 60.0, y: 95.0),
                ops: [.translate, .scale],
                tKeys: [0.0, 0.45, 1.0],
                tVals: [0.0, 0.0, 1.03, 0.97, 0.0, -9.0, 0.98, 1.03, 0.0, 0.0, 1.03, 0.97],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "sparkle", parent: nil, shapes: 18..<20,
            anim: KeloAnim(
                durationMs: 800, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 60.0, y: 60.0),
                ops: [.scale],
                tKeys: [0.0, 0.45, 1.0],
                tVals: [0.6, 0.6, 1.15, 1.15, 0.6, 0.6],
                oKeys: [0.0, 0.45, 1.0],
                oVals: [0.4, 1.0, 0.4],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_done.svg
    public static let done: KeloMoodArt = KeloMoodArt(mood: .done, shapes: doneShapes, groups: doneGroups)

    // MARK: kelo_error.svg

    private static let errorShapes: [KeloShape] = [
        KeloShape("M60 32 C68 24, 78 28, 76 36 C73 40, 65 36, 60 32 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M30 68 C20 74, 20 84, 28 82", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M90 68 C100 74, 100 84, 92 82", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M60 32 C36 32, 26 52, 28 74 C29 89, 42 97, 60 97 C78 97, 91 89, 92 74 C94 52, 84 32, 60 32 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M48 97 L48 104 M43 104 L53 104 M72 97 L72 104 M67 104 L77 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M96 36 C96 36, 104 46, 104 51 C104 55, 100 58, 96 58 C92 58, 88 55, 88 51 C88 46, 96 36, 96 36 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M48 95 L48 104 M43 104 L53 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M72 95 L72 104 M67 104 L77 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M60 32 C68 24, 78 28, 76 36 C73 40, 65 36, 60 32 Z", fill: 0xFF9CCC65, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M60 32 C36 32, 26 52, 28 74 C29 89, 42 97, 60 97 C78 97, 91 89, 92 74 C94 52, 84 32, 60 32 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M60 66 C44 66, 38 80, 41 93 C46 96, 53 97, 60 97 C67 97, 74 96, 79 93 C82 80, 76 66, 60 66 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M60 32 C36 32, 26 52, 28 74 C29 89, 42 97, 60 97 C78 97, 91 89, 92 74 C94 52, 84 32, 60 32 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M29 64 C19 72, 19 86, 29 83", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M91 64 C101 72, 101 86, 91 83", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M40 54 L49 59 L40 64", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M80 54 L71 59 L80 64", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M54.5 64A5.5 3.2 0 1 0 65.5 64A5.5 3.2 0 1 0 54.5 64Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M96 36 C96 36, 104 46, 104 51 C104 55, 100 58, 96 58 C92 58, 88 55, 88 51 C88 46, 96 36, 96 36 Z", fill: 0xFF4FC3F7, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .round, group: 1),
    ]

    private static let errorGroups: [KeloGroup] = [
        KeloGroup(
            name: "kelo-shiver", parent: nil, shapes: 0..<18,
            anim: KeloAnim(
                durationMs: 400, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 60.0, y: 95.0),
                ops: [.translate, .translate],
                tKeys: [0.0, 1.0],
                tVals: [-1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 2.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "sweat-drop", parent: 0, shapes: 17..<18,
            anim: KeloAnim(
                durationMs: 1200, delayMs: 0, easing: .easeIn, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.8, 1.0],
                tVals: [0.0, 0.0, 0.0, 8.0, 0.0, 8.0],
                oKeys: [0.0, 0.8, 1.0],
                oVals: [1.0, 0.0, 0.0],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_error.svg
    public static let error: KeloMoodArt = KeloMoodArt(mood: .error, shapes: errorShapes, groups: errorGroups)

    // MARK: kelo_offline.svg

    private static let offlineShapes: [KeloShape] = [
        KeloShape("M18 96A44 6 0 1 0 106 96A44 6 0 1 0 18 96Z", fill: 0xFF000000, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M26 68 C14 64, 8 74, 16 80 C21 83, 25 74, 26 68 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M26 68 C26 44, 46 38, 70 40 C86 41, 94 54, 94 70 C94 86, 86 94, 70 94 C46 95, 26 88, 26 68 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M94 60 L105 57 M105 52 L105 62", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M94 78 L105 81 M105 76 L105 86", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M56 76 C60 92, 74 92, 72 78", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M44 38 C36 38, 34 26, 42 22 C48 19, 54 24, 52 32 C51 36, 49 42, 53 45 C47 45, 46 38, 44 38 Z", fill: 0xFFFFFFFF, stroke: 0xFF90A4AE, width: 3.0, cap: .round, join: .round, group: 1),
        KeloShape("M39.5 28A1.5 1.5 0 1 0 42.5 28A1.5 1.5 0 1 0 39.5 28Z", fill: 0xFF90A4AE, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M45.5 28A1.5 1.5 0 1 0 48.5 28A1.5 1.5 0 1 0 45.5 28Z", fill: 0xFF90A4AE, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 1),
        KeloShape("M92 60 L105 57 M105 52 L105 62", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 2),
        KeloShape("M92 78 L105 81 M105 76 L105 86", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 2),
        KeloShape("M26 68 C14 64, 8 74, 16 80 C21 83, 25 74, 26 68 Z", fill: 0xFF9CCC65, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: nil),
        KeloShape("M26 68 C26 44, 46 38, 70 40 C86 41, 94 54, 94 70 C94 86, 86 94, 70 94 C46 95, 26 88, 26 68 Z", fill: 0xFF4FC3F7, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: nil),
        KeloShape("M62 68 C62 54, 76 48, 90 51 C93 56, 94 63, 94 70 C94 77, 93 84, 90 89 C76 92, 62 84, 62 68 Z", fill: 0xFFE1F5FE, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: nil),
        KeloShape("M26 68 C26 44, 46 38, 70 40 C86 41, 94 54, 94 70 C94 86, 86 94, 70 94 C46 95, 26 88, 26 68 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: nil),
        KeloShape("M42 50 L50 58", fill: nil, stroke: 0xFF1A1A1A, width: 3.8, cap: .round, join: .miter, group: nil),
        KeloShape("M50 50 L42 58", fill: nil, stroke: 0xFF1A1A1A, width: 3.8, cap: .round, join: .miter, group: nil),
        KeloShape("M42 76 L50 84", fill: nil, stroke: 0xFF1A1A1A, width: 3.8, cap: .round, join: .miter, group: nil),
        KeloShape("M50 76 L42 84", fill: nil, stroke: 0xFF1A1A1A, width: 3.8, cap: .round, join: .miter, group: nil),
        KeloShape("M52 70 C52 78, 60 78, 60 70 Z", fill: 0xFFFF8A80, stroke: 0xFF1A1A1A, width: 3.0, cap: .butt, join: .round, group: nil),
        KeloShape("M48.5 67A3.5 5.5 0 1 0 55.5 67A3.5 5.5 0 1 0 48.5 67Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: nil),
        KeloShape("M54 74 C58 92, 74 92, 72 76", fill: 0xFF0288D1, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 3),
    ]

    private static let offlineGroups: [KeloGroup] = [
        KeloGroup(
            name: "floor-shadow", parent: nil, shapes: 0..<1,
            anim: KeloAnim(
                durationMs: 2800, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [],
                tKeys: [],
                tVals: [],
                oKeys: [0.0, 1.0],
                oVals: [0.18, 0.18],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "ghost-soul", parent: nil, shapes: 6..<9,
            anim: KeloAnim(
                durationMs: 2600, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate, .scale],
                tKeys: [0.0, 1.0],
                tVals: [0.0, 6.0, 0.8, 0.8, -4.0, -16.0, 1.15, 1.15],
                oKeys: [0.0, 0.35, 1.0],
                oVals: [0.0, 0.95, 0.0],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "dead-twitch", parent: nil, shapes: 9..<11,
            anim: KeloAnim(
                durationMs: 2800, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 94.0, y: 74.0),
                ops: [.rotate, .translate],
                tKeys: [0.0, 0.84, 0.88, 0.92, 0.96, 1.0],
                tVals: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -10.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 8.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "flop-wing", parent: nil, shapes: 21..<22,
            anim: KeloAnim(
                durationMs: 2800, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 62.0, y: 76.0),
                ops: [.rotate],
                tKeys: [0.0, 0.84, 0.88, 1.0],
                tVals: [0.0, 0.0, 0.0, 0.0, -5.0, 0.0, 0.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_offline.svg
    public static let offline: KeloMoodArt = KeloMoodArt(mood: .offline, shapes: offlineShapes, groups: offlineGroups)

    // MARK: kelo_sleeping.svg

    private static let sleepingShapes: [KeloShape] = [
        KeloShape("M56 32 C46 18, 54 12, 60 20 C64 25, 59 30, 56 32 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M26 68 C16 74, 16 84, 24 82", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M86 68 C96 74, 96 84, 88 82", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M56 32 C32 32, 22 52, 24 75 C25 90, 38 98, 56 98 C74 98, 87 90, 88 75 C90 52, 80 32, 56 32 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M44 98 L44 104 M39 104 L49 104 M68 98 L68 104 M63 104 L73 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M44 96 L44 104 M39 104 L49 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M68 96 L68 104 M63 104 L73 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M56 33 C44 19, 53 11, 61 19 C65 24, 60 31, 56 33 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M56 32 C32 32, 22 52, 24 75 C25 90, 38 98, 56 98 C74 98, 87 90, 88 75 C90 52, 80 32, 56 32 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M56 66 C40 66, 34 80, 37 94 C42 97, 49 98, 56 98 C63 98, 70 97, 75 94 C78 80, 72 66, 56 66 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M56 32 C32 32, 22 52, 24 75 C25 90, 38 98, 56 98 C74 98, 87 90, 88 75 C90 52, 80 32, 56 32 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M25 64 C15 72, 15 86, 25 83", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M87 64 C97 72, 97 86, 87 83", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M37 58 Q42 64 47 58", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M65 58 Q70 64 75 58", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M50.5 63A5.5 3.2 0 1 0 61.5 63A5.5 3.2 0 1 0 50.5 63Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M82 32 L94 32 L82 44 L94 44", fill: nil, stroke: 0xFFFFFFFF, width: 9.0, cap: .round, join: .round, group: 1),
        KeloShape("M82 32 L94 32 L82 44 L94 44", fill: nil, stroke: 0xFF5C6BC0, width: 4.0, cap: .round, join: .round, group: 1),
        KeloShape("M94 16 L104 16 L94 26 L104 26", fill: nil, stroke: 0xFFFFFFFF, width: 8.0, cap: .round, join: .round, group: 2),
        KeloShape("M94 16 L104 16 L94 26 L104 26", fill: nil, stroke: 0xFF5C6BC0, width: 3.5, cap: .round, join: .round, group: 2),
    ]

    private static let sleepingGroups: [KeloGroup] = [
        KeloGroup(
            name: "sleep-breathe", parent: nil, shapes: 0..<16,
            anim: KeloAnim(
                durationMs: 3200, delayMs: 0, easing: .easeInOut, alternate: false, origin: KeloPoint(x: 56.0, y: 96.0),
                ops: [.scale, .translate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [1.0, 1.0, 0.0, 0.0, 1.03, 0.97, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "zzz1", parent: nil, shapes: 16..<18,
            anim: KeloAnim(
                durationMs: 2600, delayMs: 0, easing: .easeOut, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate, .scale],
                tKeys: [0.0, 1.0],
                tVals: [0.0, 6.0, 0.8, 0.8, 8.0, -10.0, 1.1, 1.1],
                oKeys: [0.0, 0.4, 1.0],
                oVals: [0.0, 1.0, 0.0],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "zzz2", parent: nil, shapes: 18..<20,
            anim: KeloAnim(
                durationMs: 2600, delayMs: 900, easing: .easeOut, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate, .scale],
                tKeys: [0.0, 1.0],
                tVals: [0.0, 6.0, 0.8, 0.8, 8.0, -10.0, 1.1, 1.1],
                oKeys: [0.0, 0.4, 1.0],
                oVals: [0.0, 1.0, 0.0],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_sleeping.svg
    public static let sleeping: KeloMoodArt = KeloMoodArt(mood: .sleeping, shapes: sleepingShapes, groups: sleepingGroups)

    // MARK: kelo_listening.svg

    private static let listeningShapes: [KeloShape] = [
        KeloShape("M54 28 C44 14, 52 8, 58 16 C62 21, 57 26, 54 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M24 66 C14 72, 14 82, 22 80", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M82 56 C96 42, 98 62, 82 68", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M54 28 C30 28, 20 48, 22 72 C23 88, 36 96, 54 96 C72 96, 85 88, 86 72 C88 48, 78 28, 54 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M42 96 L42 104 M37 104 L47 104 M66 96 L66 104 M61 104 L71 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: 0),
        KeloShape("M42 94 L42 104 M37 104 L47 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M66 94 L66 104 M61 104 L71 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: 0),
        KeloShape("M54 29 C42 15, 51 7, 59 15 C63 20, 58 27, 54 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M54 28 C30 28, 20 48, 22 72 C23 88, 36 96, 54 96 C72 96, 85 88, 86 72 C88 48, 78 28, 54 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: 0),
        KeloShape("M54 64 C38 64, 32 78, 35 92 C40 95, 47 96, 54 96 C61 96, 68 95, 73 92 C76 78, 70 64, 54 64 Z", fill: 0xFFB3E5FC, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M54 28 C30 28, 20 48, 22 72 C23 88, 36 96, 54 96 C72 96, 85 88, 86 72 C88 48, 78 28, 54 28 Z", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M23 60 C13 68, 13 82, 23 79", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M82 58 C96 42, 98 62, 82 68", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .round, group: 0),
        KeloShape("M35.8 56A4.2 4.2 0 1 0 44.2 56A4.2 4.2 0 1 0 35.8 56Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M63.8 56A4.2 4.2 0 1 0 72.2 56A4.2 4.2 0 1 0 63.8 56Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M39.8 54.5A1.4 1.4 0 1 0 42.6 54.5A1.4 1.4 0 1 0 39.8 54.5Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M67.8 54.5A1.4 1.4 0 1 0 70.6 54.5A1.4 1.4 0 1 0 67.8 54.5Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M48.2 60A5.8 3.4 0 1 0 59.8 60A5.8 3.4 0 1 0 48.2 60Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: 0),
        KeloShape("M98 42 Q105 52 98 62", fill: nil, stroke: 0xFFFFFFFF, width: 9.0, cap: .round, join: .miter, group: 1),
        KeloShape("M98 42 Q105 52 98 62", fill: nil, stroke: 0xFF00ACC1, width: 4.0, cap: .round, join: .miter, group: 1),
        KeloShape("M107 35 Q116 52 107 69", fill: nil, stroke: 0xFFFFFFFF, width: 9.0, cap: .round, join: .miter, group: 2),
        KeloShape("M107 35 Q116 52 107 69", fill: nil, stroke: 0xFF00ACC1, width: 4.0, cap: .round, join: .miter, group: 2),
    ]

    private static let listeningGroups: [KeloGroup] = [
        KeloGroup(
            name: "ear-lean", parent: nil, shapes: 0..<18,
            anim: KeloAnim(
                durationMs: 1200, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 54.0, y: 95.0),
                ops: [.rotate],
                tKeys: [0.0, 1.0],
                tVals: [0.0, 0.0, 4.0, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "wave1", parent: nil, shapes: 18..<20,
            anim: KeloAnim(
                durationMs: 1200, delayMs: 0, easing: .ease, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [0.0, 0.0, 2.0, 0.0, 0.0, 0.0],
                oKeys: [0.0, 0.5, 1.0],
                oVals: [0.25, 1.0, 0.25],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "wave2", parent: nil, shapes: 20..<22,
            anim: KeloAnim(
                durationMs: 1200, delayMs: 350, easing: .ease, alternate: false, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 0.5, 1.0],
                tVals: [0.0, 0.0, 2.0, 0.0, 0.0, 0.0],
                oKeys: [0.0, 0.5, 1.0],
                oVals: [0.25, 1.0, 0.25],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_listening.svg
    public static let listening: KeloMoodArt = KeloMoodArt(mood: .listening, shapes: listeningShapes, groups: listeningGroups)

    // MARK: kelo_reading.svg

    private static let readingShapes: [KeloShape] = [
        KeloShape("M60 28 C50 14, 58 8, 64 16 C68 21, 63 26, 60 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M60 28 C36 28, 26 48, 28 72 C29 88, 42 96, 60 96 C78 96, 91 88, 92 72 C94 48, 84 28, 60 28 Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M28 70 L60 75 L92 70 L92 94 L60 99 L28 94Z", fill: 0xFFFFFFFF, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M48 96 L48 104 M43 104 L53 104 M72 96 L72 104 M67 104 L77 104", fill: nil, stroke: 0xFFFFFFFF, width: 10.0, cap: .round, join: .round, group: nil),
        KeloShape("M48 94 L48 104 M43 104 L53 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: nil),
        KeloShape("M72 94 L72 104 M67 104 L77 104", fill: nil, stroke: 0xFF1A1A1A, width: 4.0, cap: .round, join: .miter, group: nil),
        KeloShape("M60 29 C48 15, 57 7, 65 15 C69 20, 64 27, 60 29 Z", fill: 0xFF66BB6A, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: nil),
        KeloShape("M60 28 C36 28, 26 48, 28 72 C29 88, 42 96, 60 96 C78 96, 91 88, 92 72 C94 48, 84 28, 60 28 Z", fill: 0xFF29B6F6, stroke: 0xFF1A1A1A, width: 4.0, cap: .butt, join: .round, group: nil),
        KeloShape("M41.8 56A4.2 4.2 0 1 0 50.2 56A4.2 4.2 0 1 0 41.8 56Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M69.8 56A4.2 4.2 0 1 0 78.2 56A4.2 4.2 0 1 0 69.8 56Z", fill: 0xFF1A1A1A, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M43.7 57A1.3 1.3 0 1 0 46.3 57A1.3 1.3 0 1 0 43.7 57Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M71.7 57A1.3 1.3 0 1 0 74.3 57A1.3 1.3 0 1 0 71.7 57Z", fill: 0xFFFFFFFF, stroke: nil, width: 1.0, cap: .butt, join: .miter, group: 0),
        KeloShape("M54.5 60A5.5 3.2 0 1 0 65.5 60A5.5 3.2 0 1 0 54.5 60Z", fill: 0xFFFFB300, stroke: 0xFF1A1A1A, width: 3.5, cap: .butt, join: .miter, group: nil),
        KeloShape("M28 70 L60 75 L92 70 L92 94 L60 99 L28 94Z", fill: 0xFFFFF8E1, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .round, group: 1),
        KeloShape("M28 74 L60 79 L92 74 L92 94 L60 99 L28 94Z", fill: 0xFFFFCA28, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .round, group: 1),
        KeloShape("M60 75 L60 99", fill: nil, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .miter, group: 1),
        KeloShape("M24 80 C32 76, 38 82, 30 88 C24 90, 20 84, 24 80 Z", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .miter, group: 1),
        KeloShape("M96 80 C88 76, 82 82, 90 88 C96 90, 100 84, 96 80 Z", fill: 0xFF039BE5, stroke: 0xFF1A1A1A, width: 3.8, cap: .butt, join: .miter, group: 1),
    ]

    private static let readingGroups: [KeloGroup] = [
        KeloGroup(
            name: "scan-eyes", parent: nil, shapes: 8..<12,
            anim: KeloAnim(
                durationMs: 1400, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 0.0, y: 0.0),
                ops: [.translate],
                tKeys: [0.0, 1.0],
                tVals: [-2.5, 0.0, 2.5, 0.0],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
        KeloGroup(
            name: "book-bob", parent: nil, shapes: 13..<18,
            anim: KeloAnim(
                durationMs: 1400, delayMs: 0, easing: .easeInOut, alternate: true, origin: KeloPoint(x: 60.0, y: 82.0),
                ops: [.translate],
                tKeys: [0.0, 1.0],
                tVals: [0.0, 0.0, 0.0, -1.5],
                oKeys: [],
                oVals: [],
                baseOpacity: 1.0,
                fKeys: [],
                fVals: []
            )
        ),
    ]

    /// kelo_reading.svg
    public static let reading: KeloMoodArt = KeloMoodArt(mood: .reading, shapes: readingShapes, groups: readingGroups)
}
