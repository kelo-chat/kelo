import SwiftUI
import KeloCore
import KeloShared

// The designed mail preview in an approval card (Android chat/MailPreviewBlock.kt): the collapsed box in the card, the full view, and
// the link confirm. The document is the sanitized mail the server sends (GET /api/approvals/{id}/mail-preview); the web view loads and
// runs nothing (no script, no network, no navigation), and a tapped link opens outside the app only after the owner confirms it.

/// The app's previews of designed mails (the newest eight stay, see MailPreviewStore).
@MainActor public enum MailPreviews {
    public static let store = MailPreviewStore()

    /// Fetches the card's preview once (the store keeps the answer) and returns its state.
    static func load(_ approvalId: String, _ design: MailDesign, api: KeloApi?) async -> MailPreviewStore.State {
        guard let api else { return .failed }
        return await store.load(approvalId, path: design.previewPath, api: api)
    }
}

#if os(iOS)
import WebKit
import UIKit

/// The web view's content blocking: every load of a web or file address is refused, whatever a document asks for.
enum MailWebBlocking {
    static let identifier = "kelo-mail-preview-block"
    static let rules = #"[{"trigger":{"url-filter":"^(https?|wss?|ftp|file|blob|data):"},"action":{"type":"block"}}]"#

    /// Compiles the rules once and adds them to the controller; `done` says whether they are in place (no load without them).
    @MainActor static func install(on controller: WKUserContentController, done: @escaping @MainActor (Bool) -> Void) {
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: identifier, encodedContentRuleList: rules) { list, _ in
            Task { @MainActor in
                guard let list else { done(false); return }
                controller.removeAllContentRuleLists()
                controller.add(list)
                done(true)
            }
        }
    }
}

/// The preview document in a web view that loads and runs nothing. A tapped link comes back through `onLink`; `onFailure` when the
/// view cannot show the document (the card then says so).
struct MailWebView: UIViewRepresentable {
    let html: String
    let scrollable: Bool
    let onLink: (String) -> Void
    let onFailure: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onLink: onLink, onFailure: onFailure) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.websiteDataStore = .nonPersistent()
        config.dataDetectorTypes = []
        config.allowsInlineMediaPlayback = false
        config.mediaTypesRequiringUserActionForPlayback = .all
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.allowsLinkPreview = false   // a long press must not fetch the page
        web.allowsBackForwardNavigationGestures = false
        web.isOpaque = false
        web.backgroundColor = .white
        web.scrollView.backgroundColor = .white
        web.scrollView.isScrollEnabled = scrollable
        web.isUserInteractionEnabled = scrollable   // the card's overlay takes the tap
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        let c = context.coordinator
        guard c.html != html else { return }   // loaded once per document
        c.html = html
        c.onLink = onLink
        c.onFailure = onFailure
        MailWebBlocking.install(on: web.configuration.userContentController) { ok in
            guard ok else { c.onFailure(); return }
            c.allowFirstLoad = true
            web.loadHTMLString(html, baseURL: nil)
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var html: String?
        var onLink: (String) -> Void
        var onFailure: () -> Void
        /// Only the one load of the document itself is allowed (about:blank, not a link).
        var allowFirstLoad = false

        init(onLink: @escaping (String) -> Void, onFailure: @escaping () -> Void) {
            self.onLink = onLink; self.onFailure = onFailure
        }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            let address = action.request.url?.absoluteString ?? ""
            if allowFirstLoad, action.navigationType == .other, address == "about:blank" {
                allowFirstLoad = false
                decisionHandler(.allow)
                return
            }
            decisionHandler(.cancel)
            if action.navigationType == .linkActivated, MailPreviewRules.linkToOpen(address) != nil { onLink(address) }
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { onFailure() }
    }
}
#else
/// Other platforms (the macOS type-check) draw nothing here: the preview is an iPhone view.
struct MailWebView: View {
    let html: String
    let scrollable: Bool
    let onLink: (String) -> Void
    let onFailure: () -> Void
    var body: some View { EmptyView() }
}
#endif

/// The collapsed preview of a designed mail, in the approval card: the words as they will be sent, in a fixed white box. A tap (or
/// "View larger") opens the full view.
public struct MailPreviewBlock: View {
    let approvalId: String
    let design: MailDesign
    @Environment(\.keloApi) private var api
    @Environment(\.kelo) private var c
    @State private var state: MailPreviewStore.State = .loading
    @State private var full = false
    @State private var link: String?

