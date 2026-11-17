import SwiftUI
import WebKit
import KeloCore
import KeloShared

/// The files the VNC page may load: only what is under the bundled `vnc` folder (noVNC and Kelo's page), served as `kelo-vnc://app/…`.
/// Nothing else is ever loaded by that web view (every other address is refused, and a path that climbs out of the folder is a 404).
enum VncAssets {
    static let scheme = "kelo-vnc"
    static let host = "app"
    static let page = URL(string: "\(scheme)://\(host)/view.html")!

    struct Answer: Equatable {
        let status: Int
        let mime: String
        let body: Data
    }

    /// The bundled folder.
    static var root: URL { Bundle.module.resourceURL!.appendingPathComponent("vnc", isDirectory: true) }

    static func mime(for name: String) -> String {
        switch (name as NSString).pathExtension.lowercased() {
        case "html": return "text/html"
        case "js", "mjs": return "text/javascript"
        case "css": return "text/css"
        case "json": return "application/json"
        case "md", "txt": return "text/plain"
        default: return "application/octet-stream"
        }
    }

    /// The answer for `url` out of the folder `root`.
    static func answer(for url: URL, root: URL = VncAssets.root) -> Answer {
        let notFound = Answer(status: 404, mime: "text/plain", body: Data())
        guard url.scheme == scheme, url.host == host else { return notFound }
        let parts = url.path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard !parts.isEmpty, !parts.contains(where: { $0 == ".." || $0 == "." || $0.contains("\\") }) else { return notFound }
        let file = parts.reduce(root) { $0.appendingPathComponent($1) }.standardizedFileURL
        let base = root.standardizedFileURL.path
        var isDir: ObjCBool = false
        guard file.path.hasPrefix(base + "/"), FileManager.default.fileExists(atPath: file.path, isDirectory: &isDir), !isDir.boolValue,
              let data = try? Data(contentsOf: file) else { return notFound }
        return Answer(status: 200, mime: mime(for: file.lastPathComponent), body: data)
    }

    /// Only the page's own address may be navigated to.
    static func allows(_ url: URL?) -> Bool { url?.scheme == scheme && url?.host == host }
}

/// Serves `VncAssets` to a web view (so the page's ES modules load from a real origin instead of a file).
final class VncSchemeHandler: NSObject, WKURLSchemeHandler {
    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let url = task.request.url ?? VncAssets.page
        let a = VncAssets.answer(for: url)
        let headers = ["Content-Type": a.mime + "; charset=utf-8", "Content-Length": String(a.body.count), "Cache-Control": "no-store"]
        let response = HTTPURLResponse(url: url, statusCode: a.status, httpVersion: "HTTP/1.1", headerFields: headers)
            ?? HTTPURLResponse()
        task.didReceive(response)
        task.didReceive(a.body)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}

/// The web view of the VNC screen and its bridge to a `VncRelay` (see `VncPageBridge`): page -> app is the script message handler
/// `keloVnc`, app -> page is `window.keloVncReceive(…)`.
@MainActor
final class VncWebHost: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    let webView: WKWebView
    private let bridge: VncPageBridge
    static let handlerName = "keloVnc"

    init(relay: VncRelay) {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.setURLSchemeHandler(VncSchemeHandler(), forURLScheme: VncAssets.scheme)
        let web = WKWebView(frame: .zero, configuration: config)
        webView = web
        bridge = VncPageBridge(relay: relay) { [weak web] script in web?.evaluateJavaScript(script, completionHandler: nil) }
        super.init()
        // a weak proxy: the controller keeps its handlers alive, so the host is not kept by the web view it owns
        config.userContentController.add(WeakScriptHandler(self), name: VncWebHost.handlerName)
        web.navigationDelegate = self
        #if os(iOS)
        web.isOpaque = false
        web.backgroundColor = .clear
        web.scrollView.backgroundColor = .clear
        web.scrollView.isScrollEnabled = false
        web.scrollView.bounces = false
        web.scrollView.pinchGestureRecognizer?.isEnabled = false
        #else
        web.setValue(false, forKey: "drawsBackground")
        #endif
        bridge.start()
        web.load(URLRequest(url: VncAssets.page))
    }

    func hold(_ on: Bool) { bridge.hold(on) }

    func release() {
        bridge.release()
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.configuration.userContentController.removeScriptMessageHandler(forName: VncWebHost.handlerName)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if let text = message.body as? String { bridge.pageMessage(text) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        VncAssets.allows(action.request.url) ? .allow : .cancel
    }
}

private final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(c, didReceive: message)
    }
}

/// noVNC in a web view, drawing the tab's X screen. `holding` = the owner holds the tab (the page lets input through only then).
struct VncScreenView {
    let relay: VncRelay
    let holding: Bool

    @MainActor func make() -> VncWebHost { let h = VncWebHost(relay: relay); h.hold(holding); return h }
}

#if os(iOS)
extension VncScreenView: UIViewRepresentable {
    func makeCoordinator() -> Holder { Holder() }
    func makeUIView(context: Context) -> WKWebView {
        let host = make()
        context.coordinator.host = host
        return host.webView
    }
    func updateUIView(_ view: WKWebView, context: Context) { context.coordinator.host?.hold(holding) }
    static func dismantleUIView(_ view: WKWebView, coordinator: Holder) { coordinator.host?.release(); coordinator.host = nil }
}
#else
extension VncScreenView: NSViewRepresentable {
    func makeCoordinator() -> Holder { Holder() }
    func makeNSView(context: Context) -> WKWebView {
        let host = make()
        context.coordinator.host = host
        return host.webView
    }
    func updateNSView(_ view: WKWebView, context: Context) { context.coordinator.host?.hold(holding) }
    static func dismantleNSView(_ view: WKWebView, coordinator: Holder) { coordinator.host?.release(); coordinator.host = nil }
}
#endif

extension VncScreenView {
    @MainActor final class Holder { var host: VncWebHost? }
}
