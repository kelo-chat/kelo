import Testing
import Foundation
import CryptoKit
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private func decode<T: Decodable>(_ json: String, as: T.Type = T.self) throws -> T {
    try JSONDecoder().decode(T.self, from: Data(json.utf8))
}

@Suite struct ModelRulesTests {
    @Test func fallbackNeverSharesTheMainProvider() {
        let p = Profile(provider: "openai", model: "gpt-5", fallbackProvider: "openai", fallbackModel: "gpt-4o")
        #expect(ModelRules.initialFallback(p) == .none)
        let q = Profile(provider: "openai", model: "gpt-5", fallbackProvider: "anthropic", fallbackModel: "claude")
        #expect(ModelRules.initialFallback(q) == ModelChoice("anthropic", "claude"))
        let (fb, cleared) = ModelRules.afterMainChange(ModelChoice("anthropic", ""), ModelChoice("anthropic", "claude"))
        #expect(fb == .none && cleared)
        let (same, kept) = ModelRules.afterMainChange(ModelChoice("openai", ""), ModelChoice("anthropic", "claude"))
        #expect(same == ModelChoice("anthropic", "claude") && !kept)
        #expect(ModelRules.complete(.none))
        #expect(!ModelRules.complete(ModelChoice("openai", " ")))
        let all = [ModelProvider(name: "a"), ModelProvider(name: "b")]
        #expect(ModelRules.fallbackProviders(all, main: "a").map(\.name) == ["b"])
    }

    @Test func modelNamesReadLikePeopleWrite() {
        #expect(ModelNames.humanize("claude-sonnet-5-5") == "Claude Sonnet 5.5")
        #expect(ModelNames.humanize("anthropic/claude-3-5-haiku-20241022") == "Claude 3.5 Haiku")
        #expect(ModelNames.humanize("qwen3:8b") == "Qwen3 8B")
        #expect(ModelNames.humanize("gpt-4o") == "GPT-4o")
        #expect(ModelNames.humanize("o3") == "o3")
        #expect(ModelNames.display("x-1", label: "Nice name") == "Nice name")
        let all = ModelNames.displayAll(["gpt-4o", "gpt-4o-2024-08-06"], labels: [:])
        #expect(all["gpt-4o"] == "GPT-4o")
        #expect(all["gpt-4o-2024-08-06"] == "GPT-4o (2024-08-06)")
    }

    @Test func endpointAndKeyChecks() {
        #expect(EndpointRules.check("") == .empty)
        #expect(EndpointRules.check("http://a b") == .spaces)
        #expect(EndpointRules.check("ftp://x") == .scheme)
        #expect(EndpointRules.check("localhost:1234") == .scheme)
        #expect(EndpointRules.check("http://") == .host)
        #expect(EndpointRules.check("http://192.168.0.5:1234/v1") == nil)
        #expect(EndpointRules.check("https://user@host.example/v1") == nil)
        #expect(KeyRules.state("app") == .inApp)
        #expect(KeyRules.state("env") == .env)
        #expect(KeyRules.state(nil) == .none)
        #expect(!KeyRules.keyReady(" 1234567 "))
        #expect(KeyRules.keyReady("12345678"))
    }

    @Test func consentRules() {
        let p = ModelProvider(name: "claude-subscription", displayName: "Claude", unofficial: true,
                           consent: ProviderConsentRecord(acceptedAt: 1, version: 1), consentVersion: 2)
        #expect(ProviderConsentRules.needs(p))
        let ok = ModelProvider(name: "x", unofficial: true, consent: ProviderConsentRecord(acceptedAt: 1, version: 2), consentVersion: 2)
        #expect(!ProviderConsentRules.needs(ok))
        #expect(!ProviderConsentRules.needs(ModelProvider(name: "openai")))
        #expect(ProviderConsentRules.service("claude-subscription") == .claudeSubscription)
        #expect(ProviderConsentRules.service("openai-codex") == .chatgptSubscription)
        #expect(ProviderConsentRules.subjectParticle("Anthropic") == "이")
        #expect(ProviderConsentRules.subjectParticle("OpenAI") == "가")
        #expect(ProviderConsentRules.subjectParticle("구글") == "이")
        #expect(ProviderConsentRules.subjectParticle("카카오") == "가")
        #expect(ProviderConsentRules.pending(main: "claude-subscription", fallback: "claude-subscription", providers: [p]).count == 1)
        #expect(ProviderConsentRules.requiredBy(ApiError(status: 428, detail: "consent_required:qwen")) == "qwen")
        #expect(ProviderConsentRules.requiredBy(ApiError(status: 422, detail: "consent_required:qwen")) == nil)
    }

    @Test func phoneSignInOnlyWhenTheServerEnablesIt() {
        #expect(SignInRules.offered(signin: true, signedIn: false))
        #expect(SignInRules.offered(signin: true, signedIn: nil))
        #expect(!SignInRules.offered(signin: false, signedIn: false))
        #expect(!SignInRules.offered(signin: true, signedIn: true))
        #expect(SignInRules.pasteReady("abc123"))
        #expect(!SignInRules.pasteReady("abc 123"))
        #expect(!SignInRules.pasteReady("  "))
        #expect(SignInRules.problem(ApiError(status: 428, detail: "")) == .consent)
        #expect(SignInRules.problem(ApiError(status: 409, detail: "already_signed_in: as x")) == .already)
        #expect(SignInRules.problem(ApiError(status: 503, detail: "")) == .off)
        #expect(SignInRules.readable(ApiError(status: 409, detail: "already_signed_in: as x")) == "as x")
        #expect(SignInRules.mmss(125) == "2:05")
        #expect(SignInRules.finished(SignInSession(state: "done")))
        #expect(SignInRules.ended(SignInSession(state: "expired")))
        #expect(SignInRules.isDeviceCode(SignInSession(kind: "device_code")))
    }

