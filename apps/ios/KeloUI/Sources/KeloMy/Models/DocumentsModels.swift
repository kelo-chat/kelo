import SwiftUI
import AVKit
import UniformTypeIdentifiers
import KeloCore
import KeloShared

/// The server's previews (GET /api/uploads/{name}/thumb), kept in memory for as long as the page that owns them lives.
@MainActor final class DocumentThumbnails {
    private let api: KeloApi
    private var cache: [String: PlatformImage] = [:]

    init(api: KeloApi) { self.api = api }

    func get(_ name: String, size: Int) async -> PlatformImage? {
        let key = "\(name)@\(size)"
        if let i = cache[key] { return i }
        guard let data = try? await api.download("/api/uploads/\(KeloApi.segment(name))/thumb?size=\(size)"),
              let img = PlatformImage(data: data) else { return nil }
        cache[key] = img
        return img
    }
}

/// 마이 → 문서: the files the person uploaded, and the upload under way with its progress. Up to 100 MB per file, checked before
/// anything is sent. Sending is streamed from the file (never read whole into memory).
@MainActor @Observable final class DocumentsModel {
    private(set) var docs: [MyUpload]?
    private(set) var message = ""
    private(set) var uploading = false
    private(set) var sent: Int64 = 0
    private(set) var total: Int64 = -1
    @ObservationIgnored let thumbnails: DocumentThumbnails
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored var upload: Task<Void, Never>?

    init(api: KeloApi) { self.api = api; thumbnails = DocumentThumbnails(api: api) }

    var fraction: Double { total > 0 ? min(max(Double(sent) / Double(total), 0), 1) : 0 }

    /// A newer read replaces a running one, so an old list never covers a newer one.
    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            let list = MyUpload.list((try? await api.request("GET", "/api/uploads")) ?? .null)
            if !Task.isCancelled { docs = list }
        }
        loading = task
        await task.value
    }

    func readFailed() { message = L(R.string.docs_read_failed) }

    func cancelUpload() { upload?.cancel() }

    /// A file picked from the Files app (`scoped`: a security-scoped address) or copied from the photo library (`temporary`).
    func start(url: URL, scoped: Bool, temporary: Bool = false) {
        message = ""
        sent = 0; total = -1
        uploading = true
        upload = Task { @MainActor [self] in
            let access = scoped && url.startAccessingSecurityScopedResource()
            defer {
                if access { url.stopAccessingSecurityScopedResource() }
                if temporary { try? FileManager.default.removeItem(at: url) }
                uploading = false
            }
            let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
            let name = MediaKind.withSuffix(url.lastPathComponent, mime: type)
            do {
                // the same uploader as the chat's tray: one request, or in parts where this route has a limit; refuses over what it takes
                _ = try await api.uploader.upload(name: name, source: .file(url), type: type, progress: { [weak self] sent, total in
                    Task { @MainActor in self?.sent = sent; self?.total = total }
                })
                await load()
                message = L(R.string.docs_uploaded)
            } catch is CancellationError {
            } catch let e as UploadTooLarge {
                message = L(R.string.upload_too_large_limit, UploadSizeText.of(e.limit))
            } catch let e as ApiError {
                message = ApiMessage.text(e)
            } catch {
                message = L(R.string.docs_read_failed)
            }
        }
    }
}

/// One uploaded file: a photo, a video or sound file playing, or a document's text; the original to save, and delete.
@MainActor @Observable final class FileViewerModel {
    let name: String
    private(set) var text: String?
    private(set) var error = ""
    private(set) var photo: PlatformImage?
    private(set) var saved: URL?
    private(set) var player: AVPlayer?
    /// Set when the file was deleted: the page closes.
    private(set) var deleted = false
    @ObservationIgnored private let api: KeloApi
    /// Feeds the player through KE2E (kept here: the asset holds its delegate weakly).
    @ObservationIgnored private var mediaLoader: E2EMediaLoader?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, name: String) { self.api = api; self.name = name }

    var kind: String { MediaKind.of(name) }
    private var base: String { "/api/uploads/\(KeloApi.segment(name))" }

    func load() async {
        if kind == "video" || kind == "audio" { player = makePlayer() }
        if kind == "image" {
            if let d = try? await api.download("\(base)/file") { photo = PlatformImage(data: d) }
        } else if kind == "document" {
            do { text = try await api.get("\(base)/text", as: UploadText.self).text }
            catch { self.error = ApiMessage.text(error) }
        }
    }

    /// Streams the file with the device token (the token goes in the request headers, never in the address). Through KE2E the
    /// player asks a resource loader, which fetches the byte ranges it wants through the API's encrypted transport.
    private func makePlayer() -> AVPlayer? {
        let path = "\(base)/file?inline=1"
        guard let url = URL(string: api.urlString(path)) else { return nil }
        if api.e2e?.state.pin != nil {
            let loader = E2EMediaLoader(api: api, path: path)
            mediaLoader = loader
            return AVPlayer(playerItem: AVPlayerItem(asset: loader.asset()))
        }
        let asset = AVURLAsset(url: url, options: ["AVURLAssetHTTPHeaderFieldsKey": ["Authorization": "Bearer \(api.token)"]])
        return AVPlayer(playerItem: AVPlayerItem(asset: asset))
    }

    /// The original, saved to a temporary file and handed to the share sheet (Save to Files, AirDrop, …).
    func download() {
        jobs.run { [self] in
            do {
                let data = try await api.download("\(base)/file")
                let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
                try data.write(to: url, options: .atomic)
                saved = url
            } catch {
                self.error = L(R.string.docs_save_failed)
            }
        }
    }

    func delete() {
        jobs.run { [self] in
            do { try await api.request("DELETE", base); deleted = true } catch { self.error = ApiMessage.text(error) }
        }
    }
}
