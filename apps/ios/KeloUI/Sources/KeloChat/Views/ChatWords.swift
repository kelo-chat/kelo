import Foundation
import KeloCore
import KeloShared

/// The app's own words for the chat's kinds (Android ChatScreen noticeText / errorText, ModelChooser, FollowupUi, PurchaseUi).
@MainActor
enum ChatWords {
    static func notice(_ n: ChatNotice) -> String {
        switch n {
        case .fallback(let model, let reason): L(R.string.chat_notice_fallback, ModelNames.display(model, label: nil), reason)
        case .retry: L(R.string.chat_notice_retry)
        case .reconnecting: L(R.string.chat_notice_reconnecting)
        case .uploaded(let name): L(R.string.chat_notice_uploaded, name)
        }
    }

    static func error(_ e: ChatError) -> String {
        switch e {
        case .stepLimit: L(R.string.chat_error_step_limit)
        case .message(let t): t
        case .pickModel(let d): L(R.string.chat_error_pick_model, d)
        case .network: L(R.string.chat_error_network)
        case .unsupported(let n): L(R.string.share_unsupported, n)
        case .shareFailed: L(R.string.share_failed)
        case .cameraDenied: L(R.string.chat_camera_denied)
        case .cameraMissing: L(R.string.chat_camera_missing)
        }
    }

    /// A folded chip's words: "메일 읽는 중 · 3/6" while they run, "✓ 메일 6개 읽음" once done (failures counted after it).
    static func folded(_ t: ChatItem.Tool) -> String {
        let m = t.members
        let ok = ChatReducer.doneCount(t)
        let failed = m.filter { $0.state == .error || $0.state == .denied }.count
        let running = m.filter { $0.state == .running }.count
        if running > 0 { return L(R.string.chat_tool_progress, t.shown, m.count - running, m.count) }
        if ok == 0 {
            return L(m.allSatisfy { $0.state == .denied } ? R.string.chat_tool_denied : R.string.chat_tool_error, t.shown) + " · \(m.count)"
        }
        if failed > 0 { return L(R.string.chat_tool_ok_failed, ChatReducer.foldedText(t, ok), failed) }
        return L(R.string.chat_tool_ok, ChatReducer.foldedText(t, ok))
    }

    /// The mark before one call in a folded chip's list.
    static func mark(_ s: ToolState) -> String {
        switch s {
        case .running: "⏳"
        case .ok: "✓"
        case .error: "✗"
        case .denied: "⛔"
        }
    }

    static func tool(_ t: ChatItem.Tool) -> String {
        switch t.state {
        case .running: L(R.string.chat_tool_running, t.shown)
        case .ok: L(R.string.chat_tool_ok, t.shown)
        case .error: L(R.string.chat_tool_error, t.shown)
        case .denied: L(R.string.chat_tool_denied, t.shown)
        }
    }

    static func phase(_ p: Phase, now: Double? = nil) -> String {
        let word: String = switch p.kind {
        case .waiting: L(R.string.chat_phase_waiting)
        case .thinking: L(now.map { ChatReducer.longThinking(p, now: $0) } == true ? R.string.chat_phase_thinking_long : R.string.chat_phase_thinking)
        case .writing: L(R.string.chat_phase_writing)
        case .tool: L(R.string.chat_phase_tool)
        case .approval: L(R.string.chat_phase_approval)
        case .switching: L(R.string.chat_phase_switching)
        }
        guard let label = p.label, !label.isEmpty else { return L(R.string.chat_status, word) }
        if p.kind == .tool { return L(R.string.chat_status, label) }
        if p.kind == .switching { return L(R.string.chat_status_label, word, ModelNames.display(label, label: nil)) }
        return L(R.string.chat_status_label, word, label)
    }

    static func effort(_ v: String) -> String {
        switch v {
        case "": L(R.string.chat_effort_default)
        case "low": L(R.string.chat_effort_low)
        case "medium": L(R.string.chat_effort_medium)
        case "high": L(R.string.chat_effort_high)
        case "xhigh": L(R.string.chat_effort_xhigh)
        case "max": L(R.string.chat_effort_max)
        default: v
        }
    }

    static func trait(_ t: ModelTrait) -> String {
        switch t {
        case .fast: L(R.string.model_trait_fast)
        case .reasoning: L(R.string.model_trait_reasoning)
        case .large: L(R.string.model_trait_large)
        case .images: L(R.string.model_trait_images)
        case .long: L(R.string.model_trait_long)
        }
    }