    @Test func signOutOutcomes() {
        #expect(SignOutRules.outcome(nil, ApiError(status: 409, detail: "")) == .mainModel)
        #expect(SignOutRules.outcome(nil, ApiError(status: 404, detail: "")) == .done)
        #expect(SignOutRules.outcome(nil, ApiError(status: 500, detail: "")) == .failed)
        #expect(SignOutRules.outcome(SignOutResult(stillSignedIn: true), nil) == .doneEnvLeft)
        #expect(SignOutRules.outcome(SignOutResult(fallbackCleared: true), nil) == .doneFallbackCleared)
        #expect(SignOutRules.outcome(SignOutResult(), nil) == .done)
        #expect(!SignOutRules.done(.failed) && !SignOutRules.done(.mainModel) && SignOutRules.done(.doneEnvLeft))
        #expect(SignOutRules.isMain("a", mainProvider: "a") && !SignOutRules.isMain("a", mainProvider: ""))
    }
}

@Suite struct DocumentRulesTests {
    @Test func mediaKinds() {
        #expect(MediaKind.of("IMG.HEIC") == "image")
        #expect(MediaKind.of("clip.mov") == "video")
        #expect(MediaKind.of("voice.m4a") == "audio")
        #expect(MediaKind.of("report.pdf") == "document")
        #expect(MediaKind.of("noext") == "document")
        #expect(MediaKind.withSuffix("IMG_0042", mime: "video/mp4") == "IMG_0042.mp4")
        #expect(MediaKind.withSuffix("a.pdf", mime: "video/mp4") == "a.pdf")
        #expect(MediaKind.withSuffix("x", mime: "application/zip") == "x")
    }

