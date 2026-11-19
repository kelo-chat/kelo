import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloWork

@Suite struct WatcherLastResultTests {
    @Test func newerOfLastCheckAndDryRunIsShownWhenLastCheckIsNewer() {
        let dry = DryRun(at: 100.0, ok: true, detail: "dry run finding")
        let check = LastCheck(at: 200.0, detail: "latest check finding")
        let w = Watcher(id: 1, dryRun: dry, lastCheck: check)

        let res = watchLastResult(w)
        #expect(res != nil)
        #expect(res?.text == "latest check finding")
        #expect(res?.at == 200.0)
        #expect(res?.isError == false)
    }

    @Test func newerOfLastCheckAndDryRunIsShownWhenDryRunIsNewer() {
        let dry = DryRun(at: 300.0, ok: true, detail: "manual check finding")
        let check = LastCheck(at: 200.0, detail: "older scheduled check")
        let w = Watcher(id: 1, dryRun: dry, lastCheck: check)

        let res = watchLastResult(w)
        #expect(res != nil)
        #expect(res?.text == "manual check finding")
        #expect(res?.at == 300.0)
        #expect(res?.isError == false)
    }

    @Test func showsLastCheckWhenDryRunIsNil() {
        let check = LastCheck(at: 200.0, detail: "scheduled check only")
        let w = Watcher(id: 1, dryRun: nil, lastCheck: check)

        let res = watchLastResult(w)
        #expect(res != nil)
        #expect(res?.text == "scheduled check only")
        #expect(res?.at == 200.0)
    }

    @Test func showsDryRunWhenLastCheckIsNil() {
        let dry = DryRun(at: 150.0, ok: true, detail: "dry run only")
        let w = Watcher(id: 1, dryRun: dry, lastCheck: nil)

        let res = watchLastResult(w)
        #expect(res != nil)
        #expect(res?.text == "dry run only")
        #expect(res?.at == 150.0)
    }

    @Test func returnsNilWhenBothAreNil() {
        let w = Watcher(id: 1, dryRun: nil, lastCheck: nil)
        #expect(watchLastResult(w) == nil)
    }

    @Test func errorTextAndDangerFlagArePreserved() {
        let checkErr = LastCheck(at: 400.0, error: "Connection failed")
        let wErr = Watcher(id: 1, lastCheck: checkErr)
        let resErr = watchLastResult(wErr)
        #expect(resErr != nil)
        #expect(resErr?.text == "Connection failed")
        #expect(resErr?.isError == true)

        let dryErr = DryRun(at: 500.0, ok: false, error: "HTTP 404")
        let wDryErr = Watcher(id: 2, dryRun: dryErr)
        let resDryErr = watchLastResult(wDryErr)
        #expect(resDryErr != nil)
        #expect(resDryErr?.text == "HTTP 404")
        #expect(resDryErr?.isError == true)
    }

    @Test func decodesWatcherWithLastCheck() throws {
        let json = """
        {
            "id": 10,
            "name": "Check Test",
            "last_check": {
                "at": 1700001000.0,
                "detail": "가격이 50,000원으로 확인되었어요",
                "value": "50,000",
                "hit": true,
                "error": null
            }
        }
        """
        let w = try JSONDecoder().decode(Watcher.self, from: Data(json.utf8))
        #expect(w.id == 10)
        let lc = try #require(w.lastCheck)
        #expect(lc.at == 1700001000.0)
        #expect(lc.detail == "가격이 50,000원으로 확인되었어요")
        #expect(lc.value == "50,000")
        #expect(lc.hit == true)
        #expect(lc.error == nil)
    }

    @Test func decodesWatcherEventWithCountAndDefaultsToOne() throws {
        let jsonWithCount = """
        {
            "id": 1,
            "at": 1700002000.0,
            "kind": "checked",
            "detail": "1,250,000원",
            "value": "1,250,000",
            "count": 5
        }
        """
        let ev1 = try JSONDecoder().decode(WatcherEvent.self, from: Data(jsonWithCount.utf8))
        #expect(ev1.id == 1)
        #expect(ev1.kind == "checked")
        #expect(ev1.count == 5)

        let jsonWithoutCount = """
        {
            "id": 2,
            "at": 1700002000.0,
            "kind": "fired",
            "detail": "980,000원",
            "value": "980,000"
        }
        """
        let ev2 = try JSONDecoder().decode(WatcherEvent.self, from: Data(jsonWithoutCount.utf8))
        #expect(ev2.id == 2)
        #expect(ev2.kind == "fired")
        #expect(ev2.count == 1)
    }

    @MainActor @Test func checkedEventWordAndCountText() {
        withLanguage("ko") {
            #expect(WorkText.watchEvent("checked") == "확인")
            #expect(L(R.string.work_watch_ev_count, 3) == "· 3번 확인")
        }
        withLanguage("en") {
            #expect(WorkText.watchEvent("checked") == "Checked")
            #expect(L(R.string.work_watch_ev_count, 3) == "· Checked 3 times")
        }
    }
}