    public init(approvalId: String, design: MailDesign) {
        self.approvalId = approvalId; self.design = design
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L(R.string.mail_preview_title)).font(KeloFont.label).foregroundStyle(c.muted)
            content
        }
        .task(id: approvalId) { state = await MailPreviews.load(approvalId, design, api: api) }
        .sheet(isPresented: $full) {
            MailPreviewFull(approvalId: approvalId, design: design)
        }
        .modifier(MailLinkConfirm(link: $link))
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .ready(let p):
            ZStack(alignment: .bottom) {
                MailWebView(html: p.html, scrollable: false, onLink: { link = $0 }, onFailure: { state = .failed })
                    .allowsHitTesting(false)
                LinearGradient(colors: [.white.opacity(0), .white], startPoint: .top, endPoint: .bottom)
                    .frame(height: 32)
                    .allowsHitTesting(false)
            }
            .frame(height: 220)
            .frame(maxWidth: .infinity)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(c.line, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 10))
            .onTapGesture { full = true }
            .accessibilityAddTraits(.isButton)
            .accessibilityLabel(L(R.string.mail_preview_desc))
            LinkButton(L(R.string.mail_preview_open)) { full = true }
            if p.pictures > 0 { MutedText(L(R.string.mail_preview_pictures_note), small: true) }
        case .loading:
            VStack(alignment: .leading, spacing: 8) {
                MutedText(L(R.string.mail_preview_loading), small: true)
                SkeletonRows(rows: 4)
            }
            .padding(12)
            .frame(height: 220, alignment: .top)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(c.line, lineWidth: 1))
        case .gone:
            MutedText(L(R.string.mail_preview_gone), small: true)
        case .tooBig:
            MutedText(L(R.string.mail_preview_too_big), small: true)
        case .failed:
            HStack(spacing: 8) {
                MutedText(L(R.string.mail_preview_failed), small: true)
                LinkButton(L(R.string.retry)) {
                    state = .loading
                    Task { state = await MailPreviews.load(approvalId, design, api: api) }
                }
            }
        }
    }
}

/// The link confirm: a tapped link opens outside the app only when the owner says so here. The address is shown in full.
struct MailLinkConfirm: ViewModifier {
    @Binding var link: String?
    @Environment(\.openURL) private var openURL

    func body(content: Content) -> some View {
        content.alert(L(R.string.mail_preview_link_title), isPresented: Binding(get: { link != nil }, set: { if !$0 { link = nil } })) {
            Button(L(R.string.mail_preview_link_open)) {
                if let l = link, let url = URL(string: l) { openURL(url) }
                link = nil
            }
            Button(L(R.string.cancel), role: .cancel) { link = nil }
        } message: {
            Text(link.map { MailPreviewRules.linkLabel($0) + "\n" + $0 } ?? "")
        }
    }
}

/// The full view of a designed mail: the whole document, scrollable (a sheet, Close at the top).
struct MailPreviewFull: View {
    let approvalId: String
    let design: MailDesign
    @Environment(\.keloApi) private var api
    @Environment(\.kelo) private var c
    @Environment(\.dismiss) private var dismiss
    @State private var state: MailPreviewStore.State = .loading
    @State private var link: String?

    var body: some View {
        NavigationStack {
            Group {
                switch state {
                case .ready(let p):
                    MailWebView(html: p.html, scrollable: true, onLink: { link = $0 }, onFailure: { state = .failed })
                        .ignoresSafeArea(edges: .bottom)
                case .failed:
                    MutedText(L(R.string.mail_preview_failed)).padding(16)
                case .gone:
                    MutedText(L(R.string.mail_preview_gone)).padding(16)
                case .tooBig:
                    MutedText(L(R.string.mail_preview_too_big)).padding(16)
                case .loading:
                    MutedText(L(R.string.mail_preview_loading)).padding(16)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(Color.white)
            .navigationTitle(L(R.string.mail_preview_title))
            .modifier(InlineNavTitle())
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L(R.string.close)) { dismiss() }
                }
            }
        }
        .task(id: approvalId) { state = await MailPreviews.load(approvalId, design, api: api) }
        .modifier(MailLinkConfirm(link: $link))
    }
}

/// The iPhone's inline title (macOS has no such mode).
private struct InlineNavTitle: ViewModifier {
    func body(content: Content) -> some View {
        #if os(iOS)
        content.navigationBarTitleDisplayMode(.inline)
        #else
        content
        #endif
    }
}
