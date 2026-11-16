import Foundation
import Testing
@testable import KeloCore

/// The generated CSS animations, played at their keyframe times. The first seven mirror the Android app's KeloMotionTest
/// (apps/android/kelo/app/src/test/java/chat/kelo/app/ui/character/KeloMotionTest.kt) case for case, with the same numbers.
@Suite struct KeloMotionTests {
    private let eps = 1e-3

    private func near(_ a: Double, _ b: Double, _ tol: Double) -> Bool { abs(a - b) <= tol }

    /// The transform's numbers at `t` ms (like Android's `transform` + `now`); empty when at rest.
    private func now(_ a: KeloAnim, _ t: Int) -> [Double] { KeloMotion.transformValues(a, KeloMotion.progress(a, t)) ?? [] }

    @Test func idleBreathesBlinksAndSways() throws {
        let anims = KeloArt.idle.anims
        let (breathe, leaf, blink) = (anims[0], anims[1], anims[2])
        let b = try #require(KeloMotion.transformValues(breathe, KeloMotion.progress(breathe, 1_200)))   // 50% of 2.4 s
        #expect(near(b[0], 0, eps)); #expect(near(b[1], -2, eps))
        #expect(near(b[2], 1.02, eps)); #expect(near(b[3], 0.98, eps))
        #expect(near(now(leaf, 0)[0], -4, eps))
        #expect(near(now(leaf, 1_200)[0], 6, eps))
        #expect(near(now(blink, 1_656)[1], 0.1, eps))   // 46% of 3.6 s
        #expect(near(now(blink, 1_000)[1], 1, eps))
    }

    @Test func posesTakeTurns() {
        let anims = KeloArt.working.anims
        let (front, back) = (anims[0], anims[1])
        #expect(near(KeloMotion.opacity(front, KeloMotion.progress(front, 0)), 1, eps))
        #expect(near(KeloMotion.opacity(back, KeloMotion.progress(back, 0)), 0, eps))
        #expect(near(KeloMotion.opacity(front, KeloMotion.progress(front, 1_800)), 0, eps))
        #expect(near(KeloMotion.opacity(back, KeloMotion.progress(back, 1_800)), 1, eps))
    }

    @Test func alternateGoesThereAndBack() throws {
        let bob = try #require(KeloArt.working.anims.first { $0.durationMs == 300 && $0.ops.count == 1 })
        #expect(near(now(bob, 0)[1], 0, eps))
        #expect(near(now(bob, 300)[1], 1.5, eps))   // the second run starts at the end
        #expect(near(now(bob, 600)[1], 0, eps))
        #expect(near(now(bob, 299)[1], 1.5, 0.01))
        #expect(near(now(bob, 450)[1], 0.75, eps))
    }

    @Test func delaysHoldTheElementAtRest() {
        let dots = KeloArt.thinking.anims.filter { $0.durationMs == 1_500 }
        #expect(dots.map(\.delayMs) == [0, 300, 600])
        let dot3 = dots[2]
        #expect(KeloMotion.progress(dot3, 599) == -1)
        #expect(KeloMotion.transformValues(dot3, -1) == nil)
        #expect(KeloMotion.transform(dot3, -1) == .identity)
        #expect(KeloMotion.opacity(dot3, -1) == 1)
        #expect(near(KeloMotion.opacity(dot3, KeloMotion.progress(dot3, 600)), 0.25, eps))
        #expect(near(KeloMotion.opacity(dot3, KeloMotion.progress(dot3, 600 + 750)), 1, eps))   // its 50%
    }

    @Test func shorterTransformListsArePaddedWithIdentity() {
        // sleep-bob: scale(1, 1) → scale(1.03, 0.97) translateY(1px)
        let bob = KeloArt.sleeping.anims[0]
        #expect(bob.ops == [.scale, .translate])
        #expect(near(now(bob, 0)[3], 0, eps))
        #expect(near(now(bob, 1_600)[3], 1, eps))
    }

    @Test func fillMixesInSrgb() throws {
        let glow = try #require(KeloArt.browser.anims.first { !$0.fKeys.isEmpty })
        let c = try #require(KeloMotion.fill(glow, 0))
        #expect(near(c.red, Double(0xE1) / 255, 0.01))
        let d = try #require(KeloMotion.fill(glow, 1))
        #expect(near(d.red, Double(0xB3) / 255, 0.01))
    }

