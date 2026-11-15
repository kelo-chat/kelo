import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloMy

/// The storage warning, the cleanup screen's choices and the storage notification (Android my/StorageRulesTest.kt).
@MainActor @Suite(.serialized) struct StorageRulesTests {
    private func decode<T: Decodable>(_ json: String, as: T.Type) throws -> T { try JSONDecoder().decode(T.self, from: Data(json.utf8)) }

    private var list: CleanupList {
        get throws {
            try decode("""
            {"groups":[
              {"id":"backups","name":"Older backups","bytes":3000,"count":1,"items":[{"id":"backups:update/b1","name":"0.1.4","bytes":3000,"date":1.7e9,"kind":"update"}]},
              {"id":"uploads","name":"Old upload originals","bytes":8000,"count":2,"items":[
                {"id":"uploads:a.pdf","name":"a.pdf","bytes":5000,"date":1700000000.5,"keeps_text":true},
                {"id":"uploads:b.jpg","name":"b.jpg","bytes":3000,"date":1700000000}]},
              {"id":"images","name":"Generated images","bytes":0,"count":0,"items":[]},
              {"id":"future_kind","name":"Something new","bytes":10,"count":1,"items":[{"id":"future_kind:x","name":"x","bytes":10,"date":0}]},
              {"id":"results","name":"Old results and trash","bytes":600,"count":1,"items":[{"id":"results:trash/9","name":"gone.txt","bytes":600,"date":1.7e9,"trashed":true}]}
            ],"total":11610,"older_than_days":14}
            """, as: CleanupList.self)
        }
    }

    @Test func theAlertMapsToAStateWithTheServersOwnDiskWorst() {
        #expect(StorageRules.state(nil) == .ok)
        #expect(StorageRules.state(StorageAlert()) == .ok)
        #expect(StorageRules.state(StorageAlert(level: "warn", percent: 82)) == .warn)
        #expect(StorageRules.state(StorageAlert(level: "critical", percent: 96)) == .critical)
        #expect(StorageRules.state(StorageAlert(level: "warn", hostLow: true)) == .hostLow)
        #expect(StorageRules.urgent(.critical) && StorageRules.urgent(.hostLow))
        #expect(!StorageRules.urgent(.warn) && !StorageRules.urgent(.ok))
        for s in StorageState.allCases { #expect(StorageRules.fromKey(StorageRules.key(s)) == s) }
        #expect(StorageRules.key(.ok) == nil)
    }

    @Test func theResourcesAnswerReadsTheAlertAndAnOlderServersAnswerSaysNothing() throws {
        let now = try decode("""
            {"disk":{"used":900,"limit":1000,"parts":{"uploads":600}},"alert":{"level":"critical","percent":96,"host_low":false,"top":"uploads"},"can_clean":true}
            """, as: Resources.self)
        #expect(StorageRules.state(now.alert) == .critical && now.alert.top == "uploads" && now.canClean)
        let older = try decode(#"{"disk":{"used":900,"limit":1000}}"#, as: Resources.self)
        #expect(StorageRules.state(older.alert) == .ok && !older.canClean)
    }

    @Test func groupsShowInAFixedOrderEmptyOnesHiddenUnknownOnesLast() throws {
        let l = try list
        #expect(StorageRules.shown(l).map(\.id) == ["uploads", "results", "backups", "future_kind"])
        #expect(l.groups.first { $0.id == "uploads" }?.items.first?.keepsText == true)
        #expect(StorageRules.shown(nil).isEmpty)
    }

    @Test func tickingItemsAndWholeGroups() throws {
        let l = try list
        let uploads = l.groups.first { $0.id == "uploads" }!
        var sel = StorageRules.toggle([], "uploads:a.pdf")
        #expect(!StorageRules.groupChecked(sel, uploads))
        #expect(StorageRules.selectedBytes(l, sel) == 5000)
        sel = StorageRules.toggleGroup(sel, uploads)
        #expect(StorageRules.groupChecked(sel, uploads) && StorageRules.selectedBytes(l, sel) == 8000)
        sel = StorageRules.toggleGroup(sel, uploads)
        #expect(sel.isEmpty)
        #expect(!StorageRules.groupChecked([], l.groups.first { $0.id == "images" }!))
        let fresh = CleanupList(groups: l.groups.filter { $0.id != "uploads" })
        #expect(StorageRules.keep(["uploads:a.pdf", "results:trash/9"], fresh) == ["results:trash/9"])
    }

    @Test func aStorageNotificationOpensTheResourcesPageAndOffersCleanup() {
        let n = ServerNotification(id: 7, type: NoteTypes.storage, title: "Kelo storage is 82% full", body: "820MB of 1GB used.")
        #expect(NotifyRules.target(n) == .storage)
        #expect(NotifyRules.actions(NoteTypes.storage) == [.storageCleanup])
        #expect(NotifyRules.actions(NoteTypes.taskDone).isEmpty)
        #expect(OpenTarget.decode(OpenTarget.storage.encoded) == .storage)
        #expect(OpenTarget.decode(OpenTarget.storageCleanup.encoded) == .storageCleanup)
        #expect(!NotifyRules.fresh([n], lastPosted: 0).isEmpty)
        #expect(MyRoute.of(.resources) == .resources && MyRoute.of(.cleanup) == .cleanup)
    }

    @Test func aFullDiskIsNotRetriedLikeAPassingServerError() {
        #expect(!ApiError(status: 507, detail: "full").isTransient)
        #expect(ApiError(status: 503, detail: "busy").isTransient)
    }

    @Test func everyWarningAndGroupHasWordsInEnglishAndKorean() {
        for lang in ["en", "ko"] {
            Strings.shared.use(lang)
            let words = [L(R.string.storage_warn, 82), L(R.string.storage_critical, 96), L(R.string.storage_host_low, "512MB"),
                         L(R.string.storage_clean_up), L(R.string.storage_group_uploads), L(R.string.storage_group_backups)]
            #expect(!words.contains("") && Set(words).count == words.count)
            #expect(L(R.string.storage_warn, 82).contains("82"))
        }
        Strings.shared.use("en")
    }
}
