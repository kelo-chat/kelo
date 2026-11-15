import Foundation
import KeloCore
import KeloShared

// The attachment tray: picking, uploading in the background and taking a chip off again.

extension ChatModel {
    /// Puts a chip in the tray at once and uploads in the background. `prepare` reads the pick (and makes a photo smaller) off the
    /// main thread; the uploader refuses a file over what this route takes before a byte is sent.
    public func attach(image: Bool, label: String,
                       prepare: @escaping @Sendable () async throws -> PreparedUpload) {
        guard let api = api() else { return }
        seq += 1
        let id = "a\(seq)"
        attachments.append(Attachment(id: id, name: label, image: image))
        let uploader = self.uploader
        uploads[id] = Task { [weak self] in
            var prepared: PreparedUpload?
            do {
                let p = try await prepare()
                prepared = p
                self?.change(id) { $0.name = p.name; $0.total = max(p.length, 0); $0.thumbnail = p.thumbnail }
                let path = try await uploader.upload(api: api, name: p.name, source: p.source, type: p.type) { sent, total in
                    Task { @MainActor in self?.change(id) { $0.sent = sent; $0.total = total } }
                }
                let stored = UploadRules.stored(path, fallback: p.name)
                self?.change(id) { $0.state = .done; $0.stored = stored; $0.sent = $0.total }
            } catch is CancellationError {
            } catch let e as UploadTooLarge {
                self?.fail(id, .tooLarge(e.limit))
            } catch let e as ApiError {
                self?.fail(id, UploadRules.error(e))
            } catch {
                self?.fail(id, .unreadable)
            }
            self?.uploads[id] = nil
            prepared?.cleanup()
        }
    }

    private func change(_ id: String, _ f: (inout Attachment) -> Void) {
        guard let i = attachments.firstIndex(where: { $0.id == id }) else { return }
        f(&attachments[i])
    }

    private func fail(_ id: String, _ e: AttachError) {
        change(id) { $0.state = .failed; $0.error = e }
        onEvent(.error)
    }

    /// The chip's ×: a running upload is cancelled; one already up is taken off the server again.
    public func removeAttachment(_ id: String) {
        guard let a = attachments.first(where: { $0.id == id }) else { return }
        uploads.removeValue(forKey: id)?.cancel()
        attachments.removeAll { $0.id == id }
        if a.state == .done, let stored = a.stored, let api = api() {
            Task { _ = try? await api.request("DELETE", "/api/uploads/\(KeloApi.segment(stored))") }
        }
    }
}
