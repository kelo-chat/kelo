import SwiftUI
import AVKit
import UniformTypeIdentifiers
import PhotosUI
import KeloCore
import KeloShared

/// Uploads a file from disk with progress (PUT /api/uploads/{name}, streamed from the file, never read whole into memory).
final class FileUploader: @unchecked Sendable {
    private let onProgress: @Sendable (Int64, Int64) -> Void
    init(onProgress: @escaping @Sendable (Int64, Int64) -> Void) { self.onProgress = onProgress }

    /// Sends `file` as `name` (through the API's transport, so through KE2E when the server is pinned); throws ApiError like any call.
    func upload(api: KeloApi, name: String, file: URL, contentType: String?) async throws {
        let report = onProgress
        _ = try await api.upload(name: name, body: .file(file), contentType: contentType, progress: { sent, total in report(sent, total) })
    }
}

/// Upload progress, shown under the button.
@MainActor @Observable final class UploadProgress {
    var sent: Int64 = 0
    var total: Int64 = -1
    var fraction: Double { total > 0 ? min(max(Double(sent) / Double(total), 0), 1) : 0 }
}

/// The files the user has uploaded, and only those (nothing the assistant came with): photos and videos as a grid of previews,
/// documents and sound files as rows. Up to 100 MB per file (the server's limit), checked here before anything is sent.
struct DocumentsPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var docs: [MyUpload]?
    @State private var msg = ""
    @State private var uploading: Task<Void, Never>?
    @State private var progress = UploadProgress()
    @State private var pickFile = false
    @State private var pickPhoto: PhotosPickerItem?

    private static let types: [UTType] = [.pdf, .image, .movie, .audio,
                                          UTType("org.openxmlformats.wordprocessingml.document") ?? .data,
                                          UTType("org.openxmlformats.spreadsheetml.sheet") ?? .data]

    var body: some View {
        SubPage(L(R.string.docs_title), onBack: { dismiss() }) {
            MyCard {
                if uploading != nil {
                    if progress.total > 0 { ProgressView(value: progress.fraction) } else { ProgressView().frame(maxWidth: .infinity) }
                    HStack(alignment: .firstTextBaseline) {
                        MutedText(progress.total > 0
                                  ? L(R.string.docs_upload_progress, MyFormat.storageSize(progress.sent), MyFormat.storageSize(progress.total), Int(progress.fraction * 100))
                                  : L(R.string.docs_uploading), small: true)
                        Spacer(minLength: 8)
                        LinkButton(L(R.string.cancel)) { uploading?.cancel(); uploading = nil }
                    }
                } else {
                    PrimaryButton(L(R.string.docs_upload)) { pickFile = true }
                    let photosLabel = L(R.string.my_ios_docs_from_photos)
                    PhotosPicker(selection: $pickPhoto, matching: .any(of: [.images, .videos])) {
                        Text(photosLabel).font(KeloFont.small).foregroundStyle(Color.accentColor)
                    }
                }
                MutedText(L(R.string.docs_hint), small: true)
                if !msg.isEmpty { MutedText(msg, small: true) }
            }
            if let docs {
                if docs.isEmpty { EmptyNote(L(R.string.docs_empty)) }
                let media = docs.filter(\.isMedia).sorted { $0.modified > $1.modified }
                let files = docs.filter { !$0.isMedia }
                if !media.isEmpty {
                    SectionLabel(L(R.string.docs_media)).padding(.horizontal, -16)
                    MediaGrid(items: media)
                }
                if !files.isEmpty {
                    if !media.isEmpty { SectionLabel(L(R.string.docs_files)).padding(.horizontal, -16) }
                    ForEach(files) { d in
                        let day = L(R.string.docs_size_day, MyFormat.fileSize(d.bytes), MyFormat.date(d.modified, style: .numeric))
                        MenuRow(d.kind == "audio" ? .music : .file, d.name,
                                value: (d.kind == "audio" ? d.duration : nil).map { L(R.string.docs_size_day, MyFormat.clipLength($0), day) } ?? day) {
                            MyNav(app: app).push(.document(d.name))
                        }
                        .padding(.horizontal, -16)
                    }
                }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await load() }
        .fileImporter(isPresented: $pickFile, allowedContentTypes: Self.types) { result in
            if case .success(let url) = result { start(url: url, scoped: true) }
        }
        .onChange(of: pickPhoto) { _, item in if let item { startPhoto(item) } }
    }

    private func load() async {
        docs = MyUpload.list((try? await app.requireApi().request("GET", "/api/uploads")) ?? .null)
    }

    private func startPhoto(_ item: PhotosPickerItem) {
        pickPhoto = nil
        Task {
            guard let file = try? await item.loadTransferable(type: PickedFile.self) else { msg = L(R.string.docs_read_failed); return }
            start(url: file.url, scoped: false, temporary: true)
        }
    }

    private func start(url: URL, scoped: Bool, temporary: Bool = false) {
        msg = ""
        progress.sent = 0; progress.total = -1
        let p = progress
        uploading = Task {
            let access = scoped && url.startAccessingSecurityScopedResource()
            defer {
                if access { url.stopAccessingSecurityScopedResource() }
                if temporary { try? FileManager.default.removeItem(at: url) }
                uploading = nil
            }
            let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? -1
            if size > MediaKind.maxUpload { msg = L(R.string.docs_too_large); return }
            let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
            let name = MediaKind.withSuffix(url.lastPathComponent, mime: type)
            let uploader = FileUploader { sent, total in Task { @MainActor in p.sent = sent; p.total = total } }
            do {
                try await uploader.upload(api: app.requireApi(), name: name, file: url, contentType: type)
                await load()
                msg = L(R.string.docs_uploaded)
            } catch is CancellationError {
            } catch let e as ApiError {
                msg = e.status == 413 ? L(R.string.docs_too_large) : app.message(for: e)
            } catch {
                msg = L(R.string.docs_read_failed)
            }
        }
    }
}

