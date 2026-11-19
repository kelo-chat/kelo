import SwiftUI

/// The app's icons (Android ui/icons/KeloIcons.kt), as SF Symbols with the same meaning.
public enum KeloIcon: String, CaseIterable, Sendable {
    case chat = "bubble.left"
    case goal = "flag.checkered"
    case plug = "powerplug"
    case user = "person"
    case trash = "trash"
    case globe = "globe"
    case chevronRight = "chevron.right"
    case chevronLeft = "chevron.left"
    case chevronDown = "chevron.down"
    case plus = "plus"
    case minus = "minus"
    case send = "arrow.up"
    case shield = "shield"
    case phone = "iphone"
    case key = "key"
    case chip = "cpu"
    case file = "doc"
    case mic = "mic"
    case speaker = "speaker.wave.2"
    case spark = "sparkles"
    case check = "checkmark"
    case arrowDown = "arrow.down"
    case clock = "clock"
    case card = "creditcard"
    case lock = "lock"
    case receipt = "doc.plaintext"
    case close = "xmark"
    case copy = "doc.on.doc"
    case keyboard = "keyboard"
    case share = "square.and.arrow.up"
    case retry = "arrow.clockwise"
    case edit = "pencil"
    case flag = "flag"
    case textSelect = "text.cursor"
    case image = "photo"
    case play = "play.fill"
    case video = "video"
    case music = "music.note"
    case camera = "camera"
    case bell = "bell"
    case pulse = "waveform.path.ecg"
    case book = "book"
    case calendar = "calendar"
    case hourglass = "hourglass"
    case alert = "exclamationmark.triangle"
    case arrowLeft = "arrow.left"
    case arrowRight = "arrow.right"
    case eye = "eye"
    case hand = "hand.raised"
    /// The Android device's Home and Recents keys.
    case home = "house"
    case recents = "square"
    case work = "briefcase"
    case moreVert = "ellipsis"
    case info = "info.circle"
    case stop = "stop.fill"
    case cloud = "cloud"
    case contacts = "person.crop.circle"
    case faceID = "faceid"
    case paperclip = "paperclip"
    case keyboardDown = "keyboard.chevron.compact.down"
    case update = "arrow.down.circle"
    case mapPin = "mappin.and.ellipse"
    case package = "shippingbox"
    case qr = "qrcode"
    case sun = "sun.max"
    case location = "location"
    case search = "magnifyingglass"
    case mail = "envelope"
    case scan = "camera.viewfinder"
    case dialpad = "circle.grid.3x3"
    case handset = "phone.fill"
    case handsetDown = "phone.down.fill"
    case micOff = "mic.slash"
}

public extension Image {
    init(kelo icon: KeloIcon) { self.init(systemName: icon.rawValue) }
}

/// An icon at a size, tinted.
public struct KeloIconView: View {
    let icon: KeloIcon
    let size: CGFloat
    let tint: Color?
    @Environment(\.kelo) private var c

    public init(_ icon: KeloIcon, size: CGFloat = 22, tint: Color? = nil) {
        self.icon = icon; self.size = size; self.tint = tint
    }

    public var body: some View {
        Image(kelo: icon)
            .font(.system(size: size * 0.82, weight: .regular))
            .frame(width: size, height: size)
            .foregroundStyle(tint ?? c.ink)
            .accessibilityHidden(true)
    }
}