    /// The one line under a model's name: its traits, or "글로 대화" when nothing is known.
    static func blurb(_ id: String, facts: ModelEffort?) -> String {
        let traits = ModelTraits.of(id, facts: facts)
        return traits.isEmpty ? L(R.string.model_trait_plain) : traits.map(trait).joined(separator: L(R.string.model_trait_separator))
    }

    /// The chip's words: "<model> <thinking level>", the level only when this provider takes one. Main model only.
    static func chip(_ profile: Profile?, providers: [ModelProvider], list: ModelList?) -> String {
        guard let profile, !profile.model.isEmpty else { return L(R.string.chat_model_none) }
        let label = list?.provider == profile.provider ? list?.labels[profile.model] : nil
        let name = ModelNames.short(ModelNames.display(profile.model, label: label))
        let facts = list?.provider == profile.provider ? list?.info[profile.model] : nil
        if case .levels(let levels) = EffortRules.effort(providers.first { $0.name == profile.provider }, facts: facts) {
            return L(R.string.chat_model_chip, name, effort(EffortRules.shownLevel(levels, profile.effort)))
        }
        return name
    }

    /// A provider as people know it, never its raw id.
    static func provider(_ id: String, providers: [ModelProvider]) -> String {
        switch ProviderConsentRules.service(id) {
        case .claudeSubscription: return L(R.string.consent_service_claude)
        case .chatgptSubscription: return L(R.string.consent_service_chatgpt)
        case .antigravity: return "Antigravity"
        case .qwen: return "Qwen"
        case .minimax: return "MiniMax"
        case .other:
            let d = providers.first { $0.name == id }?.displayName.trimmingCharacters(in: .whitespaces) ?? ""
            return !d.isEmpty && d != id ? d : L(R.string.usage_provider_generic)
        }
    }

    static func outcome(_ o: ActionOutcome) -> String {
        switch o {
        case .approved: L(R.string.followup_done_approved)
        case .denied: L(R.string.followup_done_denied)
        case .resumed: L(R.string.followup_done_resumed)
        case .dropped: L(R.string.followup_done_dropped)
        case .snoozed: L(R.string.followup_done_snoozed)
        case .signedIn: L(R.string.followup_done_signed_in)
        case .gone: L(R.string.followup_done_gone)
        case .resolved: L(R.string.followup_done_resolved)
        }
    }

    /// "30분 뒤" / "2시간 뒤" / "1일 뒤"
    static func later(_ minutes: Int) -> String {
        if minutes >= 1440 && minutes % 1440 == 0 { return P(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440) }
        if minutes >= 60 && minutes % 60 == 0 { return P(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60) }
        return P(R.plurals.agent_rel_minutes_future, minutes, minutes)
    }

    static func action(_ step: FollowupStep) -> String {
        switch step {
        case .openLogin: L(R.string.followup_action_login)
        case .answer(_, let approve): approve ? L(R.string.followup_action_approve) : L(R.string.followup_action_deny)
        case .resumeWorker: L(R.string.followup_action_resume)
        case .drop: L(R.string.followup_action_drop)
        case .snooze(_, let minutes): L(R.string.followup_action_snooze, later(minutes))
        case .retry: L(R.string.followup_action_retry)
        }
    }

    static func cardSendError(_ e: CardSendError) -> String {
        switch e {
        case .notWaiting: L(R.string.chat_card_error_not_waiting)
        case .mismatch: L(R.string.chat_card_error_mismatch)
        case .notOnPhone: L(R.string.chat_card_error_not_on_phone)
        case .noLock: L(R.string.chat_card_error_no_lock)
        case .cancelled: L(R.string.chat_card_error_cancelled)
        case .network: L(R.string.chat_card_error_network)
        case .other: L(R.string.chat_card_error_other)
        }
    }

    static func attachError(_ e: AttachError?) -> String {
        switch e {
        case .server(let t): t
        case .tooLarge(let limit): L(R.string.upload_too_large_limit, UploadSizeText.of(limit))
        case .network: L(R.string.attach_error_network)
        case .unreadable, nil: L(R.string.attach_error_unreadable)
        }
    }

    static func attachmentWords() -> AttachmentWords {
        let line = L(R.string.chat_attachment_line, "\u{1}")
        let ask = L(R.string.chat_upload_prefill, "\u{1}")
        return AttachmentWords(line: { line.replacingOccurrences(of: "\u{1}", with: $0) },
                               ask: { ask.replacingOccurrences(of: "\u{1}", with: $0) },
                               separator: L(R.string.chat_attachment_separator))
    }
}