    @Test func sparklesTurnAboutTheMiddle() throws {
        let sparkle = try #require(KeloArt.done.anims.last)
        #expect(sparkle.origin == KeloPoint(x: 60, y: 60))
    }

    // ---- beyond the Android tests ----

    @Test func transformIsAboutTheOrigin() throws {
        // the leaf at 50%: rotate(6deg) about 60,28 keeps the origin in place and turns a point 10 px right of it by 6°
        let leaf = KeloArt.idle.anims[1]
        let m = KeloMotion.transform(leaf, KeloMotion.progress(leaf, 1_200))
        let o = m.apply(KeloPoint(x: 60, y: 28))
        #expect(near(o.x, 60, 1e-9) && near(o.y, 28, 1e-9))
        let p = m.apply(KeloPoint(x: 70, y: 28))
        #expect(near(p.x, 60 + 10 * cos(6 * Double.pi / 180), 1e-4))
        #expect(near(p.y, 28 + 10 * sin(6 * Double.pi / 180), 1e-4))   // clockwise on screen, as CSS
        // the breath at 50%: translateY(-2px) scale(1.02, 0.98) about 60,95 — the CSS order: translate applied after the scale
        let breathe = KeloArt.idle.anims[0]
        let b = KeloMotion.transform(breathe, KeloMotion.progress(breathe, 1_200))
        let q = b.apply(KeloPoint(x: 70, y: 45))
        #expect(near(q.x, 60 + 10 * 1.02, 1e-3))
        #expect(near(q.y, 95 - 50 * 0.98 - 2, 1e-3))
    }

    @Test func timingFunctionsMatchCss() {
        #expect(KeloEasing.linear.transform(0.3) == 0.3)
        // well-known values of the CSS curves at x = 0.5
        #expect(near(KeloEasing.ease.transform(0.5), 0.8024, 1e-3))
        #expect(near(KeloEasing.easeIn.transform(0.5), 0.3153, 1e-3))
        #expect(near(KeloEasing.easeOut.transform(0.5), 0.6847, 1e-3))
        #expect(near(KeloEasing.easeInOut.transform(0.5), 0.5, 1e-6))
        for e in [KeloEasing.ease, .easeIn, .easeOut, .easeInOut] {
            #expect(e.transform(0) == 0 && e.transform(1) == 1)
        }
    }

    @Test func framesFollowTheGroups() {
        let f = KeloMotion.frame(.working, at: 1_800)
        #expect(f.groups.count == KeloArt.working.groups.count)
        #expect(near(f.groups[0].opacity, 0, eps))   // facing front: hidden at the desk
        // a shape inside body-bob inside pose-back moves with both
        let art = KeloArt.working
        let bob = art.groups.firstIndex { $0.name == "body-bob" }!
        let shape = art.groups[bob].shapes.lowerBound
        #expect(f.transform(ofShape: shape) == f.groups[art.groups[bob].parent!].transform * f.groups[bob].transform)
        // the screen glow's fill is animated; other shapes keep their own
        let browser = KeloArt.browser
        let glow = browser.groups.firstIndex { $0.name == "screen-glow" }!
        let g = KeloMotion.frame(.browser, at: 0)
        #expect(g.fill(ofShape: browser.groups[glow].shapes.lowerBound)?.argb == 0xFFE1F5FE)
        #expect(KeloMotion.stillFrame(.done).timeMs == 360)
    }

    @Test func constantsMatchAndroid() {
        #expect(KeloMotion.fadeMs == 150)
        #expect(KeloMotion.calmFrameMs == 32)
        #expect(KeloMotion.calm == [.idle, .sleeping, .offline, .reading])
        #expect(KeloMotion.frameStepMs(for: .working) == 0)
        #expect(KeloMood.allCases.map(KeloMotion.stillAt) == [0, 1_800, 1_800, 0, 750, 360, 0, 0, 1_900, 600, 0])
    }
}

