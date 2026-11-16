import SwiftUI
#if os(iOS)
import AVFoundation
import UIKit
#endif

/// The QR scanner for a pairing link (Android start/QrScanScreen.kt): the camera full screen, a frame, a hint; a code that is not
/// a Kelo link says so and keeps scanning. Asks for the camera first; when refused, offers the Settings app.
public struct QrScanScreen: View {
    let accept: (String) -> Bool
    let onScanned: (String) -> Void
    let onClose: () -> Void

    public init(accept: @escaping (String) -> Bool, onScanned: @escaping (String) -> Void, onClose: @escaping () -> Void) {
        self.accept = accept
        self.onScanned = onScanned
        self.onClose = onClose
    }
    @Environment(\.kelo) private var c
    @State private var permission: CameraPermission = .unknown
    @State private var notKelo = false
    @State private var failed = false

    enum CameraPermission { case unknown, granted, denied }

    public var body: some View {
        ZStack {
            c.media.ignoresSafeArea()
            switch permission {
            case .granted where !failed:
                #if os(iOS)
                CameraScanner(onCode: { raw in
                    if accept(raw) { Haptics.success(); onScanned(raw) } else { notKelo = true }
                }, onFailure: { failed = true })
                .ignoresSafeArea()
                RoundedRectangle(cornerRadius: 24).stroke(c.onMedia.opacity(0.9), lineWidth: 3)
                    .frame(width: 240, height: 240)
                    .accessibilityHidden(true)
                #else
                message(L(R.string.scan_camera_failed))
                #endif
            case .granted:
                message(L(R.string.scan_camera_failed))
            case .denied:
                VStack(spacing: 14) {
                    message(L(R.string.scan_permission_needed))
                    PrimaryButton(L(R.string.scan_open_settings)) { DeviceInfo.openAppSettings() }.frame(maxWidth: 320)
                }
                .padding(24)
            case .unknown:
                VStack(spacing: 14) {
                    message(L(R.string.scan_permission_needed))
                    PrimaryButton(L(R.string.scan_permission_allow)) { Task { await ask() } }.frame(maxWidth: 320)
                }
                .padding(24)
            }
            VStack {
                HStack {
                    Text(L(R.string.scan_title)).font(KeloFont.titleLarge).foregroundStyle(c.onMedia)
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    Button(action: onClose) { KeloIconView(.close, size: 26, tint: c.onMedia).frame(width: 44, height: 44) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(L(R.string.close))
                }
                .padding(.horizontal, 16).padding(.top, 8)
                Spacer()
                if permission == .granted && !failed {
                    Text(notKelo ? L(R.string.scan_not_kelo) : L(R.string.scan_hint))
                        .font(KeloFont.body).foregroundStyle(c.onMedia).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 24).padding(.bottom, 32)
                }
            }
        }
        .task { await check() }
    }

    private func message(_ text: String) -> some View {
        Text(text).font(KeloFont.bodyLarge).foregroundStyle(c.onMedia).multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 24)
    }

    private func check() async {
        #if os(iOS)
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: permission = .granted
        case .notDetermined: await ask()
        default: permission = .denied
        }
        #else
        permission = .granted
        #endif
    }

    private func ask() async {
        #if os(iOS)
        permission = await AVCaptureDevice.requestAccess(for: .video) ? .granted : .denied
        #endif
    }
}

#if os(iOS)
/// The camera preview with a QR metadata reader. Nothing is recorded or saved; frames are only read for codes.
private struct CameraScanner: UIViewRepresentable {
    let onCode: (String) -> Void
    let onFailure: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        let session = AVCaptureSession()
        guard let device = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else {
            DispatchQueue.main.async { onFailure() }
            return view
        }
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { DispatchQueue.main.async { onFailure() }; return view }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(context.coordinator, queue: .main)
        output.metadataObjectTypes = [.qr]
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        context.coordinator.session = session
        DispatchQueue.global(qos: .userInitiated).async { session.startRunning() }
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}

    static func dismantleUIView(_ uiView: PreviewView, coordinator: Coordinator) {
        let s = coordinator.session
        DispatchQueue.global(qos: .userInitiated).async { s?.stopRunning() }
    }

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        let onCode: (String) -> Void
        var session: AVCaptureSession?
        private var last: String?
        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
            guard let raw = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue, raw != last else { return }
            last = raw
            onCode(raw)
        }
    }
}
#endif
