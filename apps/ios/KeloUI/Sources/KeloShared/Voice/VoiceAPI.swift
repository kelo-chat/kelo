import Foundation
import KeloCore

/// The calls behind server voice input (api/voice.py), through the same KeloApi as everything else, so KE2E seals them.
public enum VoiceAPI {
    /// GET /api/voice?lang=…; nil when the server is older than voice input (404).
    public static func view(_ api: KeloApi, lang: String) async throws -> VoiceView? {
        let q = lang.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? lang
        do {
            return try await api.get("/api/voice?lang=\(q)", as: VoiceView.self)
        } catch let e as ApiError where e.status == 404 {
            return nil
        }
    }

    /// POST /api/voice/model: starts (or resumes) the download of `id` and makes it the model in use.
    public static func choose(_ api: KeloApi, id: String) async throws -> VoiceView {
        try await api.post("/api/voice/model", body: .object(["model": .string(id)]), as: VoiceView.self)
    }

    /// POST /api/voice/decline: the owner chose 취소 on the first-tap question.
    public static func decline(_ api: KeloApi) async throws -> VoiceView {
        try await api.post("/api/voice/decline", as: VoiceView.self)
    }

    /// DELETE /api/voice/model: removes the model files on the server.
    public static func remove(_ api: KeloApi) async throws -> VoiceView {
        try await api.delete("/api/voice/model", as: VoiceView.self)
    }

    /// POST /api/voice/transcribe with a WAV (16 kHz mono PCM16). Returns the text, which may be empty. Two minutes can take a while
    /// on a small server, so this call waits up to 120 s.
    public static func transcribe(_ api: KeloApi, wav: Data) async throws -> String {
        var request = try api.makeRequest("/api/voice/transcribe", method: "POST")
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 120
        let body = try await api.upload(request, body: .data(wav))
        return VoiceAnswer.text(from: body)
    }
}
