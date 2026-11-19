import Foundation

// Typed calls for the screens (paths and bodies from the server.py API blocks). Each throws ApiError like any KeloApi call.

extension KeloApi {
    private static func seg(_ s: String) -> String { segment(s) }
    private static func num<T: BinaryInteger>(_ v: T) -> JSON { .number(Double(v)) }

    // ---- profile, devices, pairing ----------------------------------------------------------------------------------------------
    public func profile() async throws -> Profile { try await get("/api/profile") }

    /// PUT /api/profile with only the fields given (428 "consent_required:<provider>" for an unofficial provider not yet agreed to;
    /// 422 when main and fallback would share a provider).
    public func updateProfile(_ fields: [String: String]) async throws -> Profile {
        try await put("/api/profile", body: .object(fields.mapValues { .string($0) }))
    }

    public func devices() async throws -> DeviceList { try await get("/api/devices") }
    public func removeDevice(_ id: Int64) async throws { try await request("DELETE", "/api/devices/\(id)") }
    public func about() async throws -> About { try await get("/api/about") }

    public func clearMessages() async throws { try await request("DELETE", "/api/messages") }

    /// POST /api/approvals/{id} {approve, sig?, remember?: "site" | "schedule" | "recipient", edited?: {field: text} (an Allow with the
    /// message fixed in the card), via?: "notification" (a phone notification's button; the server keeps "app" otherwise)}.
    /// `sig`: the phone's signature of a payment Allow.
    public func answerApproval(_ id: String, approve: Bool, remember: String? = nil, sig: String? = nil,
                               edited: [String: JSON]? = nil, via: String? = nil) async throws -> ApprovalAnswer {
        var body: [String: JSON] = ["approve": .bool(approve)]
        if let sig { body["sig"] = .string(sig) }
        if let remember { body["remember"] = .string(remember) }
        if approve, let edited { body["edited"] = .object(edited) }
        if let via { body["via"] = .string(via) }
        return try await post("/api/approvals/\(KeloApi.seg(id))", body: .object(body))
    }

    // ---- agent status, tasks, approvals ------------------------------------------------------------------------------------------
    public func agentStatus() async throws -> AgentStatus { try await get("/api/agent/status") }

    public func tasks(limit: Int = 50, before: Int64? = nil, source: String? = nil) async throws -> TaskPage {
        var q = "limit=\(limit)"
        if let before { q += "&before=\(before)" }
        if let source { q += "&source=\(KeloApi.query(source))" }
        return try await get("/api/tasks?\(q)")
    }

    public func task(_ id: Int64) async throws -> TaskDetail { try await get("/api/tasks/\(id)") }
    public func tasksSeen(upTo id: Int64) async throws -> SeenAnswer { try await post("/api/tasks/seen", body: ["up_to": KeloApi.num(id)]) }
    public func stopTask(_ id: Int64) async throws { try await request("POST", "/api/tasks/\(id)/stop") }
    public func pendingApprovals() async throws -> [PendingApproval] { PendingApproval.parseList(try await request("GET", "/api/approvals/pending")) }

    public func approvalHistory(limit: Int = 50, before: Double? = nil, taskId: Int64? = nil) async throws -> ApprovalHistory {
        var q = "limit=\(limit)"
        if let before { q += "&before=\(JSON.format(before))" }
        if let taskId { q += "&task_id=\(taskId)" }
        return try await get("/api/approvals/history?\(q)")
    }

    public func approvalRules() async throws -> RuleList { try await get("/api/approvals/rules") }
    public func deleteApprovalRule(_ id: Int64) async throws { try await request("DELETE", "/api/approvals/rules/\(id)") }