/// A photo or video from the photo library, copied to a temporary file (videos can be large: never held in memory).
struct PickedFile: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .item) { received in
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let dest = dir.appendingPathComponent(received.file.lastPathComponent)
            try FileManager.default.copyItem(at: received.file, to: dest)
            return PickedFile(url: dest)
        }
    }
}

/// The server's previews (GET /api/uploads/{name}/thumb), kept in memory.
@MainActor final class Thumbs {
    static let shared = Thumbs()
    private var cache: [String: PlatformImage] = [:]
    func get(_ name: String, size: Int, app: AppModel) async -> PlatformImage? {
        let key = "\(name)@\(size)"
        if let i = cache[key] { return i }
        guard let data = try? await app.requireApi().download("/api/uploads/\(KeloApi.segment(name))/thumb?size=\(size)"),
              let img = PlatformImage(data: data) else { return nil }
        cache[key] = img
        return img
    }
}

/// Photos and videos as a grid of previews.
struct MediaGrid: View {
    let items: [MyUpload]
    @Environment(AppModel.self) private var app
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: 4)], spacing: 4) {
            ForEach(items) { u in
                Button { MyNav(app: app).push(.document(u.name)) } label: { MediaTile(u: u) }
                    .buttonStyle(.plain)
                    .accessibilityLabel(u.name)
            }
        }
    }
}

