import Foundation
import KeloCore

/// The owner's voice input state on this server, shared by the mic and My → 음성 입력.
/// [refresh] reads GET /api/voice; while a model is downloading it reads again every two seconds.
@MainActor @Observable
public final class VoiceModel {
    public private(set) var view: VoiceView?
    public private(set) var error: VoiceProblem?
    private let api: () -> KeloApi?
    private let lang: () -> String
    private var watching = false
    private var pollTask: Task<Void, Never>?

    public init(api: @escaping () -> KeloApi?, lang: @escaping () -> String = { "ko" }, initialView: VoiceView? = nil) {
        self.api = api
        self.lang = lang
        self.view = initialView
    }

    public func refresh() {
        Task { await load() }
    }

    public func choose(_ id: String) { act { try await VoiceAPI.choose($0, id: id) } }
    public func decline() { act { try await VoiceAPI.decline($0) } }
    public func remove() { act { try await VoiceAPI.remove($0) } }

    private func act(_ call: @escaping (KeloApi) async throws -> VoiceView) {
        guard let client = api() else { return }
        Task {
            do {
                self.view = try await call(client)
                self.error = nil
                watchIfDownloading()
            } catch let e as ApiError {
                self.error = voiceProblem(httpStatus: e.isNetwork ? nil : e.status, code: e.code)
            } catch {
                self.error = .failed
            }
        }
    }

    /// Reads GET /api/voice now (a call awaits this before it chooses its ears).
    public func load() async {
        guard let client = api() else { return }
        do {
            self.view = try await VoiceAPI.view(client, lang: lang())
            self.error = nil
        } catch let e as ApiError {
            self.error = voiceProblem(httpStatus: e.isNetwork ? nil : e.status, code: e.code)
        } catch {
            self.error = .failed
        }
        watchIfDownloading()
    }

    private func watchIfDownloading() {
        guard !watching, view?.state == "downloading" else { return }
        watching = true
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard let self, self.view?.state == "downloading" else { break }
                guard let client = self.api() else { break }
                if let fresh = try? await VoiceAPI.view(client, lang: self.lang()) {
                    self.view = fresh
                }
            }
            self?.watching = false
        }
    }
}

/// Puts the spoken words into the field where the caret was, with a space only where two words would touch.
public enum VoiceInsert {
    public struct Anchor: Equatable, Sendable {
        public let before: String
        public let after: String
        public init(before: String, after: String) {
            self.before = before
            self.after = after
        }
    }

    public struct Merged: Equatable, Sendable {
        public let text: String
        public let cursor: Int
        public init(text: String, cursor: Int) {
            self.text = text
            self.cursor = cursor
        }
    }

    public static func merge(anchor: Anchor, spoken: String) -> Merged {
        let words = spoken.trimmingCharacters(in: .whitespacesAndNewlines)
        if words.isEmpty {
            return Merged(text: anchor.before + anchor.after, cursor: anchor.before.count)
        }
        let sep: String
        if !anchor.before.isEmpty, let last = anchor.before.last, !last.isWhitespace {
            sep = " "
        } else {
            sep = ""
        }
        let head = anchor.before + sep + words
        return Merged(text: head + anchor.after, cursor: head.count)
    }
}