    /// POST /api/schedules {name, prompt, one of cron (+ window_minutes) | run_at | every_minutes, timezone?, enabled?, goal_id?}.
    public func createSchedule(_ body: JSON) async throws -> Schedule { try await post("/api/schedules", body: body) }
    public func updateSchedule(_ id: Int64, _ body: JSON) async throws -> Schedule { try await patch("/api/schedules/\(id)", body: body) }
    /// DELETE archives it: off and out of the list, restorable for 30 days.
    public func archiveSchedule(_ id: Int64) async throws { try await request("DELETE", "/api/schedules/\(id)") }
    /// DELETE ?forever=true: gone at once, not kept in the archive.
    public func deleteScheduleForever(_ id: Int64) async throws { try await request("DELETE", "/api/schedules/\(id)?forever=true") }
    /// DELETE /api/schedules/archive/{id}: an archived schedule deleted for good.
    public func eraseArchivedSchedule(_ id: Int64) async throws { try await request("DELETE", "/api/schedules/archive/\(id)") }
    public func runSchedule(_ id: Int64) async throws -> RunStarted { try await post("/api/schedules/\(id)/run") }
    public func resumeSchedule(_ id: Int64) async throws -> Schedule { try await post("/api/schedules/\(id)/resume") }
    public func scheduleRuns(_ id: Int64, limit: Int = 20) async throws -> ScheduleRuns { try await get("/api/schedules/\(id)/runs?limit=\(limit)") }
    public func scheduleArchive() async throws -> ScheduleArchive { try await get("/api/schedules/archive") }
    public func restoreSchedule(_ id: Int64) async throws -> Schedule { try await post("/api/schedules/\(id)/restore") }
    /// What a schedule may do without asking (a login, a purchase up to a cap), allowed once at setup.
    public func scheduleGrants(_ id: Int64) async throws -> ScheduleGrantList { try await get("/api/schedules/\(id)/grants") }
    /// Takes one back: its runs ask again.
    public func revokeScheduleGrant(_ g: ScheduleGrant) async throws {
        _ = try await request("DELETE", g.revokePath)
    }
    /// GET /api/watchers/{id}/grants: what a watcher may do without asking (allowed at setup).
    public func watcherGrants(_ id: Int64) async throws -> ScheduleGrantList { try await get("/api/watchers/\(id)/grants") }
    /// GET /api/workers/{id}/grants: what a background job may do without asking.
    public func jobGrants(_ id: String) async throws -> ScheduleGrantList { try await get("/api/workers/\(KeloApi.segment(id))/grants") }

    // ---- watchers --------------------------------------------------------------------------------------------------------------
    public func watchers() async throws -> WatcherList { try await get("/api/watchers") }
    public func deleteWatcher(_ id: Int64) async throws { try await request("DELETE", "/api/watchers/\(id)") }
    public func dryRunWatcher(_ id: Int64) async throws -> DryRunAnswer { try await post("/api/watchers/\(id)/dry-run") }
    /// 409 without a fresh successful dry run.
    public func enableWatcher(_ id: Int64, _ enabled: Bool) async throws -> Watcher {
        try await post("/api/watchers/\(id)/enable", body: ["enabled": .bool(enabled)])
    }
    public func watcherEvents(_ id: Int64) async throws -> WatcherEvents { try await get("/api/watchers/\(id)/events") }
    public func buyCheck(_ id: Int64) async throws -> BuyReady { try await post("/api/watchers/\(id)/buy-check") }

    // ---- workers ---------------------------------------------------------------------------------------------------------------
    public func workers(limit: Int = 50, status: String? = nil, parentTaskId: Int64? = nil) async throws -> WorkerList {
        var q = "limit=\(limit)"
        if let status { q += "&status=\(KeloApi.query(status))" }
        if let parentTaskId { q += "&parent_task_id=\(parentTaskId)" }
        return try await get("/api/workers?\(q)")
    }
    public func worker(_ id: String) async throws -> WorkerDetail { try await get("/api/workers/\(KeloApi.seg(id))") }
    public func resumeWorker(_ id: String, note: String? = nil) async throws -> WorkerAnswer {
        try await post("/api/workers/\(KeloApi.seg(id))/resume", body: note.map { ["note": .string($0)] })
    }
    public func stopWorker(_ id: String) async throws -> WorkerAnswer { try await post("/api/workers/\(KeloApi.seg(id))/stop") }
    public func messageWorker(_ id: String, text: String) async throws -> WorkerAnswer {
        try await post("/api/workers/\(KeloApi.seg(id))/message", body: ["text": .string(text)])
    }

