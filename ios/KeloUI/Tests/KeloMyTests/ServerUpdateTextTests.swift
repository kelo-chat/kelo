import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloMy

/// The server update's words on 마이 and its page (Android my/ServerUpdatePage.kt): every step and card has its own line, in Korean
/// too, and the routes to the page.
@MainActor @Suite(.serialized) struct ServerUpdateTextTests {
    private func job(_ step: String) -> ServerUpdateJob { ServerUpdateJob(id: "j", state: "running", step: step, progress: 40) }

    @Test func everyStepHasItsOwnWords() {
        for lang in ["en", "ko"] {
            Strings.shared.use(lang)
            let words = ServerUpdateRules.steps.map { ServerUpdateText.step(job($0)) }
            #expect(Set(words).count == ServerUpdateRules.steps.count)
            #expect(!words.contains(""))
            #expect(ServerUpdateText.step(job("something_new")) == L(R.string.server_update_step_working))
        }
        Strings.shared.use("en")
    }

    @Test func cardLines() {
        Strings.shared.use("en")
        #expect(ServerUpdateText.card(.available("0.5.0")) == "Server update available: 0.5.0")
        #expect(ServerUpdateText.card(.running(rollback: false, progress: 40, to: "0.5.0")) == "Updating the server… 40%")
        #expect(ServerUpdateText.card(.running(rollback: true, progress: 7, to: "0.3.9")) == L(R.string.server_update_card_going_back, 7))
        #expect(ServerUpdateText.card(.failed(rollback: false)) == L(R.string.server_update_card_failed))
        #expect(ServerUpdateText.card(.failed(rollback: true)) == L(R.string.server_update_go_back_failed))
        #expect(ServerUpdateText.card(.rolledBack(from: "0.4.0", to: "0.5.0")) == L(R.string.server_update_card_rolled_back))
        #expect(ServerUpdateText.card(.rollbackFailed) == L(R.string.server_update_card_rollback_failed))
        Strings.shared.use("ko")
        #expect(ServerUpdateText.card(.running(rollback: false, progress: 40, to: nil)) == "서버를 업데이트하는 중… 40%")
        // the owner's words: the server machine, never "my computer"
        #expect(!L(R.string.server_update_card_rollback_failed).contains("내 컴퓨터"))
        #expect(L(R.string.server_update_card_rollback_failed).contains("서버 컴퓨터"))
        Strings.shared.use("en")
    }

    @Test func blankTextCountsAsNone() {
        #expect(ServerUpdateText.nonEmpty("  \n") == nil)
        #expect(ServerUpdateText.nonEmpty(nil) == nil)
        #expect(ServerUpdateText.nonEmpty(" 0.5.0 ") == "0.5.0")
    }

    @Test func aNoticeOpensThePage() {
        let r = Router()
        r.open(.serverUpdate)
        #expect(r.tab == .my)
        #expect(r.myPageRequest == .serverUpdate)
        #expect(MyRoute.of(.serverUpdate) == .serverUpdate)
    }
}
