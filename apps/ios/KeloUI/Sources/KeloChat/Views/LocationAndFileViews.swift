import SwiftUI
import KeloCore
import KeloShared

/// The reply waits for this phone's place (LOCATION REQUEST; Android chat/LocationAsk.kt): why (the agent's words), "Share my
/// location" and "Not now". When the owner refused before (or iOS will not ask again) the button opens Kelo's page in Settings, and
/// coming back with location allowed sends it. The place goes only to the owner's own server (`PhonePlace.read`: position,
/// accuracy, town, country, neighbourhood).
struct LocationAskCard: View {
    let item: ChatItem.LocationAsk
    let model: ChatModel
    @Environment(\.kelo) private var c
    @Environment(\.scenePhase) private var scenePhase
    @State private var settingsOpened = false
    @State private var checked = 0
    @State private var unavailable = false

    var body: some View {
        CardBox {
            if let end = LocationAskRules.endLine(item.state) {
                ResultLine(icon: item.state == ChatItem.LocationAsk.allowed ? .check : .close, text: L(end),
                           muted: item.state != ChatItem.LocationAsk.allowed)
                if item.state == ChatItem.LocationAsk.refused && unavailable {
                    Text(L(R.string.location_ask_unavailable)).font(KeloFont.small).foregroundStyle(c.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                HStack(alignment: .top, spacing: 8) {
                    KeloIconView(.mapPin, size: 22, tint: c.accent)
                    Text(L(R.string.location_ask_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityAddTraits(.isHeader)
                }
                Text(item.reason.isEmpty ? L(R.string.location_ask_body) : item.reason).font(KeloFont.body).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(L(R.string.location_ask_private)).font(KeloFont.small).foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
                if item.state == ChatItem.LocationAsk.sending {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(L(R.string.location_ask_reading)).font(KeloFont.small).foregroundStyle(c.muted)
                    }
                } else {
                    let _ = checked
                    let step = LocationAskRules.onShare(granted: PhonePlace.granted, settings: item.settings, blocked: PhonePlace.refused, servicesOn: PhonePlace.servicesOn)
                    if step == .servicesOff {
                        Text(L(R.string.ios_location_services_off)).font(KeloFont.small).foregroundStyle(c.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        if step == .openSettings {
                            Text(L(R.string.location_ask_settings_hint)).font(KeloFont.small).foregroundStyle(c.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        // one under the other, so long words in any language fit at 320 points
                        PrimaryButton(L(step == .openSettings ? R.string.location_ask_settings : R.string.location_ask_share)) {
                            Haptics.tick()
                            switch step {
                            case .send: send()
                            case .askSystem:
                                Task {
                                    if await PhonePlace.ask() { send() } else { model.answerLocation(item.approvalId, place: nil) }
                                }
                            case .openSettings:
                                settingsOpened = true
                                DeviceInfo.openAppSettings()
                            case .servicesOff:
                                break
                            }
                        }
                    }
                    SecondaryButton(L(R.string.location_ask_not_now), fill: true) { model.answerLocation(item.approvalId, place: nil) }
                }
            }
        }
        .accessibilityIdentifier("kelo.locationAsk")
        // back from Settings: allowed there -> send at once
        .onChange(of: scenePhase) { _, p in
            if p == .active { checked += 1 }
            guard p == .active, settingsOpened, item.state == ChatItem.LocationAsk.waiting else { return }
            settingsOpened = false
            if PhonePlace.granted { send() }
        }
    }

    private func send() {
        model.locationReading(item.approvalId)
        Task {
            let place = await PhonePlace.read()
            unavailable = place == nil
            model.answerLocation(item.approvalId, place: place)
        }
    }
}

/// The files the user sent with a message, at the end side above (or instead of) the text (Android chat/MessageFiles.kt): pictures
/// and videos as a small grid of thumbnails (GET thumb with the signed-in API; a tap shows the picture whole), other files as
/// one-line chips with their names.
struct UserFiles: View {
    let files: [MessageFile]

    var body: some View {
        let pictures = files.filter(\.visual)
        let others = files.filter { !$0.visual }
        let rows = MessageFileRules.rows(pictures.count)
        VStack(alignment: .trailing, spacing: 4) {
            ForEach(Array(rows.enumerated()), id: \.offset) { r, n in
                let start = rows.prefix(r).reduce(0, +)
                HStack(spacing: 4) {
                    ForEach(pictures[start..<(start + n)], id: \.path) { f in
                        FileThumb(file: f, side: pictures.count == 1 ? MessageFileRules.single : MessageFileRules.grid)
                    }
                }
            }
            ForEach(others, id: \.path) { f in FileChip(file: f) }
        }
    }
}

private struct FileThumb: View {
    let file: MessageFile
    let side: CGFloat
    @Environment(\.kelo) private var c
    @State private var thumb = RemotePictureModel()
    @State private var big = RemotePictureModel()
    @Environment(\.keloApi) private var api
    @State private var open = false

    var body: some View {
        Button { if file.kind == "image" { open = true } } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 14).fill(c.card)
                if let picture = thumb.picture {
                    Image(platformImage: picture).resizable().scaledToFill()
                } else {
                    KeloIconView(file.kind == "video" ? .video : .image, size: 22, tint: c.muted)
                }
                if file.kind == "video", thumb.picture != nil {
                    KeloIconView(.play, size: 22, tint: .white).shadow(radius: 2)
                }
            }
            .frame(width: side, height: side)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(file.name)
        .accessibilityAddTraits(.isImage)
        // the server makes the thumbnail once the message is stored (the user_message event brings it)
        .task(id: file.thumb ?? "") {
            await thumb.load(file.thumb ?? "", api: api)
        }
        .keloFullScreen(isPresented: $open) {
            ZStack {
                Color.black.opacity(0.92).ignoresSafeArea()
                if let shown = big.picture ?? thumb.picture { Image(platformImage: shown).resizable().scaledToFit() }
            }
            .contentShape(Rectangle())
            .onTapGesture { open = false }
            .accessibilityAddTraits(.isButton)
            .accessibilityLabel(file.name)
            .task {
                await big.load(file.url, api: api)
            }
        }
    }
}

private struct FileChip: View {
    let file: MessageFile
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(spacing: 8) {
            KeloIconView(.file, size: 18, tint: c.ink)
            // one line (a file name is one value): a long one cut in the middle, its extension kept
            Text(MessageFileRules.chipName(file.name)).font(KeloFont.body).foregroundStyle(c.ink).lineLimit(1).truncationMode(.middle)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: 260, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.mine))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(L(R.string.chat_attached_file)): \(file.name)")
    }
}
