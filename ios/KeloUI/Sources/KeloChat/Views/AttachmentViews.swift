import SwiftUI
import PhotosUI
import UniformTypeIdentifiers
import ImageIO
import KeloCore
import KeloShared
#if os(iOS)
import UIKit
import AVFoundation
#endif

enum AttachSource { case photo, camera, file }

/// "+": 사진 (the system photo picker, photos and videos, no permission), 카메라, 파일.
struct AttachSheet: View {
    let onPick: (AttachSource) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                Text(L(R.string.attach_sheet_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink).padding(.top, 20).padding(.bottom, 6)
                row(.image, L(R.string.attach_photo), L(R.string.attach_photo_hint)) { onPick(.photo) }
                if AttachmentPickers.cameraAvailable {
                    row(.camera, L(R.string.attach_camera), L(R.string.attach_camera_hint)) { onPick(.camera) }
                }
                row(.file, L(R.string.attach_file), L(R.string.attach_file_hint)) { onPick(.file) }
            }
            .padding(.horizontal, 20).padding(.bottom, 16)
        }
    }

    private func row(_ icon: KeloIcon, _ label: String, _ hint: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 16) {
                KeloIconView(icon, size: 24, tint: c.ink)
                VStack(alignment: .leading, spacing: 2) {
                    Text(label).font(KeloFont.bodyLarge).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    Text(hint).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// The picked photos and files above the composer: a picture or a file icon, the name, how far the upload is (or why it
/// failed), and ×.
struct AttachmentTray: View {
    let items: [Attachment]
    let onRemove: (String) -> Void

    var body: some View {
        if !items.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(items) { a in AttachmentChip(a: a) { onRemove(a.id) } }
                }
                .padding(.horizontal, 12).padding(.top, 6)
            }
        }
    }
}

private struct AttachmentChip: View {
    let a: Attachment
    let onRemove: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            Group {
                if let t = a.thumbnail, let img = PlatformImage(data: t) {
                    Image(platformImage: img).resizable().scaledToFill()
                } else {
                    KeloIconView(a.image ? .image : .file, size: 20, tint: c.muted)
                }
            }
            .frame(width: 36, height: 36)
            .background(RoundedRectangle(cornerRadius: 8).fill(c.bubble))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 1) {
                Text(TokenText.middle(a.name, max: 24)).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(1)
                Text(status).font(KeloFont.caption).foregroundStyle(a.state == .failed ? c.danger : c.muted).lineLimit(2)
            }
            .frame(maxWidth: 200, alignment: .leading)
            Button(action: onRemove) { KeloIconView(.close, size: 18, tint: c.muted).frame(width: 32, height: 32) }
                .buttonStyle(.plain)
                .accessibilityLabel(L(a.state == .uploading ? R.string.attach_cancel : R.string.attach_remove, a.name))
        }
        .padding(.leading, 6).padding(.trailing, 2).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
    }

    private var status: String {
        switch a.state {
        case .uploading: a.progress.map { L(R.string.attach_progress, Int($0 * 100)) } ?? L(R.string.attach_preparing)
        case .done: L(R.string.attach_done)
        case .failed: ChatWords.attachError(a.error)
        }
    }
}

/// The three pickers "+" opens, and turning what they give into uploads (each chip appears at once and uploads in the background).
@MainActor @Observable
final class AttachmentPickers {
    var photos = false
    var files = false
    var camera = false
    var photoItems: [PhotosPickerItem] = []

    static var cameraAvailable: Bool {
        #if os(iOS)
        return UIImagePickerController.isSourceTypeAvailable(.camera)
        #else
        return false
        #endif
    }

    /// What the file picker offers: the documents the agent reads, plus audio and video (web: .pdf, .docx, .xlsx, .xlsm).
    static let documentTypes: [UTType] = [
        .pdf, UTType("org.openxmlformats.wordprocessingml.document") ?? .data,
        UTType("org.openxmlformats.spreadsheetml.sheet") ?? .data,
        UTType(filenameExtension: "xlsm") ?? .data, .movie, .audio,
    ]

    @MainActor func open(_ s: AttachSource, model: ChatModel) {
        // the sheet closes first; the picker opens once it has gone
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [self] in
            switch s {
            case .photo: photos = true
            case .file: files = true
            case .camera: openCamera(model: model)
            }
        }
    }

    /// The camera only after its permission (asked once; refused: the reason shows in the chat).
    @MainActor private func openCamera(model: ChatModel) {
        #if os(iOS)
        guard Self.cameraAvailable else { model.showError(.cameraMissing); return }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: camera = true
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { ok in
                Task { @MainActor in if ok { self.camera = true } else { model.showError(.cameraDenied) } }
            }
        default: model.showError(.cameraDenied)
        }
        #else
        model.showError(.cameraMissing)
        #endif
    }

    @MainActor func modifiers(model: ChatModel) -> some ViewModifier { PickersModifier(pickers: self, model: model) }

    @MainActor func take(_ items: [PhotosPickerItem], model: ChatModel) {
        photoItems = []
        for item in items {
            let isVideo = item.supportedContentTypes.contains { $0.conforms(to: .movie) }
            let label = L(R.string.attach_photo)
            model.attach(image: !isVideo, label: label) {
                if isVideo {
                    guard let movie = try await item.loadTransferable(type: PickedMovie.self) else { throw AttachmentReadError() }
                    return try FilePrep.prepare(movie.url, temporary: true)
                }
                guard let data = try await item.loadTransferable(type: Data.self) else { throw AttachmentReadError() }
                return try PhotoPrep.prepare(data, display: nil)
            }
        }
    }

    @MainActor func take(file url: URL, model: ChatModel) {
        model.attach(image: false, label: url.lastPathComponent.isEmpty ? L(R.string.attach_file) : url.lastPathComponent) {
            // a picked document is read only while it goes up (copied out of its sandbox first, then deleted)
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? -1
            if UploadRules.tooLarge(size) { throw UploadTooLarge() }
            let copy = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "-" + url.lastPathComponent)
            try FileManager.default.copyItem(at: url, to: copy)
            return try FilePrep.prepare(copy, temporary: true, display: url.lastPathComponent)
        }
    }

    #if os(iOS)
    @MainActor func take(cameraImage: UIImage, model: ChatModel) {
        let data = cameraImage.jpegData(compressionQuality: 1) ?? Data()
        model.attach(image: true, label: L(R.string.attach_photo)) { try PhotoPrep.prepare(data, display: nil) }
    }
    #endif
}