    @Test func uploadsDecodeLeniently() throws {
        let u = MyUpload.list(try JSON.parse(#"[{"path":"uploads/a.mp4","bytes":"12","kind":"video","duration":3.5},{"bytes":1},{"path":"uploads/b.png"}]"#))
        #expect(u.count == 2)
        #expect(u[0].name == "a.mp4" && u[0].bytes == 12 && u[0].isMedia && u[0].duration == 3.5)
        #expect(u[1].kind == "image")
    }

    @Test func formatHelpers() {
        let en = Locale(identifier: "en_US")
        #expect(MyFormat.number(1_234_567, locale: en) == "1,234,567")
        #expect(MyFormat.storageParts(5 * 1024 * 1024 * 1024, locale: en) == ("5.0", "gb"))
        #expect(MyFormat.storageParts(20 * 1024 * 1024, locale: en) == ("20", "mb"))
        #expect(MyFormat.storageParts(3 * 1024 * 1024 / 2, locale: en) == ("1.5", "mb"))
        #expect(MyFormat.storageParts(2048, locale: en) == ("2", "kb"))
        #expect(MyFormat.fileParts(10, locale: en) == ("1", "kb"))
        #expect(MyFormat.cpuCount(2) == "2")
        #expect(MyFormat.cpuCount(1.5) == "1.5")
        #expect(MyFormat.clipLength(65.4) == "1:05")
        #expect(MyFormat.clipLength(3723) == "1:02:03")
        #expect(MyFormat.ago(nil, now: 100) == nil)
        #expect(MyFormat.ago(1000, now: 1060)! == ("now", 0))
        #expect(MyFormat.ago(1000, now: 1000 + 600)! == ("m", 10))
        #expect(MyFormat.ago(1000, now: 1000 + 3 * 3600)! == ("h", 3))
        #expect(MyFormat.ago(1000, now: 1000 + 5 * 86400)! == ("d", 5))
    }
}

@Suite struct OperatorRulesTests {
    @Test func cloudflareForm() {
        #expect(OperatorRules.problem(CloudflareForm()) == nil)
        #expect(OperatorRules.problem(CloudflareForm(token: "short")) == .token)
        #expect(OperatorRules.problem(CloudflareForm(accountId: "xyz")) == .account)
        #expect(OperatorRules.problem(CloudflareForm(zoneId: String(repeating: "a", count: 32), zoneName: "bad")) == .zone)
        #expect(OperatorRules.problem(CloudflareForm(maxNames: "0")) == .max)
        let body = OperatorRules.cloudflareBody(CloudflareForm(token: "", accountId: String(repeating: "A", count: 32), zoneName: "Kelo.Example.com", maxNames: "50"))
        #expect(body["api_token"] == nil)
        #expect(body["account_id"]?.stringValue == String(repeating: "a", count: 32))
        #expect(body["zone_name"]?.stringValue == "kelo.example.com")
        #expect(body["max_names"]?.intValue == 50)
        #expect(OperatorRules.cloudflareOff()["api_token"]?.stringValue == "")
    }

    @Test func keyFilesAndOwner() {
        #expect(OperatorRules.keyFileOk(Data(#"{"type":"service_account"}"#.utf8)))
        #expect(!OperatorRules.keyFileOk(Data("[1]".utf8)))
        #expect(!OperatorRules.keyFileOk(Data()))
        #expect(!OperatorRules.keyFileOk(Data(repeating: 32, count: 30_000)))
        #expect(OperatorRules.ownerFrom(nil) == true)
        #expect(OperatorRules.ownerFrom(403) == false)
        #expect(OperatorRules.ownerFrom(500) == nil)
        #expect(OperatorRules.reportsPath(showHandled: false).hasSuffix("handled=false"))
        #expect(OperatorRules.modelName(ReceivedReport(provider: "openai", model: "gpt")) == "openai / gpt")
        #expect(OperatorRules.modelName(ReceivedReport()) == nil)
    }
}

@Suite struct NotifySettingsTests {
    @Test func sendTimesAreReadAndAnOlderServerHasNone() throws {
        let p = try decode(#"{"enabled": true, "send_times": {"day": "2026-11-17", "times": ["12:00", "16:00", "20:00"], "briefing": "08:00", "next_at": 1}}"#, as: ProactiveSettings.self)
        #expect(NotifySettingsRules.sendTimes(p) == ["12:00", "16:00", "20:00"])
        #expect(NotifySettingsRules.sendTimes(try decode(#"{"enabled": true}"#, as: ProactiveSettings.self)).isEmpty)
        #expect(NotifySettingsRules.sendTimes(try decode(#"{"send_times": {"times": ["09:00", "x"]}}"#, as: ProactiveSettings.self)) == ["09:00"])
    }

    @Test func anItemWithItsOwnJobSaysSoAndAnOlderServerSaysNothing() throws {
        let k = try decode(#"{"list": ["A", "B"], "entries": [{"text": "A", "state": "own", "job": {"kind": "schedule", "id": 3, "name": "Before events", "description": "Every 15 minutes", "on": true}}, {"text": "B", "state": "send_times", "job": null}]}"#, as: ChecklistSettings.self)
        #expect(NotifySettingsRules.ownJob(k, "A") == "Before events")
        #expect(NotifySettingsRules.ownJob(k, "B") == nil)
        #expect(NotifySettingsRules.ownJob(try decode(#"{"list": ["A"]}"#, as: ChecklistSettings.self), "A") == nil)
    }

    @Test func theAboutTextSaysTheSendTimesWhenTheServerHasThem() throws {
        #expect(NotifySettingsRules.aboutSendTimes(try decode(#"{"enabled": true, "send_times": {"times": ["12:00"]}}"#, as: ProactiveSettings.self)))
        #expect(NotifySettingsRules.aboutSendTimes(try decode(#"{"send_times": {"times": []}}"#, as: ProactiveSettings.self)))
        #expect(!NotifySettingsRules.aboutSendTimes(try decode(#"{"enabled": true}"#, as: ProactiveSettings.self)))
        #expect(!NotifySettingsRules.aboutSendTimes(try decode(#"{"send_times": null}"#, as: ProactiveSettings.self)))
    }

    @Test func settingsRules() {
        #expect(NotifySettingsRules.parseTime("07:30")! == (7, 30))
        #expect(NotifySettingsRules.parseTime("24:00") == nil)
        #expect(NotifySettingsRules.parseTime("7:5") == nil)
        #expect(NotifySettingsRules.formatTime(7, 5) == "07:05")
        // the 새 소식 알림 text says how often it looks, from the server's every_minutes
        #expect(NotifySettingsRules.every(120) == (true, 2) && NotifySettingsRules.every(60) == (true, 1))
        #expect(NotifySettingsRules.every(90) == (false, 90) && NotifySettingsRules.every(30) == (false, 30))
        let caps = NotifySettingsRules.perDay(2, regular: 5, now: 9)
        #expect(caps == (2, 2, 2))
        #expect(NotifySettingsRules.perDay(0, regular: 1, now: 1).total == 1)
        #expect(NotifySettingsRules.added(["a"], "  b   c ") == ["a", "b c"])
        #expect(NotifySettingsRules.added(["a"], "a") == ["a"])
        #expect(NotifySettingsRules.added(["a"], "   ") == ["a"])
        #expect(FollowupDelayRules.delays(0, 0) == (1, 1))
        #expect(FollowupDelayRules.delays(30, 15) == (30, 30))
        #expect(FollowupDelayRules.choices([5, 15], current: 7) == [5, 7, 15])
    }

    @Test func proactiveDecodes() throws {
        let p: ProactiveSettings = try decode(#"{"enabled":true,"wake_start":"08:00","daily_total":4,"today":{"regular":1,"now":2}}"#)
        #expect(p.enabled && p.wakeStart == "08:00" && p.wakeEnd == "22:00" && p.dailyTotal == 4)
        #expect(p.today.values.reduce(0, +) == 3)
        #expect(p.mailCheck == true)
        let off: ProactiveSettings = try decode(#"{"mail_check":false}"#)
        #expect(off.mailCheck == false)
    }
}

@Suite struct NotifyRulesTests {
    private func note(_ id: Int64, _ type: String, seen: Bool = false, task: Int64? = nil, schedule: Int64? = nil, message: Int64? = nil) throws -> ServerNotification {
        var o: [String: JSON] = ["id": .number(Double(id)), "type": .string(type), "seen": .bool(seen)]
        if let task { o["task_id"] = .number(Double(task)) }
        if let schedule { o["schedule_id"] = .number(Double(schedule)) }
        if let message { o["message_id"] = .number(Double(message)) }
        return try JSON.decode(ServerNotification.self, from: .object(o))
    }

    @Test func targetsMatchAndroid() throws {
        #expect(NotifyRules.target(try note(1, NoteTypes.approvalWaiting)) == .chat)
        #expect(NotifyRules.target(try note(9, NoteTypes.betaRequest)) == .operatorPage)
        #expect(NotifyRules.target(try note(10, NoteTypes.cliUpdate)) == .modelSettings(""))   // an older server names no provider
        #expect(NotifyRules.target(ServerNotification(id: 11, type: NoteTypes.cliUpdate, provider: "antigravity-oauth")) == .modelSettings("antigravity-oauth"))
        #expect(NotifyRules.target(try note(12, NoteTypes.serverUpdate)) == .serverUpdate)
        #expect(NotifyRules.fresh([try note(13, NoteTypes.serverUpdate)], lastPosted: 0).count == 1)   // posted like the others
        #expect(NotifyRules.target(try note(1, NoteTypes.paymentAuto)) == .wallet)
        #expect(NotifyRules.target(try note(1, NoteTypes.schedulePaused, schedule: 4)) == .work(.schedule(4)))
        #expect(NotifyRules.target(try note(1, NoteTypes.scheduleDisabled)) == .work(.schedules))
        #expect(NotifyRules.target(try note(1, NoteTypes.watchFired)) == .work(.watchers))
        #expect(NotifyRules.target(try note(1, NoteTypes.proactive)) == .chat)
        #expect(NotifyRules.target(try note(1, NoteTypes.followup, message: 9)) == .chatAt(9))
        #expect(NotifyRules.channel(NoteTypes.followup, scheduleId: nil) == .approvals)       // it waits for the owner
        #expect(NotifyRules.channel(NoteTypes.taskDone, scheduleId: nil) == .replies)
        #expect(NotifyRules.target(try note(1, NoteTypes.taskDone, task: 3)) == .work(.task(3)))
        #expect(NotifyRules.channel(NoteTypes.taskDone, scheduleId: 2) == .schedules)
        #expect(NotifyRules.channel(NoteTypes.taskDone, scheduleId: nil) == .replies)
    }

    @Test func freshAndCursor() throws {
        let items = [try note(5, NoteTypes.taskDone), try note(4, NoteTypes.taskDone, seen: true), try note(3, "unknown"), try note(6, NoteTypes.proactive)]
        #expect(NotifyRules.fresh(items, lastPosted: 2).map(\.id) == [3, 5, 6])   // a type the app does not know is kept
        let sec = try note(8, "security")
        #expect(NotifyRules.unknownTitle(sec, hint: "hint") == "hint")
        #expect(NotifyRules.unknownTitle(ServerNotification(id: 9, type: "security", title: "New sign-in"), hint: "hint") == "New sign-in")
        #expect(NotifyRules.unknownTitle(try note(1, NoteTypes.taskDone), hint: "hint") == nil)
        #expect(NotifyRules.fresh(items, lastPosted: 5).map(\.id) == [6])
        #expect(NotifyRules.nextCursor(10, serverLast: 3, items: items) == 10)
        #expect(NotifyRules.nextCursor(1, serverLast: 3, items: items) == 6)
        #expect(NotifyRules.identifier(try note(1, NoteTypes.taskDone, task: 3)) == "task:3")
    }

    @Test func aWaitingNotificationIsIdentifiedByItsItemId() throws {
        let n = try JSON.decode(ServerNotification.self, from: .object(["id": .number(5), "type": .string("approval_waiting"), "task_id": .number(3), "approval_id": .string("ab12-x")]))
        #expect(NotifyRules.itemId(n) == "ab12-x")
        #expect(NotifyRules.identifier(n) == "ab12-x")   // the item's id wins over the task's: one scheme on all three apps
        #expect(NotifyRules.itemId(try note(1, NoteTypes.taskDone, task: 3)) == nil)
        // only notifications that carry an item id are asked about; news and follow-ups are never taken down by "unknown = settled"
        let delivered: [(id: String, userInfo: [AnyHashable: Any])] = [("ab12-x", ["item": "ab12-x"]), ("task:3", ["channel": "replies"]), ("note:9", [:])]
        #expect(NotifyRules.itemsToAskAbout(delivered) == ["ab12-x"])
    }

    @Test func aSettledPushCarriesItemIdsOnly() {
        let p = PushRules.payload(["type": "settled", "id": "ap1", "ids": "ap1, ap2,ap1,bad id,<x>"])
        #expect(p == PushPayload(type: "settled", id: 0, ids: ["ap1", "ap2"]))
        #expect(PushRules.payload(["type": "settled", "id": "ap1"])?.ids == ["ap1"])
        #expect(PushRules.payload(["type": "settled", "ids": (1...30).map { "i\($0)" }.joined(separator: ",")])?.ids.count == 10)
        #expect(PushRules.payload(["type": "task_done", "id": "3", "ids": "a"])?.ids == [])
        // a settled push is not a banner to take down or to open: it has no "open" text, and tapping would open the chat at most
        #expect(PushRules.payload(["type": "task_done", "id": "x"]) == nil)   // other kinds still need a numeric id
    }

    @Test func firstLine() {
        #expect(NotifyRules.firstLine("```\ncode\n```\n## **Done** [here](http://x)") == "Done here")
        #expect(NotifyRules.firstLine("- item one\nsecond") == "item one")
        #expect(NotifyRules.firstLine("---\n\n") == "")
        let long = String(repeating: "가", count: 200)
        #expect(NotifyRules.firstLine(long, max: 10).count == 10)
    }
}

@Suite struct JobUsageTests {
    @Test func modelSharesWithSixModelsGivesTopFourAndOthers() {
        let models = [
            ModelUsage(provider: "p1", model: "m1", inputTokens: 500, outputTokens: 100, cacheReadTokens: 0),
            ModelUsage(provider: "p2", model: "m2", inputTokens: 400, outputTokens: 100, cacheReadTokens: 0),
            ModelUsage(provider: "p3", model: "m3", inputTokens: 300, outputTokens: 100, cacheReadTokens: 0),
            ModelUsage(provider: "p4", model: "m4", inputTokens: 200, outputTokens: 100, cacheReadTokens: 0),
            ModelUsage(provider: "p5", model: "m5", inputTokens: 100, outputTokens: 100, cacheReadTokens: 0),
            ModelUsage(provider: "p6", model: "m6", inputTokens: 50, outputTokens: 50, cacheReadTokens: 0),
        ]
        let shares = UsageLogic.modelShares(models)
        #expect(shares.count == 5)
        #expect(shares[0].model == "m1")
        #expect(shares[1].model == "m2")
        #expect(shares[2].model == "m3")
        #expect(shares[3].model == "m4")
        #expect(shares[4].others == 2)
        #expect(shares[4].tokens == 300)
        let sumPercents = shares.reduce(0) { $0 + $1.percent }
        #expect(abs(sumPercents - 100) <= 1)
    }

    @Test func compactTokensFormatting() {
        #expect(UsageLogic.compactTokens(2_990_000, "ko") == "299만")
        #expect(UsageLogic.compactTokens(2_990_000, "en") == "3M")
        #expect(UsageLogic.compactTokens(8512, "en") == "8,512")
    }

    @Test func usedCalculation() {
        #expect(UsageLogic.used(1000, 200, 900) == 300)
    }

    @Test func backgroundFailureShowsOnlyForTheModelStillChosen() {
        let state = BackgroundModelState(provider: "anthropic", model: "small", lastFallback: BackgroundFallback(at: 1.7e9, kind: "journal", reason: "down"))
        #expect(UsageLogic.fallbackShown(state, provider: "anthropic", model: "small")?.kind == "journal")
        #expect(UsageLogic.fallbackShown(state, provider: "anthropic", model: "other") == nil)
        #expect(UsageLogic.fallbackShown(state, provider: "", model: "") == nil)
        #expect(UsageLogic.fallbackShown(nil, provider: "anthropic", model: "small") == nil)
    }

    @Test func usageDecodesWithAndWithoutJobs() throws {
        let old = try decode(#"{"today":{"input_tokens":1,"output_tokens":2},"total":{"input_tokens":3,"output_tokens":4}}"#, as: MyUsage.self)
        #expect(old.jobs == nil && old.background == nil)
        let now = try decode(#"{"today":{"input_tokens":1,"output_tokens":2},"jobs":{"today":{"calls":2,"input_tokens":200,"output_tokens":20,"cache_read_tokens":120,"kinds":[{"kind":"chat","calls":1,"input_tokens":100,"output_tokens":10,"cache_read_tokens":60}]},"month":{}},"background":{"provider":"","model":"","last_fallback":null}}"#, as: MyUsage.self)
        #expect(now.jobs?.today.calls == 2)
        let p = try decode(#"{"provider":"p","background_provider":"anthropic","background_model":"small"}"#, as: Profile.self)
        #expect(p.backgroundProvider == "anthropic" && p.backgroundModel == "small")
    }
}

@Suite struct UsageLogicTests {
    private func window(_ group: String?, _ label: String, _ used: Int, resetsAt: String? = nil, resetsText: String? = nil) -> UsageWindow {
        UsageWindow(group: group, label: label, usedPercent: used, resetsAt: resetsAt, resetsText: resetsText)
    }

    @Test func groupsPutTheActiveOneFirst() {
        let u = MyUsage(subscription: Subscription(provider: "agy", windows: [
            window("Claude and GPT models", "Weekly Limit Remaining", 10), window("Gemini Models", "Current session", 42),
            window("Gemini Models", "Current week", 80),
        ]))
        let g = UsageLogic.groups(u, model: "gemini-3.8-flash-high") { $0.uppercased() }
        #expect(g.first?.group == "Gemini Models" && g.first?.active == true)
        #expect(g.count == 2)
        #expect(UsageLogic.summaryPercent(g) == 80)
        #expect(UsageLogic.oneLine(g, g[1], full: false))
        #expect(!UsageLogic.oneLine(g, g[1], full: true))
        #expect(UsageLogic.groups(MyUsage(), model: "x") { $0 }.isEmpty)
        #expect(UsageLogic.groupName("Claude and GPT models", models: { "\($0) 모델" }, joiner: " · ") == "Claude · GPT 모델")
        #expect(UsageLogic.label("Current session", [("Current session", "현재 세션")]) == "현재 세션")
        #expect(UsageLogic.activeGroup(["Gemini Models", "Gemini Pro"], model: "gemini-pro") == nil)
    }

    @Test func resetTimes() {
        let now = 1_762_400_000.0   // 2025-11-06T03:33:20Z
        #expect(UsageLogic.parseResetText("in 3 hours", now: now) == now + 3 * 3600)
        let at = UsageLogic.parseResetText("Nov 7, 11:40am (UTC)", now: now)!
        #expect(ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: at)) == "2025-11-07T11:40:00Z")
        let soon = UsageLogic.parseResetText("2:00am (UTC)", now: now)!
        #expect(ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: soon)) == "2025-11-07T02:00:00Z")
        #expect(UsageLogic.parseResetText("whenever", now: now) == nil)
        #expect(UsageLogic.parseTime("2025-11-07T11:40:00Z", zone: .current) != nil)
        #expect(UsageLogic.parseTime("2025-11-07T11:40:00+09:00", zone: .current) == UsageLogic.parseTime("2025-11-07T02:40:00Z", zone: .current))
        #expect(UsageLogic.phrase(.at(epochSeconds: now + 30), now: now) == .soon)
        #expect(UsageLogic.phrase(.at(epochSeconds: now + 600), now: now) == .inMinutes(10))
        #expect(UsageLogic.phrase(.at(epochSeconds: now + 3 * 3600), now: now) == .inHours(3))
        #expect(UsageLogic.phrase(.at(epochSeconds: now + 2 * 86400), now: now) == .on(now + 2 * 86400))
        #expect(UsageLogic.phrase(.text("soon-ish"), now: now) == .raw("soon-ish"))
        #expect(UsageLogic.reset(window(nil, "x", 1, resetsText: "whenever"), now: now) == .text("whenever"))
        #expect(UsageLogic.barFraction(150) == 1 && UsageLogic.isHot(90) && UsageLogic.left(30) == 70)
    }

    @Test func usageDecodesAccounts() throws {
        let u: MyUsage = try decode(#"{"today":{"input_tokens":5,"output_tokens":7},"accounts":[{"provider":"openrouter","balance":"1.5","currency":"USD"}]}"#)
        #expect(u.today.sum == 12)
        #expect(u.accounts.first?.balance == 1.5)
        let p: ModelProvider = try decode(#"{"name":"claude-subscription","signin":true,"signed_in":false,"key_source":"app"}"#)
        #expect(p.signin && p.signedIn == false && p.displayName == "claude-subscription")
    }
}

@Suite struct OverviewAndRoutesTests {
    @Test func offlineOnlyWhenEverythingFailedForWantOfAConnection() {
        #expect(!MyOverview.offline([]))
        #expect(!MyOverview.offline([nil, ApiError(status: 0, detail: "")]))
        #expect(MyOverview.offline([ApiError(status: 0, detail: ""), ApiError(status: 500, detail: "")]))
        #expect(!MyOverview.offline([ApiError(status: 500, detail: ""), ApiError(status: 404, detail: "")]))
    }

    @Test func pageRequestsMapToPages() {
        #expect(MyRoute.of(.wallet) == .wallet)
        #expect(MyRoute.of(.model) == .model)
        #expect(MyRoute.of(.notifications) == .notify)
        #expect(MyRoute.of(.vault) == nil)   // always through the phone's own check
        #expect(MyRoute.of(.language) == .language)
        #expect(MyRoute.of(.serverUpdate) == .serverUpdate)
    }

    @Test func resourcesShowTheMachineWhenNothingWasSet() throws {
        let r: Resources = try decode(#"{"memory":{"used":100},"disk":{"used":50},"host":{"memory":1000,"cpus":4,"disk_free":150}}"#)
        #expect(ResourceRules.ramCeiling(r) == 1000)
        #expect(ResourceRules.cpuCeiling(r) == 4)
        #expect(ResourceRules.diskCeiling(r) == 200)
        #expect(ResourceRules.fraction(50, of: 200) == 0.25)
        #expect(ResourceRules.fraction(1, of: nil) == nil)
        #expect(!ResourceRules.diskFull(r))
        let full: Resources = try decode(#"{"disk":{"used":95,"limit":100}}"#)
        #expect(ResourceRules.diskFull(full))
        #expect(ResourceRules.diskCeiling(full) == 100)
    }

    /// The answer that left the Android page blank (minimum/recommended memory as floats) reads, and the page is never left empty:
    /// loading first, an error with "try again" when the first read fails, the last numbers kept when a later refresh fails.
    @Test func resourcesPageIsNeverBlank() throws {
        let floats = #"{"memory":{"used":109551616,"limit":null},"cpus":null,"disk":{"used":1609985,"limit":null,"parts":{"database":765952},"#
            + #""free_on_host":16335097856},"host":{"memory":12514037760,"cpus":4,"disk_free":16335097856,"disk_total":83953606656},"#
            + #""profile":"unlimited","minimum":{"memory":536870912.0,"cpus":0.5,"disk":5637144576},"#
            + #""recommended":{"memory":1073741824.0,"cpus":1.0,"disk":13555990528}}"#
        let r: Resources = try decode(floats)
        #expect(r.minimum.memory == 536870912 && r.recommended.memory == 1073741824)
        #expect(ResourceRules.ramCeiling(r) == 12514037760)
        #expect(ResourceRules.diskCeiling(r) == Int64(16_336_707_841))   // used + free on the machine

        let start = ResourcesLoad()
        let failed = start.readFailed()
        let retrying = failed.retrying()
        let shown = retrying.loaded(r)
        let refreshFailed = shown.readFailed()
        #expect(refreshFailed == shown && refreshFailed.data == r && !refreshFailed.showError)
    }

    @Test func weeks() throws {
        let thu = LocalDay(year: 2026, month: 11, day: 5)
        let mon = DigestRules.startOf(thu)
        #expect(mon == LocalDay(year: 2026, month: 11, day: 2))
        #expect(DigestRules.startOf(LocalDay(year: 2026, month: 11, day: 8)) == mon)   // Sunday belongs to the week before
        #expect(DigestRules.startOf(mon) == mon)
        #expect(DigestRules.months(LocalDay(year: 2026, month: 10, day: 26)) == ["2026-10", "2026-11"])
        #expect(DigestRules.months(mon) == ["2026-11"])
        #expect(!DigestRules.canGoNext(mon, today: thu))
        #expect(DigestRules.canGoNext(mon.adding(days: -7), today: thu))
        let entries: [JournalEntry] = try decode(#"[{"id":1,"date":"2026-11-03"},{"id":1,"date":"2026-11-03"},{"id":2,"date":"2026-11-08T10:00"},{"id":3,"date":"2026-11-09"}]"#)
        let inside = DigestRules.entries(mon, entries)
        #expect(inside.map(\.id) == [2, 1])
        #expect(DigestRules.perDay(mon, inside) == [0, 1, 0, 0, 0, 0, 1])
    }

    @Test func vaultGate() {
        #expect(VaultGateRules.decide(canEvaluate: true, errorCode: nil) == .prompt)
        #expect(VaultGateRules.decide(canEvaluate: false, errorCode: -5) == .openNoLock)   // LAError.passcodeNotSet
        #expect(VaultGateRules.decide(canEvaluate: false, errorCode: -6) == .prompt)
    }
}

@Suite struct PhoneDataTests {
    @Test func calendarWindowAndSnapshot() {
        let zone = TimeZone(identifier: "Asia/Seoul")!
        let now = 1_762_400_000.0
        let (a, b) = PhoneCalendarRules.window(now: now, zone: zone)
        #expect(b - a == Double(PhoneCalendarRules.daysBack + PhoneCalendarRules.daysAhead + 1) * 86400)
        let rows = [
            PhoneEvent(id: "2", title: "Later", start: now + 100, end: now + 200, allDay: false, location: "Room"),
            PhoneEvent(id: "1", title: "First", start: now, end: now + 50, allDay: false),
            PhoneEvent(id: "1", title: "First", start: now, end: now + 50, allDay: false),
            PhoneEvent(id: "3", title: "No", start: now, end: now + 1, allDay: false, declined: true),
            PhoneEvent(id: "4", title: "Gone", start: now, end: now + 1, allDay: false, cancelled: true),
        ]
        let clean = PhoneCalendarRules.clean(rows, zone: zone)
        #expect(clean.count == 2)
        #expect(clean[0]["title"]?.stringValue == "First")
        #expect(clean[1]["location"]?.stringValue == "Room")
        #expect(clean[0]["location"] == nil)
        // an all-day event at local midnight keeps its own day
        var cal = Calendar(identifier: .gregorian); cal.timeZone = zone
        let day = cal.date(from: DateComponents(year: 2026, month: 11, day: 9))!.timeIntervalSince1970
        let allDay = PhoneCalendarRules.clean([PhoneEvent(id: "5", title: "Trip", start: day, end: day + 2 * 86400, allDay: true)], zone: zone)
        #expect(allDay[0]["day"]?.stringValue == "2026-11-09")
        #expect(allDay[0]["last_day"]?.stringValue == "2026-11-10")
        let snap = PhoneCalendarRules.snapshot(clean, window: (a, b), zone: zone, deviceName: String(repeating: "x", count: 60))
        #expect(snap["can_write"]?.boolValue == false)
        #expect(snap["timezone"]?.stringValue == "Asia/Seoul")
        #expect(snap["device_name"]?.stringValue?.count == 40)
        #expect(snap["events"]?.arrayValue?.count == 2)
    }

    @Test func phoneCalendarSendsNotesAccountAndOrganizerOnlyWhenSet() {
        let zone = TimeZone(identifier: "Asia/Seoul")!
        let row = PhoneEvent(id: "9", title: "Flight to Testville", start: 100, end: 200, allDay: false, notes: "  booking QQ9  ",
                             account: "me@example.test", accountType: "com.google")
        let out = PhoneCalendarRules.clean([row], zone: zone)[0]
        #expect(out["notes"]?.stringValue == "booking QQ9")
        #expect(out["account"]?.stringValue == "me@example.test")
        #expect(out["account_type"]?.stringValue == "com.google")
        let plain = PhoneCalendarRules.clean([PhoneEvent(id: "8", title: "Dentist", start: 100, end: 200, allDay: false)], zone: zone)[0]
        #expect(plain["notes"] == nil && plain["account"] == nil && plain["account_type"] == nil)
    }

    @Test func contactsBody() {
        let rows = [
            ContactRow(name: "Ann", phones: [("010-1234-5678", "mobile"), ("01012345678", "home")], emails: ["a@x", "a@x"], org: "Kelo",
                       birthday: "--05-08"),
            ContactRow(name: " ", phones: [], emails: []),
        ]
        let body = ContactRules.body(rows, device: "iPhone · Kelo")
        let items = body["items"]?.arrayValue ?? []
        #expect(items.count == 1)
        #expect(items[0]["phones"]?.arrayValue?.count == 1)
        #expect(items[0]["emails"]?.arrayValue?.count == 1)
        #expect(items[0]["org"]?.stringValue == "Kelo")
        #expect(items[0]["birthday"]?.stringValue == "--05-08")
        #expect(items[0]["title"] == nil)
        #expect(ContactRules.birthday("19900508") == "1990-05-08")
        #expect(ContactRules.birthday("May 8") == nil)
        #expect(ContactRules.birthday(year: nil, month: 5, day: 8) == "--05-08")
        #expect(ContactRules.birthday(year: 1990, month: 5, day: 8) == "1990-05-08")
        #expect(ContactRules.birthday(year: 1990, month: nil, day: 8) == "")
        #expect(ContactRules.label("_$!<Mobile>!$_") == "mobile")
        #expect(ContactRules.label(nil) == "")
        #expect(PhoneFeature.calendar.path == "/api/phone-calendar")
        #expect(PhoneFeature.contacts.path == "/api/phone/contacts")
        #expect(PhoneFeature.contacts.due(lastAt: 0, now: 10))
        #expect(!PhoneFeature.contacts.due(lastAt: 100, now: 200))
        #expect(PhoneFeature.calendar.due(lastAt: 100, now: 100 + 301))
        #expect(PhoneFeature.allCases == [.calendar, .contacts, .dialer])   // no SMS, call log or notifications on iOS
        #expect(PhoneFeature.dialer.path == "/api/phone/dialer")
    }
}

@Suite struct WalletCryptoTests {
    @Test func secretOnlyTakesDigitsAndHidesItself() {
        #expect(CardSecret.of(number: "4242 4242", cvc: "123") == nil)
        #expect(CardSecret.of(number: "4242424242424242", cvc: "") == nil)
        let s = CardSecret.of(number: "4242424242424242", cvc: "123")!
        #expect(s.last4 == "4242")
        #expect(s.description == "CardSecret(••4242)")
        #expect(!s.description.contains("424242424"))
        s.wipe()
    }

    @Test func blobRoundTripsWithThePhoneKeyOnly() throws {
        let key = SymmetricKey(size: .bits256)
        let id = CardBlob.keyId(key)
        let s = CardSecret.of(number: "4242424242424242", cvc: "123")!
        let blob = try CardBlob.seal(s, key: key, keyId: id)
        #expect(blob.alg == CardBlob.alg && blob.keyId == id)
        #expect(!blob.blob.contains("4242"))
        let back = CardBlob.open(blob, key: key)
        #expect(back?.last4 == "4242")
        #expect(CardBlob.open(blob, key: SymmetricKey(size: .bits256)) == nil)
        #expect(CardBlob.open(E2eBlob(blob: blob.blob, alg: "A256GCM-AKS-1", keyId: id), key: key) == nil)   // an Android blob
        #expect(CardBlob.keyId(key) == id && id.count == 16)
    }

    @Test func parseWipesAndRejectsJunk() {
        var good = Array(#"{"n":"4111111111111111","c":"999"}"#.utf8)
        let s = CardSecret.parse(&good)
        #expect(s?.last4 == "1111")
        #expect(good.allSatisfy { $0 == 0 })
        var bad = Array(#"{"n":"41x1","c":"9"}"#.utf8)
        #expect(CardSecret.parse(&bad) == nil)
    }

    @Test func secretJsonAppendsTheNumberAsBytes() throws {
        let s = CardSecret.of(number: "4242424242424242", cvc: "123")!
        let body = SecretJson.body(["card_id": "c1"], s)
        let parsed = try JSON.parse(body)
        #expect(parsed["card_id"]?.stringValue == "c1")
        #expect(parsed["number"]?.stringValue == "4242424242424242")
        #expect(parsed["cvc"]?.stringValue == "123")
        let empty = try JSON.parse(SecretJson.body(.object([:]), s))
        #expect(empty["number"]?.stringValue == "4242424242424242")
    }

    @Test func lockClosesAfterTwoMinutes() {
        var l = WalletLock()
        var open = l.isOpen(0); #expect(!open)
        l.unlock(10)
        open = l.isOpen(100); #expect(open)
        l.touch(100)
        open = l.isOpen(219); #expect(open)
        open = l.isOpen(221); #expect(!open)
        l.unlock(10)
        open = l.isOpen(5); #expect(!open)   // the clock went backwards
    }

    @Test func deliveryAndFormRules() throws {
        #expect(CardSendError.fromStatus(200) == nil)
        #expect(CardSendError.fromStatus(409) == nil)
        #expect(CardSendError.fromStatus(404) == .notWaiting)
        #expect(CardSendError.fromStatus(422) == .mismatch)
        #expect(CardSendError.fromStatus(0) == .network)
        let now = YearMonth(year: 2026, month: 11)
        let card: WalletCard = try decode(#"{"id":"c","e2e":true,"e2e_alg":"A256GCM-IKC-1","e2e_key_id":"k1"}"#)
        #expect(CardBlob.ours(card, keyId: "k1"))
        #expect(!CardBlob.ours(card, keyId: "k2"))
        #expect(!CardBlob.ours(card, keyId: nil))
    }
}

@Suite struct AppVersionLineTests {
}
