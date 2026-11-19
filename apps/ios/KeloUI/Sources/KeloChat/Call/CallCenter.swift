import Foundation
import Combine
import Observation
import os
import KeloCore
import KeloShared

private let callLogger = Logger(subsystem: "chat.kelo.app", category: "KeloCall")

/// One CallCenter per signed-in app (brief §4.9).
@MainActor @Observable
public final class CallCenter: @unchecked Sendable {
    public private(set) var session: CallSession?
    public var isMinimized: Bool = false

    @ObservationIgnored private let api: @Sendable () -> KeloApi?
    @ObservationIgnored private let stt: VoiceModel
    @ObservationIgnored private let speech: SpeechModel
    @ObservationIgnored private let audioFactory: @Sendable () -> CallAudio?
    @ObservationIgnored private let deviceFactory: @Sendable () -> (any SpeechEngine)?
    @ObservationIgnored private let phoneListenerFactory: @Sendable () -> (any PhoneListener)?
    @ObservationIgnored private let appLang: @Sendable () -> String
    @ObservationIgnored private let clock: @Sendable () -> Int64
    @ObservationIgnored private let log: @Sendable (String) -> Void

    @ObservationIgnored private weak var currentChat: ChatModel?
    @ObservationIgnored private let availableLock = NSLock()
    @ObservationIgnored nonisolated(unsafe) private var _available = false

    public var available: Bool {
        computeAvailability()
    }

    public nonisolated var isAvailable: Bool {
        availableLock.withLock { _available }
    }

    @discardableResult
    public func computeAvailability() -> Bool {
        let ok = CallAvailability.of(
            stt: stt.view,
            micAvailable: true,
            phoneRecognizer: true
        )
        availableLock.withLock { _available = ok }
        return ok
    }

    public init(
        api: @escaping @Sendable () -> KeloApi?,
        stt: VoiceModel,
        speech: SpeechModel,
        audio: @escaping @Sendable () -> CallAudio? = { IOSCallAudio() },
        device: @escaping @Sendable () -> (any SpeechEngine)? = { IOSSpeechEngine() },
        phoneListener: @escaping @Sendable () -> (any PhoneListener)? = { IOSPhoneListener() },
        appLang: @escaping @Sendable () -> String = { "ko" },
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        log: @escaping @Sendable (String) -> Void = { _ in }
    ) {
        self.api = api
        self.stt = stt
        self.speech = speech
        self.audioFactory = audio
        self.deviceFactory = device
        self.phoneListenerFactory = phoneListener
        self.appLang = appLang
        self.clock = clock
        self.log = log
        computeAvailability()
    }

    /// Collects chat.turnEvents: CallOffer -> ring(fromAgent = true).
    public func attach(chat: ChatModel) -> AnyCancellable {
        self.currentChat = chat
        // the server's voice models are read now, so the first call after the app opens already has them (2026-11-19)
        stt.refresh()
        speech.refresh()
        computeAvailability()
        let previousHook = chat.onTurnEvent
        chat.onTurnEvent = { [weak self, weak chat] ev in
            previousHook?(ev)
            if Thread.isMainThread {
                MainActor.assumeIsolated {
                    guard let self, let chat else { return }
                    if case .callOffer(let callId, let topic) = ev {
                        self.ring(callId: callId, topic: topic, fromAgent: true, chat: chat)
                    }
                }
            } else {
                Task { @MainActor [weak self, weak chat] in
                    guard let self, let chat else { return }
                    if case .callOffer(let callId, let topic) = ev {
                        self.ring(callId: callId, topic: topic, fromAgent: true, chat: chat)
                    }
                }
            }
        }
        let previousCallHook = chat.onCallTurnEvent
        chat.onCallTurnEvent = { [weak self] callId, ev in
            previousCallHook?(callId, ev)
            if Thread.isMainThread {
                MainActor.assumeIsolated { self?.deliverCallTurn(callId, ev) }
            } else {
                Task { @MainActor [weak self] in self?.deliverCallTurn(callId, ev) }
            }
        }
        let previousQuestionHook = chat.onCallQuestion
        chat.onCallQuestion = { [weak self] callId in
            previousQuestionHook?(callId)
            if Thread.isMainThread {
                MainActor.assumeIsolated { self?.deliverCallQuestion(callId) }
            } else {
                Task { @MainActor [weak self] in self?.deliverCallQuestion(callId) }
            }
        }
        let previousBusyHook = chat.onCallBusyChanged
        chat.onCallBusyChanged = { [weak self] busy in
            previousBusyHook?(busy)
            if Thread.isMainThread {
                MainActor.assumeIsolated { self?.session?.onChatBusyChanged(busy) }
            } else {
                Task { @MainActor [weak self] in self?.session?.onChatBusyChanged(busy) }
            }
        }
        return AnyCancellable { [weak chat] in
            chat?.onTurnEvent = previousHook
            chat?.onCallTurnEvent = previousCallHook
            chat?.onCallBusyChanged = previousBusyHook
            chat?.onCallQuestion = previousQuestionHook
        }
    }

