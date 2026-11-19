import Testing
import SwiftUI
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

private final class RenderCallChat: CallChat, @unchecked Sendable {
    var callBusy: Bool { false }
    func sendFromCall(text: String, callId: String, amend: Bool) -> Bool { true }
    func takeBackFromCall(callId: String) -> Bool { false }
    func followFromCall(turnId: String, callId: String) -> Bool { true }
}

private final class RenderCallAudio: CallAudio, @unchecked Sendable {
    var speakerOn: Bool = true
    var echoCancel: Bool = true
    var hasSpeakerSwitch: Bool = true
    var onInterrupted: (@Sendable () -> Void)? = nil
    func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool { true }
    func stopMic() {}
    func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async {}
    func stopPlayback() {}
    func chime(on: Bool) {}
    func release() {}
}

/// The in-call screen with a few lines of the call, at 320 and 412 points (KELO_RENDER_DIR=<folder> writes PNGs to look at).
@Suite(.serialized) struct InCallRenderTests {
    @MainActor private func render(_ session: CallSession, width: CGFloat) -> CGSize {
        let content = InCallView(session: session, agentName: "Kelo", onMinimize: {})
            .keloTheme()
            .frame(width: width, height: 760)
            .background(Color.white)
        let r = ImageRenderer(content: content)
        r.scale = 2
        if let dir = ProcessInfo.processInfo.environment["KELO_RENDER_DIR"], let img = r.nsImage,
           let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("in-call-\(Int(width)).png"))
        }
        return r.nsImage?.size ?? .zero
    }

    @MainActor @Test func inCallScreenFitsPhoneWidthsWithTheCallsLines() {
        for width: CGFloat in [320, 412] {
            let session = CallSession(
                callId: "c_render", topic: "", fromAgent: false, chat: RenderCallChat(), api: { nil }, audio: RenderCallAudio(),
                stt: VoiceModel(api: { nil }, initialView: VoiceView(state: "ready", runtime: "ready")),
                speech: SpeechModel(api: { nil }, initialView: nil), phoneListener: nil, device: nil,
                appLang: { "ko" }, clock: { 1_000_000 }
            )
            session.accept()
            session.addMineLine("내일 오전 10시에 치과 예약이 있는지 확인해줄래?")
            session.addKeloText("네, 내일 오전 10시에 치과 예약이 있어요. 알림도 30분 전에 드릴게요.")
            session.closeKeloLine()
            session.addMineLine("고마워")
            let size = render(session, width: width)
            #expect(size.width == width, "in-call screen at \(width) pt")
            #expect(size.height > 0, "in-call screen height at \(width) pt")
            session.hangUp()
        }
    }
}