    // ---- browser tabs ----------------------------------------------------------------------------------------------------------
    public func browserTabs(thumbnails: Bool = true) async throws -> BrowserTabs { try await get("/api/browser/sessions?thumbnails=\(thumbnails)") }
    /// Switch to or close one tab of a held task window (409: this device does not hold it, or it is the last tab).
    public func browserTabAction(_ id: String, tab: String, close: Bool) async throws {
        _ = try await request("POST", "/api/browser/sessions/\(KeloApi.seg(id))/tabs/\(KeloApi.seg(tab))", body: LiveRules.tabAction(close: close))
    }
    public func closeBrowserTab(_ id: String) async throws { try await request("DELETE", "/api/browser/sessions/\(KeloApi.seg(id))") }
    /// 409: another device holds the tab.
    public func browserControl(_ id: String, take: Bool, handover: Bool = false) async throws -> ControlAnswer {
        try await post("/api/browser/sessions/\(KeloApi.seg(id))/control", body: LiveMessages.control(take: take, handover: handover))
    }
    @discardableResult
    public func browserInput(_ id: String, _ event: JSON) async throws -> JSON {
        try await request("POST", "/api/browser/sessions/\(KeloApi.seg(id))/input", body: event)
    }

    // ---- notifications, journal, reflection, avatar -----------------------------------------------------------------------------
    public func notifications(after: Int64? = nil, limit: Int = 50) async throws -> NotificationPage {
        try await get("/api/notifications?" + (after.map { "after=\($0)&" } ?? "") + "limit=\(limit)")
    }
    /// Of these waiting items' ids (a notification's approval id), the ones that do not wait any more (answered anywhere, ran out, cancelled).
    public func settledItems(_ ids: [String]) async throws -> [String] {
        let s: SettledItems = try await get("/api/notifications/settled?ids=" + ids.map { KeloApi.query($0) }.joined(separator: ","))
        return s.ids
    }
    public func journal(month: String) async throws -> JournalMonth { try await get("/api/journal?month=\(KeloApi.query(month))") }
    public func setRuleStatus(_ id: Int64, status: String) async throws -> LearnedRule {
        try await patch("/api/reflection/rules/\(id)", body: ["status": .string(status)])
    }
    public func avatar() async throws -> Data { try await download("/api/agent/avatar") }

    // ---- connectors and keychain -------------------------------------------------------------------------------------------------
    public func connectors() async throws -> [Connector] { try await get("/api/connectors") }
    public func oauthProviders() async throws -> [OAuthProvider] { try await get("/api/oauth/providers") }
    public func startDeviceFlow(_ provider: String, scope: String) async throws -> OAuthFlow {
        try await post("/api/oauth/\(KeloApi.seg(provider))/start", body: ["scope": .string(scope)])
    }
    public func deviceFlow(_ flowId: String) async throws -> OAuthFlow { try await get("/api/oauth/flows/\(KeloApi.seg(flowId))") }
    public func cancelDeviceFlow(_ flowId: String) async throws { try await request("DELETE", "/api/oauth/flows/\(KeloApi.seg(flowId))") }
    /// An MCP sign-in page to open in the system browser (ASWebAuthenticationSession), never in a web view inside the app.
    public func mcpSignInURL(_ service: String) async throws -> String {
        try await post("/api/mcp-oauth/\(KeloApi.seg(service))/start", body: ["origin": .string(baseUrl)], as: UrlAnswer.self).url
    }
    public func mailProviders() async throws -> MailProviders { try await get("/api/mail/providers") }
    public func keyForm(_ via: String) async throws -> KeyForm { try await get("/api/keys/\(KeloApi.seg(via))") }
    /// Disconnects a connection. The answer's "data" says what came from it that is still kept (nil when the server did not say).
    public func disconnect(item: String) async throws -> ConnectorDataCounts? {
        ConnectorDataCounts.afterDisconnect(try await request("DELETE", "/api/credentials/\(KeloApi.seg(item))"))
    }
    /// Deletes what came from the connector (files go to the library's trash); answers the counts deleted.
    public func forgetConnector(_ item: String) async throws -> ConnectorDataCounts {
        ConnectorDataCounts.afterForget(try await request("POST", "/api/connectors/\(KeloApi.seg(item))/forget"))
    }
    public func vault() async throws -> VaultState { try await get("/api/vault") }

