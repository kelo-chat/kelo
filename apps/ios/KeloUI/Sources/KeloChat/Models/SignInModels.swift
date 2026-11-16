import SwiftUI
import Observation
import KeloCore
import KeloShared

/// 신고하기: the report that leaves the phone only when the person sends it.
@MainActor @Observable
final class ReportModel {
    private(set) var busy = false
    private(set) var err = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    /// `sent` runs when the server took it; a failure keeps the draft (`failed`) so the next try starts from it.
    func send(_ cur: ReportDraft, reply: String, asked: String?, version: String, sent: @escaping () -> Void, failed: @escaping (ReportDraft) -> Void) {
        busy = true; err = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                _ = try await api.request("POST", "/api/reports", body: ReportRules.body(cur, reply: reply, asked: asked, appVersion: version))
                sent()
            } catch let e as ApiError {
                err = e.isNetwork ? L(R.string.report_failed_offline) : e.detail
                failed(cur)
            } catch {
                err = L(R.string.report_failed_offline)
            }
        }
    }
}
