import Foundation
import KeloCore

public enum SpeakProblem: String, Equatable, Sendable {
    case notReady = "NOT_READY"
    case lang = "LANG"
    case busy = "BUSY"
    case offline = "OFFLINE"
    case failed = "FAILED"
}

public func speakProblem(_ e: ApiError) -> SpeakProblem {
    if e.isNetwork {
        return .offline
    }
    switch e.code {
    case "voice_speech_not_ready": return .notReady
    case "voice_speech_lang": return .lang
    case "voice_busy": return .busy
    default: return .failed
    }
}

public struct SpeakException: Error, Equatable, Sendable {
    public let problem: SpeakProblem
    public let detail: String

    public init(problem: SpeakProblem, detail: String = "") {
        self.problem = problem
        self.detail = detail
    }

    public init(problem: SpeakProblem, cause: ApiError) {
        self.problem = problem
        self.detail = cause.detail
    }
}

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

    /// GET /api/voice/speech: null when the server does not support speech out (404).
    public static func speechView(_ api: KeloApi) async throws -> SpeechView? {
        do {
            return try await api.get("/api/voice/speech", as: SpeechView.self)
        } catch let e as ApiError where e.status == 404 {
            return nil
        }
    }

    /// POST /api/voice/speech/model: starts (or resumes) download of Supertonic 3.
    public static func speechChoose(_ api: KeloApi) async throws -> SpeechView {
        try await api.post("/api/voice/speech/model", body: .object([:]), as: SpeechView.self)
    }

    /// POST /api/voice/speech/decline: owner declined Kelo voice model on first call.
    public static func speechDecline(_ api: KeloApi) async throws -> SpeechView {
        try await api.post("/api/voice/speech/decline", body: .object([:]), as: SpeechView.self)
    }

    /// DELETE /api/voice/speech/model: removes the speech model files on the server.
    public static func speechRemove(_ api: KeloApi) async throws -> SpeechView {
        try await api.delete("/api/voice/speech/model", as: SpeechView.self)
    }

    /// PUT /api/voice/speech/prefs: update voice sid (0-9) and/or speed (0.7-1.5).
    public static func speechPrefs(_ api: KeloApi, voice: Int? = nil, speed: Double? = nil) async throws -> SpeechView {
        var body: [String: JSON] = [:]
        if let voice { body["voice"] = .number(Double(voice)) }
        if let speed { body["speed"] = .number(speed) }
        return try await api.put("/api/voice/speech/prefs", body: .object(body), as: SpeechView.self)
    }

    /// POST /api/voice/speak: returns WAV PCM16 mono 44.1 kHz audio bytes.
    public static func speak(_ api: KeloApi, text: String, lang: String, voice: Int? = nil, speed: Double? = nil) async throws -> Data {
        var bodyDict: [String: JSON] = [
            "text": .string(text),
            "lang": .string(lang)
        ]
        if let voice { bodyDict["voice"] = .number(Double(voice)) }
        if let speed { bodyDict["speed"] = .number(speed) }

        var request = try api.makeRequest("/api/voice/speak", method: "POST")
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 40
        request.httpBody = Data(JSON.object(bodyDict).compactString.utf8)
        do {
            return try await api.send(request)
        } catch let e as ApiError {
            throw SpeakException(problem: speakProblem(e), cause: e)
        }
    }

    /// POST /api/calls/{call_id}/answered: starts turn with hidden note, returns turn id.
    public static func answered(_ api: KeloApi, callId: String, timezone: String? = nil, topic: String? = nil) async throws -> String {
        var body: [String: JSON] = [:]
        if let timezone { body["timezone"] = .string(timezone) }
        if let topic { body["topic"] = .string(topic) }
        let res = try await api.request("POST", "/api/calls/\(KeloApi.segment(callId))/answered", body: .object(body))
        return res["id"]?.stringValue ?? res["id"]?.text ?? ""
    }
}