struct AttachmentReadError: Error {}

private struct PickersModifier: ViewModifier {
    @Bindable var pickers: AttachmentPickers
    let model: ChatModel

    func body(content: Content) -> some View {
        content
            .photosPicker(isPresented: $pickers.photos, selection: $pickers.photoItems, maxSelectionCount: UploadRules.maxPhotos,
                          matching: .any(of: [.images, .videos]))
            .onChange(of: pickers.photoItems) { _, items in if !items.isEmpty { pickers.take(items, model: model) } }
            .fileImporter(isPresented: $pickers.files, allowedContentTypes: AttachmentPickers.documentTypes) { result in
                if case .success(let url) = result { pickers.take(file: url, model: model) }
            }
            #if os(iOS)
            .fullScreenCover(isPresented: $pickers.camera) {
                CameraPicker { image in
                    pickers.camera = false
                    if let image { pickers.take(cameraImage: image, model: model) }
                }
                .ignoresSafeArea()
            }
            #endif
    }
}

/// A video from the photo picker, copied to a temporary file (never held whole in memory).
struct PickedMovie: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in SentTransferredFile(movie.url) } importing: { received in
            let copy = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "-" + received.file.lastPathComponent)
            try FileManager.default.copyItem(at: received.file, to: copy)
            return PickedMovie(url: copy)
        }
    }
}

/// A file as it is: its size checked against the limit before anything is sent.
enum FilePrep {
    static func prepare(_ url: URL, temporary: Bool, display: String? = nil) throws -> PreparedUpload {
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? -1
        if UploadRules.tooLarge(size) {
            if temporary { try? FileManager.default.removeItem(at: url) }
            throw UploadTooLarge()
        }
        let type = UTType(filenameExtension: url.pathExtension)
        var name = display ?? url.lastPathComponent
        if temporary, display == nil, let dash = name.firstIndex(of: "-"), name.count > 37 { name = String(name[name.index(after: dash)...]) }
        return PreparedUpload(name: name, source: .file(url), type: type?.preferredMIMEType, length: size,
                              temporary: temporary ? url : nil)
    }
}

/// A photo: upright (the camera's orientation applied), at most 2048 px on the long edge, as JPEG without the camera's metadata
/// (the place it was taken), with a small thumbnail for the chip. ImageIO, so the same code runs on every Apple platform.
enum PhotoPrep {
    static func prepare(_ data: Data, display: String?) throws -> PreparedUpload {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { throw AttachmentReadError() }
        func scaled(_ edge: Int) -> CGImage? {
            CGImageSourceCreateThumbnailAtIndex(src, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: edge,
            ] as CFDictionary)
        }
        guard let image = scaled(UploadRules.maxEdge) else { throw AttachmentReadError() }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else { throw AttachmentReadError() }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: UploadRules.jpegQuality] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw AttachmentReadError() }
        let bytes = out as Data
        if UploadRules.tooLarge(Int64(bytes.count)) { throw UploadTooLarge() }
        var thumb: Data?
        if let t = scaled(160) {
            let td = NSMutableData()
            if let d = CGImageDestinationCreateWithData(td, UTType.jpeg.identifier as CFString, 1, nil) {
                CGImageDestinationAddImage(d, t, [kCGImageDestinationLossyCompressionQuality: 0.7] as CFDictionary)
                if CGImageDestinationFinalize(d) { thumb = td as Data }
            }
        }
        let name = UploadRules.jpegName(display, now: Int64(Date().timeIntervalSince1970 * 1000))
        return PreparedUpload(name: name, source: .data(bytes), type: "image/jpeg", length: Int64(bytes.count), thumbnail: thumb)
    }
}

#if os(iOS)
/// The system camera (UIImagePickerController). Needs NSCameraUsageDescription in the app's Info.plist.
struct CameraPicker: UIViewControllerRepresentable {
    let onDone: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = .camera
        p.delegate = context.coordinator
        return p
    }

    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onDone) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let onDone: (UIImage?) -> Void
        init(_ onDone: @escaping (UIImage?) -> Void) { self.onDone = onDone }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            onDone(info[.originalImage] as? UIImage)
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { onDone(nil) }
    }
}
#endif
