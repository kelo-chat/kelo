// Renders the iPhone app icon (1024 × 1024 PNG) with the app's own native character renderer: Kelo at rest (the idle mood's first
// frame) on the launcher icon's warm cream (Android gen_art.py --icon), a little larger than Android's adaptive icon since iOS
// does not crop the icon to a circle: the 120-unit art at 0.76 of a 108-unit canvas, centred, a touch low. macOS only; run from
// apps/ios/KeloUI:
//   swift run kelo-icon ../Kelo/App/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png
#if os(macOS)
import SwiftUI
import AppKit
import KeloShared

@MainActor func render(to path: String) throws {
    let side: CGFloat = 1024
    let unit = side / 108
    let view = ZStack(alignment: .topLeading) {
        LinearGradient(colors: [Color(hex: 0xFFF8E1), Color(hex: 0xFFE8B0)], startPoint: .top, endPoint: .bottom)
        KeloCharacterStill(.idle, timeMs: 0)
            .frame(width: 120 * 0.76 * unit, height: 120 * 0.76 * unit)
            .offset(x: 8.4 * unit, y: 11.4 * unit)
    }
    .frame(width: side, height: side)
    let r = ImageRenderer(content: view)
    r.scale = 1
    guard let cg = r.cgImage else { throw NSError(domain: "kelo-icon", code: 1) }
    // the App Store icon has no alpha channel
    let ctx = CGContext(data: nil, width: Int(side), height: Int(side), bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: side, height: side))
    let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    guard let png = rep.representation(using: .png, properties: [:]) else { throw NSError(domain: "kelo-icon", code: 2) }
    try png.write(to: URL(fileURLWithPath: path))
    print("wrote \(path)")
}

let out = CommandLine.arguments.dropFirst().first ?? "AppIcon-1024.png"
try MainActor.assumeIsolated { try render(to: out) }
#endif
