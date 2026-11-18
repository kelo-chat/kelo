import Foundation

extension BrowserTab {
    /// A browser the agent works in, not the Android device. The device's kind is "device" too, as a phone's chat tab is, so the kind
    /// cannot tell them apart; the source and the id do ("android"). An older server sends no source: the id still says it.
    public var isBrowserTab: Bool { (source.isEmpty || source == "browser") && !LiveKinds.isDevice(id) }

    /// A tab of Kelo's work browser, apart from the owner's own tabs (an older server: never).
    public var inWorkBrowser: Bool { browser == "work" }
}
