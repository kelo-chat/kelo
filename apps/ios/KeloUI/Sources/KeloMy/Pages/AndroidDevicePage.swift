import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 휴대폰 기기 (Android my/AndroidDevicePage.kt): whether the device Kelo uses is connected (model, Android version, screen
/// size, Play Store, Korean input), a connection check and its live screen. The address is not edited here: it is in the server's .env.
struct AndroidDevicePage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var model: AndroidDeviceModel

    init(api: KeloApi) {
        _model = State(initialValue: AndroidDeviceModel(api: api))
    }

    var body: some View {
        SubPage(L(R.string.android_device_title), onBack: { dismiss() }) {
            if let info = model.info {
                AndroidDeviceContent(
                    info: info, checking: model.checking,
                    onCheck: { Task { await model.check() } },
                    onOpenScreen: { app.router.openLive(AndroidDeviceRules.liveKey) },
                    onOpenReadme: { if let u = URL(string: AndroidDeviceRules.readmeUrl) { openURL(u) } })
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
    }
}

/// The status card, the .env note and the README link.
struct AndroidDeviceContent: View {
    let info: AndroidDeviceInfo
    let checking: Bool
    let onCheck: () -> Void
    let onOpenScreen: () -> Void
    let onOpenReadme: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        KeloCard {
            HStack(spacing: 8) {
                Circle().fill(info.connected ? c.success : c.muted).frame(width: 10, height: 10)
                Text(L(info.connected ? R.string.android_device_connected : R.string.android_device_not_connected))
                    .font(KeloFont.titleMedium).foregroundStyle(c.ink)
            }
            .accessibilityElement(children: .combine)
            if info.connected {
                if !info.model.isEmpty { Text(info.model).font(KeloFont.bodyLarge).foregroundStyle(c.ink) }
                if let detail = AndroidDeviceRules.detail(info) { MutedText(detail, small: true) }
                Divider().overlay(c.line)
                CheckLine(label: L(R.string.android_device_play_store), present: info.playStore)
                CheckLine(label: L(R.string.android_device_korean_input), present: info.koreanInput)
            }
            Divider().overlay(c.line)
            HStack(spacing: 12) {
                SecondaryButton(L(R.string.android_device_check), enabled: !checking, fill: true, action: onCheck)
                    .accessibilityIdentifier("kelo.androidDevice.check")
                PrimaryButton(L(R.string.android_device_view), enabled: info.connected, action: onOpenScreen)
                    .accessibilityIdentifier("kelo.androidDevice.view")
            }
        }
        MutedText(L(R.string.android_device_note), small: true).padding(.top, 4)
        LinkButton(L(R.string.android_device_readme), action: onOpenReadme)
    }
}

private struct CheckLine: View {
    let label: String
    let present: Bool
    @Environment(\.kelo) private var c

    var body: some View {
        HStack {
            Text(label).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
            Spacer(minLength: 8)
            Text(present ? "✓" : L(R.string.android_device_missing))
                .font(KeloFont.bodyLarge).foregroundStyle(present ? c.success : c.muted)
        }
        .accessibilityElement(children: .combine)
    }
}
