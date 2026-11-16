import UIKit
import Social
import UniformTypeIdentifiers

/// "Send to Kelo" in the share sheet (Android's ACTION_SEND into Kelo): the system's compose sheet with the shared text (editable);
/// Post leaves the text and at most one file in the App Group for the app, which puts the text in the chat's draft and uploads the
/// file the next time the chat shows. Nothing is sent to the server from here (the extension has no session).
final class ShareViewController: SLComposeServiceViewController {
    private static let appGroup = "group.chat.kelo.app"
    private var fileURL: URL?
    private var sharedText = ""

    override func viewDidLoad() {
        super.viewDidLoad()
        Task { await load() }
    }

    override func isContentValid() -> Bool { !(contentText ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || fileURL != nil }

    override func didSelectPost() {
        let text = String(((contentText ?? "") + (sharedText.isEmpty || (contentText ?? "").contains(sharedText) ? "" : "\n" + sharedText))
            .trimmingCharacters(in: .whitespacesAndNewlines).prefix(20_000))
        if let base = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: Self.appGroup) {
            let dir = base.appendingPathComponent("ShareInbox", isDirectory: true)
            try? FileManager.default.removeItem(at: dir)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            var o: [String: Any] = ["text": text, "at": Date().timeIntervalSince1970]
            if let f = fileURL, (try? FileManager.default.copyItem(at: f, to: dir.appendingPathComponent(f.lastPathComponent))) != nil {
                o["file"] = f.lastPathComponent
            }
            if let data = try? JSONSerialization.data(withJSONObject: o) { try? data.write(to: dir.appendingPathComponent("inbox.json"), options: .atomic) }
        }
        extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
    }

    override func configurationItems() -> [Any]! { [] }

    /// A web address joins the text; a file (PDF, Word, Excel, audio, video, picture) is copied to keep.
    private func load() async {
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        for item in items {
            for p in item.attachments ?? [] {
                if p.hasItemConformingToTypeIdentifier(UTType.url.identifier), !p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                    if let u = try? await p.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL { sharedText = u.absoluteString }
                } else if fileURL == nil, let type = [UTType.pdf, .movie, .audio, .image, .spreadsheet, .data].first(where: { p.hasItemConformingToTypeIdentifier($0.identifier) }) {
                    fileURL = await copy(p, type: type)
                }
            }
        }
        await MainActor.run { validateContent() }
    }

    private func copy(_ p: NSItemProvider, type: UTType) async -> URL? {
        await withCheckedContinuation { cont in
            _ = p.loadFileRepresentation(forTypeIdentifier: type.identifier) { url, _ in
                guard let url else { cont.resume(returning: nil); return }
                let dst = FileManager.default.temporaryDirectory.appendingPathComponent(url.lastPathComponent)
                try? FileManager.default.removeItem(at: dst)
                cont.resume(returning: (try? FileManager.default.copyItem(at: url, to: dst)) != nil ? dst : nil)
            }
        }
    }
}