@Suite struct KeloArtTests {
    @Test func everyMoodHasItsDrawing() {
        #expect(KeloMood.allCases.count == 11)
        for mood in KeloMood.allCases {
            let art = KeloArt.of(mood)
            #expect(art.mood == mood)
            #expect(!art.shapes.isEmpty && !art.groups.isEmpty)
        }
        #expect(KeloMood.allCases.map { KeloArt.of($0).anims.count }.reduce(0, +) == 35)   // offline art redrawn 2026-11-08 (4 animated parts)
        #expect(KeloMood.allCases.map { KeloArt.of($0).shapes.count }.reduce(0, +) == 270)
    }

    @Test func everyPathParses() throws {
        for mood in KeloMood.allCases {
            for (i, s) in KeloArt.of(mood).shapes.enumerated() {
                #expect(!s.d.isEmpty)
                let els = try KeloPath.parse(s.d)
                guard case .move = els.first else { Issue.record("\(mood) shape \(i) does not start with a move"); continue }
                let box = try #require(KeloPath.controlBounds(els))
                #expect(box.min.x >= -5 && box.min.y >= -5 && box.max.x <= 125 && box.max.y <= 125, "\(mood) shape \(i): \(box)")
                #expect(s.fill != nil || s.stroke != nil, "\(mood) shape \(i) paints nothing")
            }
        }
    }

    @Test func circlesBecomeArcsThatClose() throws {
        // dot1 of thinking: a circle at 79,22 r 3.2 → two half arcs → four cubic quarters
        let els = try KeloPath.parse("M75.8 22A3.2 3.2 0 1 0 82.2 22A3.2 3.2 0 1 0 75.8 22Z")
        #expect(els.count == 6)
        let box = try #require(KeloPath.controlBounds(els))
        #expect(abs(box.min.y - (22 - 3.2 * 1.1)) < 0.5 && abs(box.max.y - (22 + 3.2 * 1.1)) < 0.5)
        guard case let .cubic(_, _, end) = els[4] else { Issue.record("expected a curve"); return }
        #expect(end == KeloPoint(x: 75.8, y: 22))
        #expect((try? KeloPath.parse("L1 2")) == nil)
        #expect(try KeloPath.parse("m1 1 2 0 0 2z") == [.move(KeloPoint(x: 1, y: 1)), .line(KeloPoint(x: 3, y: 1)),
                                                       .line(KeloPoint(x: 3, y: 3)), .close])
    }

    @Test func everyAnimationHasItsShapes() {
        for mood in KeloMood.allCases {
            let art = KeloArt.of(mood)
            for (g, group) in art.groups.enumerated() {
                #expect(!group.shapes.isEmpty && group.shapes.upperBound <= art.shapes.count, "\(mood) \(group.name)")
                #expect(art.shapes[group.shapes].contains { $0.group == g }, "\(mood) \(group.name) holds no shape of its own")
                if let p = group.parent {
                    #expect(p < g && art.groups[p].shapes.contains(group.shapes.lowerBound)
                            && group.shapes.upperBound <= art.groups[p].shapes.upperBound, "\(mood) \(group.name)")
                }
                let a = group.anim
                #expect(a.durationMs > 0 && a.delayMs >= 0)
                #expect(a.tVals.count == a.tKeys.count * a.ops.count * 2)
                #expect(a.oVals.count == a.oKeys.count && a.fVals.count == a.fKeys.count)
                for keys in [a.tKeys, a.oKeys, a.fKeys] where !keys.isEmpty {
                    #expect(keys.first == 0 && keys.last == 1 && keys == keys.sorted(), "\(mood) \(group.name)")
                }
            }
            for (i, s) in art.shapes.enumerated() {
                if let g = s.group { #expect(g < art.groups.count && art.groups[g].shapes.contains(i)) }
            }
            // walking the items draws every shape exactly once, in order
            var seen: [Int] = []
            func walk(_ g: Int?) {
                for item in art.items(in: g) {
                    switch item {
                    case let .shape(i): seen.append(i); #expect(art.shapes[i].group == g)
                    case let .group(c): #expect(art.groups[c].parent == g); walk(c)
                    }
                }
            }
            walk(nil)
            #expect(seen == Array(art.shapes.indices), "\(mood)")
        }
    }
}
