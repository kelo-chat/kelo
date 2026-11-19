import Foundation
import KeloCore

/// View holder for Kelo's own voice (brief §4.6): [refresh] reads GET /api/voice/speech;
/// polls every 2 seconds while downloading.
@MainActor @Observable
public final class SpeechModel {
    public private(set) var view: SpeechView?
    public private(set) var error: String?
    private let api: () -> KeloApi?
    private var watching = false
    private var pollTask: Task<Void, Never>?

    public init(api: @escaping () -> KeloApi?, initialView: SpeechView? = nil) {
        self.api = api
        self.view = initialView
    }

    public func refresh() {
        Task { await read() }
    }

    public func choose() { act { try await VoiceAPI.speechChoose($0) } }
    public func decline() { act { try await VoiceAPI.speechDecline($0) } }
    public func remove() { act { try await VoiceAPI.speechRemove($0) } }
    public func prefs(voice: Int? = nil, speed: Double? = nil) {
        act { try await VoiceAPI.speechPrefs($0, voice: voice, speed: speed) }
    }

    private func act(_ call: @escaping (KeloApi) async throws -> SpeechView) {
        guard let client = api() else { return }
        Task {
            do {
                self.view = try await call(client)
                self.error = nil
                watchIfDownloading()
            } catch let e as ApiError {
                self.error = (e.code?.isEmpty == false ? e.code : nil) ?? e.detail
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func read() async {
        guard let client = api() else { return }
        do {
            self.view = try await VoiceAPI.speechView(client)
            self.error = nil
        } catch let e as ApiError {
            self.error = (e.code?.isEmpty == false ? e.code : nil) ?? e.detail
        } catch {
            self.error = error.localizedDescription
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
                if let fresh = try? await VoiceAPI.speechView(client) {
                    self.view = fresh
                }
            }
            self?.watching = false
        }
    }
}
