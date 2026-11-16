import SwiftUI
import KeloCore
import KeloShared
#if os(iOS)
import VisionKit
import AVFoundation
#endif

/// [카드 스캔] under the card number (Android wallet/CardScan.kt CardScanButton): fills the number and expiry from the camera, read on
/// this iPhone only (VisionKit's live text, `DataScannerViewController`). The CVC is never read; nothing scanned is kept, logged or
/// sent, and the scan screen is kept out of screenshots. Only the camera permission the app already asks for is used. No button on a
/// phone that cannot scan (no camera, an older chip, the simulator).
struct CardScanButton: View {
    let onResult: (CardScanResult) -> Void
    @Environment(\.kelo) private var c
    @State private var scanning = false
    @State private var note: Note?

    enum Note { case filled, nothing, cameraNeeded, cameraFailed }

    var body: some View {
        if CardScanner.available {
            VStack(alignment: .leading, spacing: 4) {
                Button { start() } label: {
                    HStack(spacing: 8) {
                        KeloIconView(.camera, size: 18, tint: c.ink)
                        Text(L(R.string.card_scan_button)).font(KeloFont.body).foregroundStyle(c.ink)
                    }
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .frame(minHeight: 44)
                    .background(Capsule().fill(c.card))
                    .overlay(Capsule().stroke(c.line, lineWidth: 1))
                }
                .buttonStyle(PressShrinkStyle())
                .accessibilityIdentifier("kelo.wallet.scan")
                switch note {
                case .filled: MutedText(L(R.string.card_scan_filled), small: true)
                case .nothing: MutedText(L(R.string.card_scan_nothing), small: true)
                case .cameraFailed: MutedText(L(R.string.card_scan_camera_failed), small: true)
                case .cameraNeeded:
                    MutedText(L(R.string.card_scan_camera_needed), small: true)
                    LinkButton(L(R.string.scan_open_settings)) { CardScanner.openSettings() }
                case nil: EmptyView()
                }
            }
            .keloFullScreen(isPresented: $scanning) {
                CardScanScreen(onClose: { scanning = false }, onResult: { r in
                    scanning = false
                    note = .filled
                    Haptics.success()
                    onResult(r)
                }, onFailed: { scanning = false; note = .cameraFailed })
            }
        }
    }

    private func start() {
        note = nil
        Task {
            switch await CardScanner.cameraAccess() {
            case true: scanning = true
            case false: note = .cameraNeeded
            }
        }
    }
}

/// Whether this iPhone can scan, the camera permission, and the system settings.
@MainActor enum CardScanner {
    static var available: Bool {
        #if os(iOS)
        return DataScannerViewController.isSupported && UIImagePickerController.isSourceTypeAvailable(.camera)
        #else
        return false
        #endif
    }

    /// The camera permission (asked the first time, with the app's existing camera text).
    static func cameraAccess() async -> Bool {
        #if os(iOS)
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video)
        default: return false
        }
        #else
        return false
        #endif
    }

    static func openSettings() {
        #if os(iOS)
        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
        #endif
    }
}

/// The camera scan over the whole screen: a card-shaped frame to hold the card in, the title and what to do at the top. Frames are
/// read on the phone; only their text lines go to `CardScanText`, and the scan ends once `CardScanAgreement`'s frames agree.
private struct CardScanScreen: View {
    let onClose: () -> Void
    let onResult: (CardScanResult) -> Void
    let onFailed: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        ZStack(alignment: .top) {
            c.media.ignoresSafeArea()
            #if os(iOS)
            CardScanView(onResult: onResult, onFailed: onFailed).ignoresSafeArea()
            #endif
            GeometryReader { g in
                let w = g.size.width * 0.86
                RoundedRectangle(cornerRadius: 16).stroke(c.onMedia.opacity(0.85), lineWidth: 3)
                    .frame(width: w, height: w / 1.586)
                    .position(x: g.size.width / 2, y: g.size.height / 2)
            }
            .allowsHitTesting(false)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) {
                    Button(action: onClose) { KeloIconView(.close, size: 22, tint: c.onMedia).frame(width: 44, height: 44) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(L(R.string.close))
                    Text(L(R.string.card_scan_title)).font(KeloFont.titleMedium).foregroundStyle(c.onMedia)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Text(L(R.string.card_scan_hint)).font(KeloFont.body).foregroundStyle(c.onMedia)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 12).padding(.bottom, 8)
            }
            .padding(8)
            .background(c.media.opacity(0.6))
        }
        .keloSensitive()
    }
}

#if os(iOS)
/// VisionKit's live-text scanner, text only, high quality; every update's transcripts are one "frame" of lines.
private struct CardScanView: UIViewControllerRepresentable {
    let onResult: (CardScanResult) -> Void
    let onFailed: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onResult: onResult) }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: [.text()], qualityLevel: .accurate, recognizesMultipleItems: true,
                                           isHighFrameRateTrackingEnabled: false, isPinchToZoomEnabled: true, isGuidanceEnabled: false,
                                           isHighlightingEnabled: false)
        vc.delegate = context.coordinator
        DispatchQueue.main.async {
            do { try vc.startScanning() } catch { onFailed() }
        }
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}

    static func dismantleUIViewController(_ vc: DataScannerViewController, coordinator: Coordinator) {
        vc.stopScanning()
        coordinator.agreement.clear()
    }

    @MainActor final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onResult: (CardScanResult) -> Void
        let agreement = CardScanAgreement()
        private var done = false

        init(onResult: @escaping (CardScanResult) -> Void) { self.onResult = onResult }

        func dataScanner(_ s: DataScannerViewController, didAdd added: [RecognizedItem], allItems: [RecognizedItem]) { frame(allItems) }
        func dataScanner(_ s: DataScannerViewController, didUpdate updated: [RecognizedItem], allItems: [RecognizedItem]) { frame(allItems) }

        private func frame(_ items: [RecognizedItem]) {
            guard !done else { return }
            let lines = items.compactMap { item -> String? in
                if case .text(let t) = item { return t.transcript }
                return nil
            }.flatMap { $0.components(separatedBy: .newlines) }
            guard let r = agreement.offer(CardScanText.read(lines)) else { return }
            done = true
            onResult(r)
        }
    }
}
#endif