struct MediaTile: View {
    let u: MyUpload
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var img: PlatformImage?
    var body: some View {
        Color.clear.aspectRatio(1, contentMode: .fit)
            .overlay {
                if let img { Image(platformImage: img).resizable().scaledToFill() } else { c.bubble }
            }
            .overlay(alignment: .bottomLeading) {
                if u.kind == "video" {
                    HStack(spacing: 3) {
                        Image(kelo: .play).font(.system(size: 10))
                        if let d = u.duration { Text(MyFormat.clipLength(d)).font(KeloFont.label) }
                    }
                    .foregroundStyle(.white).padding(.horizontal, 6).padding(.vertical, 3)
                    .background(Capsule().fill(.black.opacity(0.55))).padding(5)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .task(id: u.name) { img = await Thumbs.shared.get(u.name, size: 512, app: app) }
    }
}

/// One file: a photo on the whole screen, a video or sound file playing, or a document's text. Save the original, or delete.
struct FileViewer: View {
    let name: String
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var text: String?
    @State private var err = ""
    @State private var confirm = false
    @State private var photo: PlatformImage?
    @State private var saved: URL?
    @State private var player: AVPlayer?
    /// Feeds the player through KE2E (kept here: the asset holds its delegate weakly).
    @State private var mediaLoader: E2EMediaLoader?

    private var kind: String { MediaKind.of(name) }

    var body: some View {
        SubPage(name, onBack: { dismiss() }) {
            MyCard {
                HStack {
                    if let saved {
                        ShareLink(item: saved) { Text(L(R.string.docs_download)).font(KeloFont.small).foregroundStyle(c.accent) }
                    } else {
                        LinkButton(L(R.string.docs_download)) { Task { await download() } }
                    }
                    Spacer()
                    LinkButton(L(R.string.delete), danger: true) { confirm = true }
                }
                if !err.isEmpty { ErrorText(err, small: true) }
                switch kind {
                case "image":
                    if let photo { Image(platformImage: photo).resizable().scaledToFit().frame(maxWidth: .infinity) }
                    else { ProgressView().frame(maxWidth: .infinity, minHeight: 200) }
                case "video", "audio":
                    if let player {
                        VideoPlayer(player: player).frame(minHeight: kind == "audio" ? 80 : 240).aspectRatio(kind == "audio" ? nil : 16 / 9, contentMode: .fit)
                    }
                default:
                    if let t = text {
                        Text((try? AttributedString(markdown: t, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(t))
                            .font(KeloFont.body).foregroundStyle(c.ink).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    } else if err.isEmpty {
                        MutedText(L(R.string.docs_reading), small: true)
                    }
                }
            }
        }
        .task {
            let api = try? app.requireApi()
            if kind == "video" || kind == "audio" { player = makePlayer() }
            if kind == "image" {
                if let d = try? await api?.download("/api/uploads/\(KeloApi.segment(name))/file") { photo = PlatformImage(data: d) }
            } else if kind == "document" {
                do { text = try await app.requireApi().get("/api/uploads/\(KeloApi.segment(name))/text", as: UploadText.self).text }
                catch { err = app.message(for: error) }
            }
        }
        .keloConfirm(kind == "document" ? L(R.string.docs_delete_confirm) : L(R.string.docs_delete_media_confirm), isPresented: $confirm, yes: L(R.string.delete)) {
            Task {
                do {
                    try await app.requireApi().request("DELETE", "/api/uploads/\(KeloApi.segment(name))")
                    dismiss()
                } catch { err = app.message(for: error) }
            }
        }
    }

    /// Streams the file with the device token (the token goes in the request headers, never in the address). Through KE2E the
    /// player asks a resource loader, which fetches the byte ranges it wants through the API's encrypted transport.
    private func makePlayer() -> AVPlayer? {
        let path = "/api/uploads/\(KeloApi.segment(name))/file?inline=1"
        guard let api = try? app.requireApi(), let url = URL(string: api.urlString(path)) else { return nil }
        if api.e2e?.state.pin != nil {
            let loader = E2EMediaLoader(api: api, path: path)
            mediaLoader = loader
            return AVPlayer(playerItem: AVPlayerItem(asset: loader.asset()))
        }
        let asset = AVURLAsset(url: url, options: ["AVURLAssetHTTPHeaderFieldsKey": ["Authorization": "Bearer \(api.token)"]])
        return AVPlayer(playerItem: AVPlayerItem(asset: asset))
    }

    /// The original, saved to a temporary file and handed to the share sheet (Save to Files, AirDrop, …).
    private func download() async {
        do {
            let data = try await app.requireApi().download("/api/uploads/\(KeloApi.segment(name))/file")
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
            try data.write(to: url, options: .atomic)
            saved = url
        } catch {
            err = L(R.string.docs_save_failed)
        }
    }
}

/// Video and audio through KE2E: AVPlayer cannot speak KE2E itself, so the asset uses a private URL scheme and this loader answers
/// each request with byte ranges fetched through the API's transport (sealed requests, `Range` inside, 206 answers opened here).
final class E2EMediaLoader: NSObject, AVAssetResourceLoaderDelegate, @unchecked Sendable {
    static let scheme = "kelo-e2e-media"
    /// The most fetched in one sealed request (a player asking for "everything to the end" gets it in pieces).
    static let chunk: Int64 = 2 * 1024 * 1024
    let api: KeloApi
    let path: String
    private let queue = DispatchQueue(label: "chat.kelo.media-loader")

    init(api: KeloApi, path: String) {
        self.api = api
        self.path = path
    }

    func asset() -> AVURLAsset {
        let asset = AVURLAsset(url: URL(string: "\(Self.scheme)://media\(path)")!)
        asset.resourceLoader.setDelegate(self, queue: queue)
        return asset
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForLoadingOfRequestedResource loadingRequest: AVAssetResourceLoadingRequest) -> Bool {
        let request = LoadingRequestBox(loadingRequest)
        Task { await self.serve(request) }
        return true
    }

    private func serve(_ box: LoadingRequestBox) async {
        let req = box.request
        do {
            guard let d = req.dataRequest else {
                // only the type and length are asked for
                if let info = req.contentInformationRequest { fill(info, try await fetch(from: 0, length: 2).1) }
                req.finishLoading()
                return
            }
            var offset = d.currentOffset != 0 ? d.currentOffset : d.requestedOffset
            let end: Int64? = d.requestsAllDataToEndOfResource ? nil : d.requestedOffset + Int64(d.requestedLength)
            var first = true
            while !req.isCancelled {
                let want = min(Self.chunk, (end ?? Int64.max) - offset)
                if want <= 0 { break }
                let (data, meta) = try await fetch(from: offset, length: want)
                if first, let info = req.contentInformationRequest { fill(info, meta) }
                first = false
                if data.isEmpty { break }
                d.respond(with: data)
                offset += Int64(data.count)
                if let total = meta.total, offset >= total { break }
            }
            req.finishLoading()
        } catch {
            req.finishLoading(with: error)
        }
    }

    struct Meta { var type: String?; var total: Int64? }

    private func fetch(from offset: Int64, length: Int64) async throws -> (Data, Meta) {
        var r = try api.makeRequest(path)
        r.setValue("bytes=\(offset)-\(offset + length - 1)", forHTTPHeaderField: "Range")
        let reply = try await api.sendReply(r)
        var meta = Meta(type: reply.headers["content-type"], total: nil)
        if let cr = reply.headers["content-range"], let slash = cr.lastIndex(of: "/") { meta.total = Int64(cr[cr.index(after: slash)...]) }
        if meta.total == nil, reply.status == 200 { meta.total = Int64(reply.body.count) }
        return (reply.body, meta)
    }

    private func fill(_ info: AVAssetResourceLoadingContentInformationRequest, _ meta: Meta) {
        if let t = meta.type?.split(separator: ";").first.map({ String($0).trimmingCharacters(in: .whitespaces) }),
           let ut = UTType(mimeType: t) { info.contentType = ut.identifier }
        if let total = meta.total { info.contentLength = total }
        info.isByteRangeAccessSupported = true
    }
}

/// Carries the loading request into the task (AVFoundation's object is used only from there until it finishes).
private final class LoadingRequestBox: @unchecked Sendable {
    let request: AVAssetResourceLoadingRequest
    init(_ r: AVAssetResourceLoadingRequest) { request = r }
}
