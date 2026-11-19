import Foundation
import KeloCore
import KeloShared

// Sending a message and following its reply (or a reply the server started by itself), the Stop button, and error rows.

extension ChatModel {
    func apply(_ ev: TurnEvent) {
        onTurnEvent?(ev)
        onCallTurnEvent?(turnCall, ev)
        if turnCall != nil && CallRules.isAnswer(ev) { callQuiet = false }
        if case .paymentStep(let step) = ev { paymentSteps = PaymentStepRules.upsert(paymentSteps, step) }
        if case .pinNeeded(let need) = ev {
            pinNeeds = PinNeedRow.upsert(pinNeeds, need)
            if pinOnAllow.remove(need.slotId) != nil { releasePin(need.slotId) }
        }
        if phase != nil { phase = ChatReducer.phase(phase, ev, now: clock()) }
        let next = ChatReducer.apply(items, ev, newKey: key, now: Int64(Date().timeIntervalSince1970 * 1000))
        items = turnCall.map { CallCards.tagNew(before: items, after: next, callId: $0) } ?? next
        switch ev {
        case .error, .stepLimit: onEvent(.error)
        case .paymentAuto: onEvent(.paymentAuto)
        case .followup: loadFollowups()
        case .nestChanged(let id): NestLive.post(id)   // an open Nest screen reloads at once
        case .humanCheck: refreshAsks(force: true)   // the hand-over card (or its folded line) at the bottom, at once
        default: break
        }
        if ChatReducer.isReplies(ev) { repliesCame = true }
        app.activity.follow(ev)
        #if canImport(ActivityKit) && os(iOS)
        if case .toolStarted(let t) = ev, !t.hidden, !liveStarted, !turnId.isEmpty, let api = api() {
            liveStarted = true
            ReplyLiveActivity.shared.stepStarted(turnId: turnId, label: t.label.isEmpty ? t.name : t.label, api: api) { [weak self] in self?.stop() }
        }
        #endif
    }

    /// The ready attachments' stored names.
    public var uploading: Bool { attachments.contains { $0.state == .uploading } }
    public var ready: [Attachment] { attachments.filter { $0.state == .done } }

    /// Whether 보내기 (or 중지) can be pressed.
    public var canSend: Bool {
        busy || (!uploading && (!input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !ready.isEmpty))
    }

