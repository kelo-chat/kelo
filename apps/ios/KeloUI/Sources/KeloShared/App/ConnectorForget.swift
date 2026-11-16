import SwiftUI
import KeloCore

// After a connector is disconnected, the app asks whether to forget the data that came from it too (Android connector forget). The
// question is shown by the page that made the disconnect; the server deletes nothing until the owner says yes.

/// The question for one disconnected connection: its name (the connector id on the server) and what is still kept from it.
public struct ForgetAsk: Equatable, Identifiable, Sendable {
    public let item: String
    public let name: String
    public let counts: ConnectorDataCounts
    public var id: String { item }

    public init(item: String, name: String, counts: ConnectorDataCounts) {
        self.item = item; self.name = name; self.counts = counts
    }
}

public enum ForgetRules {
    /// The question for a disconnect's answer: nil when it carried no data (an older server) or nothing is left to forget.
    public static func ask(item: String, name: String, data: ConnectorDataCounts?) -> ForgetAsk? {
        guard let data, data.any else { return nil }
        return ForgetAsk(item: item, name: name, counts: data)
    }

    /// Disconnects `item` (DELETE /api/credentials/{item}) and returns the question its answer leaves; nil when the disconnect failed or
    /// nothing is left.
    @MainActor public static func disconnect(_ api: KeloApi, item: String, name: String) async -> ForgetAsk? {
        ask(item: item, name: name, data: try? await api.disconnect(item: item))
    }

    /// The counts in words, zero parts left out ("파일 3개 · 메모 2개 · 항목 5개"), and a line that files go to the trash when there are files.
    @MainActor public static func counts(_ c: ConnectorDataCounts) -> String {
        var parts: [String] = []
        if c.files > 0 { parts.append(L(R.string.connector_forget_files, c.files)) }
        if c.notes > 0 { parts.append(L(R.string.connector_forget_notes, c.notes)) }
        if c.items > 0 { parts.append(L(R.string.connector_forget_items, c.items)) }
        return parts.joined(separator: " · ")
    }

    @MainActor public static func body(_ c: ConnectorDataCounts) -> String {
        var lines = [counts(c)]
        if c.files > 0 { lines.append(L(R.string.connector_forget_trash)) }
        return lines.joined(separator: "\n")
    }
}

/// Hands a disconnect's question up to the page that shows it: the connector page's panels report through it (set in the environment
/// by the page), so a panel does not need to know which page it is on.
public struct ForgetReporter: Sendable {
    public let show: @MainActor @Sendable (ForgetAsk) -> Void

    public init(_ show: @escaping @MainActor @Sendable (ForgetAsk) -> Void) { self.show = show }

    /// Nothing is asked (no page above it shows the question).
    public static let none = ForgetReporter { _ in }
}

private struct ForgetReporterKey: EnvironmentKey {
    static let defaultValue = ForgetReporter.none
}

public extension EnvironmentValues {
    var forgetReporter: ForgetReporter {
        get { self[ForgetReporterKey.self] }
        set { self[ForgetReporterKey.self] = newValue }
    }
}

public extension View {
    /// "Also delete the data from <name>?": yes deletes it at the server (POST /api/connectors/{id}/forget), then "지웠어요" floats up;
    /// no keeps it. Shown while `ask` is set.
    func forgetAsk(_ ask: Binding<ForgetAsk?>, api: KeloApi) -> some View {
        modifier(ForgetAskModifier(ask: ask, api: api))
    }
}

private struct ForgetAskModifier: ViewModifier {
    @Binding var ask: ForgetAsk?
    let api: KeloApi
    @State private var notice: String?

    func body(content: Content) -> some View {
        content
            .alert(ask.map { L(R.string.connector_forget_title, $0.name) } ?? "",
                   isPresented: Binding(get: { ask != nil }, set: { if !$0 { ask = nil } })) {
                Button(L(R.string.connector_forget_delete), role: .destructive) {
                    guard let a = ask else { return }
                    ask = nil
                    Task { @MainActor in
                        do {
                            let gone = try await api.forgetConnector(a.item)
                            notice = L(R.string.connector_forget_done, ForgetRules.counts(gone))
                        } catch {
                            notice = ApiMessage.text(error)
                        }
                    }
                }
                Button(L(R.string.connector_forget_keep), role: .cancel) {}
            } message: {
                Text(ask.map { ForgetRules.body($0.counts) } ?? "")
            }
            .keloToast($notice)
    }
}