    // ---- wallet metadata (never card numbers) -------------------------------------------------------------------------------------
    public func wallet() async throws -> WalletInfo { try await get("/api/wallet") }
    public func walletLimits() async throws -> WalletLimits { try await get("/api/wallet/limits") }
    public func walletNotifications() async throws -> WalletNotifications { try await get("/api/wallet/notifications") }
    public func setWalletPaused(_ paused: Bool) async throws -> PauseState { try await put("/api/wallet/pause", body: ["paused": .bool(paused)]) }

    // ---- addresses and shipping profiles (no card data; the server checks every field again) ------------------------------------
    /// GET /api/address/countries: every country an address can be in (English names; show `Locale`'s name for the code).
    public func addressCountries() async throws -> AddressCountries { try await get("/api/address/countries") }
    /// GET /api/address/form/{CC}: that country's form, labels in the request's language (Accept-Language).
    public func addressForm(_ country: String) async throws -> AddressForm {
        try await get("/api/address/form/\(KeloApi.seg(country.uppercased()))")
    }
    /// GET /api/wallet/shipping (the default one first).
    public func shippingProfiles() async throws -> [ShippingProfile] {
        try await get("/api/wallet/shipping", as: ShippingProfiles.self).profiles
    }
    /// POST /api/wallet/shipping {country, field keys, phone, note?, label?, default?} (201). 422 {"detail"} names the field.
    public func addShippingProfile(_ body: JSON) async throws -> ShippingProfile { try await post("/api/wallet/shipping", body: body) }
    /// PATCH /api/wallet/shipping/{id} with any of the fields; {"default": true} makes it the default (never "default": false).
    public func updateShippingProfile(_ id: String, _ body: JSON) async throws -> ShippingProfile {
        try await patch("/api/wallet/shipping/\(KeloApi.seg(id))", body: body)
    }
    /// DELETE /api/wallet/shipping/{id}: deleting the default makes the oldest remaining one the default.
    public func deleteShippingProfile(_ id: String) async throws { try await request("DELETE", "/api/wallet/shipping/\(KeloApi.seg(id))") }
    /// POST /api/shipping-forms/{form_id} {address, save, label, payment}: the in-chat address form's answer (the agent waits for it).
    /// 422: a field is wrong (keep the form open); 404: the form is gone (expired, or the reply stopped).
    public func answerShippingForm(_ formId: String, _ body: JSON) async throws -> ShippingFormAnswer {
        try await post("/api/shipping-forms/\(KeloApi.seg(formId))", body: body)
    }
    /// POST /api/shipping-forms/{form_id} {"cancel": true}: the user closed the form without an address.
    public func cancelShippingForm(_ formId: String) async throws {
        try await request("POST", "/api/shipping-forms/\(KeloApi.seg(formId))", body: ["cancel": .bool(true)])
    }

    public func resources() async throws -> Resources { try await get("/api/resources") }
    /// GET /api/storage/cleanup (owner only): what can safely be removed, grouped.
    public func cleanupList() async throws -> CleanupList { try await get("/api/storage/cleanup") }
    /// POST /api/storage/cleanup: only after the user confirmed; the server removes only ids on its own current list.
    public func cleanup(_ ids: [String]) async throws -> CleanupResult {
        try await post("/api/storage/cleanup", body: ["items": .array(ids.map { .string($0) }), "confirm": .bool(true)])
    }

    /// POST /api/push/register {token, platform, environment?}. Android sends its FCM token; the iPhone its APNs device token
    /// (hex) with platform "ios-apns" and the app's aps-environment ("production" | "development"); see apps/ios/README.md "Push".
    public func registerPush(token: String, platform: String, environment: String? = nil) async throws -> PushRegistered {
        var body: [String: JSON] = ["token": .string(token), "platform": .string(platform)]
        if let environment { body["environment"] = .string(environment) }
        return try await post("/api/push/register", body: .object(body))
    }
    public func unregisterPush() async throws -> PushRegistered { try await delete("/api/push/register") }
}
