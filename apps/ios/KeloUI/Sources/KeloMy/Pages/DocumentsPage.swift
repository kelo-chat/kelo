import SwiftUI
import AVKit
import UniformTypeIdentifiers
import PhotosUI
import KeloCore
import KeloShared

/// The files the user has uploaded, and only those (nothing the assistant came with): photos and videos as a grid of previews,
/// documents and sound files as rows. Up to 100 MB per file (the server's limit), checked here before anything is sent.
struct DocumentsPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var model: DocumentsModel
    @State private var pickFile = false
    @State private var pickPhoto: PhotosPickerItem?

    init(api: KeloApi) { _model = State(initialValue: DocumentsModel(api: api)) }

    private static let types: [UTType] = [.pdf, .image, .movie, .audio,
                                          UTType("org.openxmlformats.wordprocessingml.document") ?? .data,
                                          UTType("org.openxmlformats.spreadsheetml.sheet") ?? .data]

    var body: some View {
        SubPage(L(R.string.docs_title), onBack: { dismiss() }) {
            MyCard {
                if model.uploading {
                    if model.total > 0 { ProgressView(value: model.fraction) } else { ProgressView().frame(maxWidth: .infinity) }
                    HStack(alignment: .firstTextBaseline) {
                        MutedText(model.total > 0
                                  ? L(R.string.docs_upload_progress, MyFormat.storageSize(model.sent), MyFormat.storageSize(model.total), Int(model.fraction * 100))
                                  : L(R.string.docs_uploading), small: true)
                        Spacer(minLength: 8)
                        LinkButton(L(R.string.cancel)) { model.cancelUpload() }
                    }
                } else {
                    PrimaryButton(L(R.string.docs_upload)) { pickFile = true }
                    let photosLabel = L(R.string.my_ios_docs_from_photos)
                    PhotosPicker(selection: $pickPhoto, matching: .any(of: [.images, .videos])) {
                        Text(photosLabel).font(KeloFont.small).foregroundStyle(Color.accentColor)
                    }
                }
                MutedText(L(R.string.docs_hint), small: true)
                if !model.message.isEmpty { MutedText(model.message, small: true) }
            }
            if let docs = model.docs {
                if docs.isEmpty { EmptyNote(L(R.string.docs_empty)) }
                let media = docs.filter(\.isMedia).sorted { $0.modified > $1.modified }
                let files = docs.filter { !$0.isMedia }
                if !media.isEmpty {
                    SectionLabel(L(R.string.docs_media)).padding(.horizontal, -16)
                    MediaGrid(items: media, thumbnails: model.thumbnails)
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
        .task { await model.load() }
        .fileImporter(isPresented: $pickFile, allowedContentTypes: Self.types) { result in
            if case .success(let url) = result { model.start(url: url, scoped: true) }
        }
        .onChange(of: pickPhoto) { _, item in if let item { startPhoto(item) } }
    }

    /// The photo library hands the picked item over as a temporary file; sending it is the model's.
    private func startPhoto(_ item: PhotosPickerItem) {
        pickPhoto = nil
        Task { @MainActor in
            guard let file = try? await item.loadTransferable(type: PickedFile.self) else { model.readFailed(); return }
            model.start(url: file.url, scoped: false, temporary: true)
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

/// Photos and videos as a grid of previews.
struct MediaGrid: View {
    let items: [MyUpload]
    let thumbnails: DocumentThumbnails
    @Environment(AppModel.self) private var app
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: 4)], spacing: 4) {
            ForEach(items) { u in
                Button { MyNav(app: app).push(.document(u.name)) } label: { MediaTile(u: u, thumbnails: thumbnails) }
                    .buttonStyle(.plain)
                    .accessibilityLabel(u.name)
            }
        }
    }
}

struct MediaTile: View {
    let u: MyUpload
    let thumbnails: DocumentThumbnails
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
            .task(id: u.name) { img = await thumbnails.get(u.name, size: 512) }
    }
}

/// One file: a photo on the whole screen, a video or sound file playing, or a document's text. Save the original, or delete.
struct FileViewer: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: FileViewerModel
    @State private var confirm = false

    init(api: KeloApi, name: String) { _model = State(initialValue: FileViewerModel(api: api, name: name)) }

    var body: some View {
        let kind = model.kind
        SubPage(model.name, onBack: { dismiss() }) {
            MyCard {
                HStack {
                    if let saved = model.saved {
                        ShareLink(item: saved) { Text(L(R.string.docs_download)).font(KeloFont.small).foregroundStyle(c.accent) }
                    } else {
                        LinkButton(L(R.string.docs_download)) { model.download() }
                    }
                    Spacer()
                    LinkButton(L(R.string.delete), danger: true) { confirm = true }
                }
                if !model.error.isEmpty { ErrorText(model.error, small: true) }
                switch kind {
                case "image":
                    if let photo = model.photo { Image(platformImage: photo).resizable().scaledToFit().frame(maxWidth: .infinity) }
                    else { ProgressView().frame(maxWidth: .infinity, minHeight: 200) }
                case "video", "audio":
                    if let player = model.player {
                        VideoPlayer(player: player).frame(minHeight: kind == "audio" ? 80 : 240).aspectRatio(kind == "audio" ? nil : 16 / 9, contentMode: .fit)
                    }
                default:
                    if let t = model.text {
                        Text((try? AttributedString(markdown: t, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(t))
                            .font(KeloFont.body).foregroundStyle(c.ink).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    } else if model.error.isEmpty {
                        MutedText(L(R.string.docs_reading), small: true)
                    }
                }
            }
        }
        .task { await model.load() }
        .onChange(of: model.deleted) { _, gone in if gone { dismiss() } }
        .keloConfirm(kind == "document" ? L(R.string.docs_delete_confirm) : L(R.string.docs_delete_media_confirm), isPresented: $confirm, yes: L(R.string.delete)) {
            model.delete()
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