    /// Sends `given` (a card's "에이전트에게 알리기", 다시 시도, a quick reply) or the typed text with the uploaded attachments.
    public func send(_ given: String? = nil, words: AttachmentWords? = nil, answers: [ReplyAnswer] = []) {
        if busy || secretAsk != nil || consentAsk != nil || (given == nil && uploading) { return }
        let took = given == nil && words != nil
        let names = took ? ready.compactMap(\.stored) : []
        let text: String
        if let given { text = given.trimmingCharacters(in: .whitespacesAndNewlines) }
        else if let words { text = AttachmentText.compose(input, names: names, words: words) }
        else { text = input.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard !text.isEmpty else { return }
        // the files go with the message (the server keeps them with it, chat_attachments.py); nothing typed = the app's own sentence
        let files = trayFiles(took)
        start(text, fromComposer: given == nil, tookAttachments: took, answers: answers, secrets: nil, files: files,
              autoText: !files.isEmpty && input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    /// The uploaded files waiting in the tray, as a message's files (a held-back message sent again takes them along).
    func trayFiles(_ took: Bool) -> [MessageFile] {
        guard took else { return [] }
        return ready.compactMap { a in
            a.stored.map { MessageFile(name: a.name, path: $0.hasPrefix("uploads/") ? $0 : "uploads/" + $0, kind: a.image ? "image" : "document") }
        }
    }

    public func sendFromCall(text: String, callId: String, amend: Bool) -> Bool {
        if busy || secretAsk != nil || consentAsk != nil { return false }
        start(text, fromComposer: false, tookAttachments: false, answers: [], secrets: nil, files: [], autoText: false, call: callId, amend: amend)
        return true
    }

    /// The owner went on talking before this call turn answered: the turn stops and the owner's bubble of it goes (the call sends the
    /// words again with the rest, as an amend). False when it is not this call's turn, or anything of the answer came.
    public func takeBackFromCall(callId: String) -> Bool {
        guard busy, turnCall == callId, callQuiet, turn != nil else { return false }
        callQuiet = false
        if let i = items.lastIndex(where: { $0.asMsg != nil }), let m = items[i].asMsg, m.mine, m.callId == callId { items.remove(at: i) }
        stop()
        return true
    }

    public func followFromCall(turnId: String, callId: String) -> Bool {
        followTurn(turnId, call: callId)
    }

    /// Starts the reply to `text`. `secrets` = `SecretGuard.allow` only when the owner chose "Send as is" on the secret sheet. When
    /// the server holds the message back for a secret (409), its bubble goes again, the composer and the attachments come back as
    /// they were, and `secretAsk` opens the sheet (no error row, no failed reply).
    func start(_ text: String, fromComposer: Bool, tookAttachments: Bool, answers: [ReplyAnswer], secrets: String?,
               files: [MessageFile] = [], autoText: Bool = false, call: String? = nil, amend: Bool = false) {
        // a search window on screen: the newest page comes back first, so the reply lands under the newest messages (not in a gap)
        if hasNewer {
            leaveWindow { [weak self] in
                self?.start(text, fromComposer: fromComposer, tookAttachments: tookAttachments, answers: answers, secrets: secrets,
                            files: files, autoText: autoText, call: call, amend: amend)
            }
            return
        }
        guard let api = api() else { return }
        let beforeInput = input
        let beforeAttachments = attachments
        if fromComposer {
            input = ""
            if tookAttachments { attachments = [] }
            saving?.cancel()
            app.cache.put("draft", "")
        }
        busy = true
        turnCall = call
        callQuiet = call != nil
        phase = Phase(.waiting, since: clock())
        let bubble = key()
        items.append(.msg(.init(key: bubble, mine: true, text: text, at: Int64(Date().timeIntervalSince1970 * 1000), files: files, autoText: autoText, fromCall: call != nil, callId: call)))
        onEvent(.sent)
        app.activity.startFollowing()
        let canCallFn = canCall
        turn = Task { [weak self] in
            var finished = false
            do {
                try await TurnFollower(api: KeloTurnApi(api: api, canCall: canCallFn, amend: amend && call != nil)).talk(
                    text: text, timezone: TimeZone.current.identifier, answers: answers,
                    secrets: secrets, attachments: files.map(\.path), autoText: autoText, call: call, onId: { id in
                    await MainActor.run { self?.turnId = id }
                }) { ev in
                    await MainActor.run { self?.apply(ev) }
                }
                finished = true
            } catch is CancellationError {
            } catch let e as ApiError {
                if secrets == nil, let held = SecretGuard.of(e), let self {
                    // nothing was sent to the model or stored: no bubble left behind, the text back where it was
                    self.fresh.remove(bubble)
                    self.items.removeAll { $0.key == bubble }
                    if fromComposer { self.input = beforeInput }
                    if tookAttachments { self.attachments = beforeAttachments + self.attachments }
                    self.onCallQuestion?(self.turnCall)
                    self.secretAsk = SecretAsk(found: held, text: text, answers: answers, fromComposer: fromComposer,
                                               tookAttachments: tookAttachments, composer: fromComposer ? beforeInput : "",
                                               composerSpans: fromComposer ? SecretGuard.composerSpans(beforeInput, held.spans) : [])
                } else if let need = AiConsent.of(e), let self {
                    // nobody agreed yet: nothing was stored or sent; the text goes back and the question opens
                    self.fresh.remove(bubble)
                    self.items.removeAll { $0.key == bubble }
                    if fromComposer { self.input = beforeInput }
                    if tookAttachments { self.attachments = beforeAttachments + self.attachments }
                    self.onCallQuestion?(self.turnCall)
                    self.consentError = nil
                    self.consentAsk = ConsentAsk(need: need, text: text, answers: answers, fromComposer: fromComposer,
                                                 tookAttachments: tookAttachments, secrets: secrets)
                } else {
                    self?.addError(e.isNetwork ? .network : (ChatRules.modelRequired(e) ? .pickModel(e.detail) : .message(e.detail)))
                }
            } catch {
                self?.addError(.message(String(describing: error)))
            }
            self?.endTurn(finished: finished)
        }
    }

    private func endTurn(finished: Bool) {
        turn = nil
        turnCall = nil
        callQuiet = false
        #if canImport(ActivityKit) && os(iOS)
        if liveStarted { ReplyLiveActivity.shared.finished(state: finished ? "done" : (Task.isCancelled ? "stopped" : "failed")) }
        #endif
        turnId = ""
        liveStarted = false
        app.activity.stopFollowing(finished: finished)
        // questions nobody answered are denied by the server once the reply is over
        busy = false
        phase = nil
        items = ChatReducer.endTurn(items)
        secrets = SecretRules.settled(secrets)   // answered entries go with the reply's end; open ones stay (spec 5.16)
        if finished { onEvent(.replyDone) }
        // the answer buttons and cards ride on the stored message, and the stored text is the tidier one: after every finished reply the
        // newest page is loaded again and swaps in place (the streamed rows keep their keys, ChatReducer.mergeStored)
        if repliesCame || finished {
            repliesCame = false
            Task { [weak self] in try? await self?.reloadNow(afterReply: finished) }
        } else {
            remember()
        }
        loadFollowups()
        refreshSecrets()
    }

    /// Follows a reply the server started by itself for this phone (a secure entry saved after the reply that asked had ended): the
    /// same status line, events and end as a reply sent from here, without a bubble of the user's own.
    @discardableResult
    public func followTurn(_ turnId: String, call: String? = nil) -> Bool {
        guard !busy, let api = api() else { return false }
        busy = true
        turnCall = call
        callQuiet = false
        self.turnId = turnId
        phase = Phase(.waiting, since: clock())
        app.activity.startFollowing()
        let canCallFn = canCall
        turn = Task { [weak self] in
            var finished = false
            do {
                try await TurnFollower(api: KeloTurnApi(api: api, canCall: canCallFn)).follow(id: turnId) { ev in
                    await MainActor.run { self?.apply(ev) }
                }
                finished = true
            } catch is CancellationError {
            } catch let e as ApiError {
                if e.status != 404 { self?.addError(e.isNetwork ? .network : .message(e.detail)) }
            } catch {
                self?.addError(.message(String(describing: error)))
            }
            self?.endTurn(finished: finished)
        }
        return true
    }

    /// 다시 시도 on a reply or an error: the user's message before it goes again.
    public func retry(_ key: String) {
        guard !busy, let text = MessageActions.retryText(items, key: key) else { return }
        send(text)
    }

    /// The Stop button: cancelling the reply tells the server to stop (DELETE /api/turns/{id}).
    public func stop() { turn?.cancel() }

    public func showError(_ e: ChatError) { addError(e) }

    func addError(_ e: ChatError) {
        items.append(.error(key: key(), e))
        onEvent(.error)
    }
}
