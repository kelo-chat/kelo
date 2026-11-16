import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// The phone card (Android chat/PhoneCardTest.kt): a `device` turn event is the browser card's sibling, one per reply, updated in place.
@Suite struct PhoneCardTests {
    final class Keys { var n = 0; func next() -> String { n += 1; return "k\(n)" } }
    let shot = "data:image/jpeg;base64,AQ=="

    func run(_ evs: [TurnEvent], start: [ChatItem] = [], keys: Keys = Keys()) -> [ChatItem] {
        evs.reduce(start) { items, ev in ChatReducer.apply(items, ev, newKey: keys.next) }
    }
    func device(_ id: String, _ status: String, image: String? = nil, w: Int = 0, h: Int = 0) -> TurnEvent {
        .device(.init(id: id, status: status, image: image, width: w, height: h))
    }
    func cards(_ rows: [ChatItem]) -> [ChatItem.Browser] { rows.compactMap { if case .browser(let b) = $0 { return b }; return nil } }

    @Test func aDeviceEventBecomesAPhoneCardForTheKeyAndroid() {
        let card = cards(run([device("c1", "휴대폰에서 누르는 중", image: shot, w: 360, h: 780)])).first
        #expect(card?.device == true && card?.tab == "android" && card?.status == "휴대폰에서 누르는 중")
        #expect(card?.width == 360 && card?.height == 780 && card?.image == shot)
    }

    @Test func everyStepUpdatesTheOneCardInPlaceAndKeepsTheLastPicture() {
        let k = Keys()
        let items = run([device("c1", "휴대폰 화면 보는 중", image: shot, w: 360, h: 780), .text("잠시만요"), device("c2", "휴대폰에서 누르는 중")], keys: k)
        let all = cards(items)
        #expect(all.count == 1 && all[0].key == "k1" && all[0].status == "휴대폰에서 누르는 중")
        #expect(all[0].image == shot && all[0].height == 780)
        let user = ChatItem.msg(.init(key: k.next(), mine: true, text: "또"))
        #expect(cards(run([device("c3", "s")], start: items + [user], keys: k)).count == 2)   // the next reply starts its own card
    }

    @Test func aPhoneCardAndABrowserCardDoNotMerge() {
        let items = run([
            .browser(.init(id: "b1", title: "A", host: "a.example", status: "사이트 보는 중", image: nil, width: 0, height: 0, tab: "owner")),
            device("c1", "휴대폰 화면 보는 중"),
            .browser(.init(id: "b2", title: "A", host: "a.example", status: "페이지 누르는 중", image: nil, width: 0, height: 0)),   // an older server: no tab
            device("c2", "휴대폰에서 누르는 중"),
        ])
        let all = cards(items)
        #expect(all.map(\.device) == [false, true])
        #expect(all[0].status == "페이지 누르는 중" && all[1].status == "휴대폰에서 누르는 중")
    }

    @Test func phoneStepsShowAsTheCardNotAsChips() {
        func started(_ id: String, _ name: String) -> TurnEvent { .toolStarted(.init(id: id, name: name, arguments: [:], label: "\(name)…", group: name, done: "")) }
        func ok(_ id: String, _ name: String) -> TurnEvent { .toolResult(.init(id: id, name: name, output: "ok", isError: false, approved: nil)) }
        let rows = ChatReducer.foldTools(run([
            started("t1", "android_look"), device("t1", "휴대폰 화면 보는 중"), ok("t1", "android_look"),
            started("t2", "android_tap"), device("t2", "휴대폰에서 누르는 중"), ok("t2", "android_tap"),
        ]))
        #expect(rows.count == 1 && cards(rows).first?.device == true)
        // without a card (an older server) the steps stay visible
        let bare = ChatReducer.foldTools(run([started("x1", "android_look"), ok("x1", "android_look")]))
        guard case .tool = bare[0] else { Issue.record("a chip was expected"); return }
    }

    @Test func thePhonePictureIsPortraitWithTheDevicesProportions() {
        #expect(abs(PhoneShape.aspect(width: 360, height: 780) - 360.0 / 780.0) < 0.001)
        #expect(abs(PhoneShape.aspect(width: 100, height: 1000) - 0.4) < 0.001)
        #expect(abs(PhoneShape.aspect(width: 1000, height: 1000) - 0.8) < 0.001)
        #expect(abs(PhoneShape.aspect(width: 0, height: 0) - 9.0 / 19.5) < 0.001)
    }
}
