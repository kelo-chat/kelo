import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 휴대폰 기기 (Android my/AndroidDevice.kt): what the page and the row decide from GET /api/android.
public enum AndroidDeviceRules {
    /// The README section that says how to set the device up (the page's link).
    public static let readmeUrl = "https://github.com/kelo-chat/kelo#android"
    /// The live view key of the device (the same one the chat's phone card opens).
    public static let liveKey = "android"

    /// The page's row shows only for a server with a device set up.
    public static func shown(_ info: AndroidDeviceInfo?) -> Bool { info?.configured == true }

    /// The screen size as "720 × 1280"; nil when the device gave none.
    public static func screenSize(_ info: AndroidDeviceInfo) -> String? {
        guard let s = info.screen, s.width > 0, s.height > 0 else { return nil }
        return "\(s.width) × \(s.height)"
    }

    /// "Android 15 · 720 × 1280", "Android 15" or the size alone; nil when neither is known.
    @MainActor public static func detail(_ info: AndroidDeviceInfo) -> String? {
        let size = screenSize(info)
        if info.androidVersion.isEmpty { return size }
        guard let s = info.screen, size != nil else { return L(R.string.android_device_version, info.androidVersion) }
        return L(R.string.android_device_detail, info.androidVersion, s.width, s.height)
    }
}

/// 마이 → 휴대폰 기기 (Android my/AndroidDeviceViewModel.kt): the device's state, and the "연결 확인" button's fresh reading
/// (POST /api/android/check). `info` is nil until the server answered, and after a failed read.
@MainActor @Observable public final class AndroidDeviceModel {
    public private(set) var info: AndroidDeviceInfo?
    public private(set) var checking = false

    @ObservationIgnored private let api: KeloApi

    public init(api: KeloApi, info: AndroidDeviceInfo? = nil) {
        self.api = api
        self.info = info
    }

    public func load() async {
        do {
            info = try await api.get("/api/android", as: AndroidDeviceInfo.self)
        } catch is CancellationError {
            return
        } catch {
            info = nil
        }
    }

    public func check() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        do {
            info = try await api.post("/api/android/check", as: AndroidDeviceInfo.self)
        } catch is CancellationError {
            return
        } catch {
            // the server did not answer: the device cannot be said to be connected
            if var i = info { i.connected = false; info = i }
        }
    }
}