    /// A reply event of the call that started its turn (the other turns are never spoken by this call).
    private func deliverCallQuestion(_ callId: String?) {
        guard let s = session, let callId, s.callId == callId else { return }
        s.questionOpened()
    }

    private func deliverCallTurn(_ callId: String?, _ ev: TurnEvent) {
        guard let s = session, let callId, s.callId == callId else { return }
        s.onTurnEvent(ev)
    }

    /// Initiates an outgoing call from the header button.
    public func ringFromButton(chat: ChatModel? = nil) {
        guard let targetChat = chat ?? currentChat else { return }
        let callId = UUID().uuidString.lowercased()
        ring(callId: callId, topic: "", fromAgent: false, chat: targetChat)
        if !CallRules.ringOnButton {
            session?.accept()
        }
    }

    public func ring(callId: String, topic: String, fromAgent: Bool, chat: ChatModel) {
        if let current = session, current.ui.phase != .ended {
            // A second ring while a call is active is ignored
            return
        }
        guard let audio = audioFactory() else { return }
        let pl = phoneListenerFactory()
        let engine = deviceFactory()   // one device voice per call (released when the call ends)
        let s = CallSession(
            callId: callId,
            topic: topic,
            fromAgent: fromAgent,
            chat: chat,
            api: api,
            audio: audio,
            stt: stt,
            speech: speech,
            phoneListener: pl,
            device: engine,
            appLang: appLang,
            clock: clock,
            log: log
        )
        self.session = s
        self.isMinimized = false
    }

    public func minimized(_ on: Bool) {
        isMinimized = on
        if CallRules.muteWhenMinimized { session?.setMuted(on) }
    }

    public func clear() {
        session?.release()
        session = nil
        isMinimized = false
    }
}

// MARK: - AppModel Extension

extension AppModel {
    public var calls: CallCenter {
        get {
            if let c = callCenterHolder as? CallCenter { return c }
            let apiClosure: @Sendable () -> KeloApi? = { [weak self] in
                if Thread.isMainThread {
                    return MainActor.assumeIsolated { self?.api }
                } else {
                    return DispatchQueue.main.sync { self?.api }
                }
            }
            let appLangClosure: @Sendable () -> String = { [weak self] in
                if Thread.isMainThread {
                    return MainActor.assumeIsolated { self?.strings.code ?? "ko" }
                } else {
                    return DispatchQueue.main.sync { self?.strings.code ?? "ko" }
                }
            }
            let c = CallCenter(
                api: apiClosure,
                stt: VoiceModel(api: apiClosure, lang: appLangClosure),
                speech: SpeechModel(api: apiClosure),
                appLang: appLangClosure,
                log: { line in callLogger.info("\(line, privacy: .public)") }
            )
            callCenterHolder = c
            return c
        }
        set {
            callCenterHolder = newValue
        }
    }
}
