import SwiftUI
import KeloCore
import KeloShared

/// A screenshot Kelo showed in the chat (`ChatImage`, Android chat/ChatPictures.kt): its own shape up to 420 pt wide, read with the
/// signed-in API, its caption under it; a tap shows it whole over the chat.
struct ChatPicture: View {
    let image: ChatImage
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var picture: PlatformImage?
    @State private var open = false

    var body: some View {
        let ratio = image.width > 0 && image.height > 0 ? CGFloat(image.width) / CGFloat(image.height) : 16.0 / 10.0
        let described = image.caption.isEmpty ? L(R.string.chat_screenshot) : image.caption
        VStack(alignment: .leading, spacing: 4) {
            Button { if picture != nil { open = true } } label: {
                ZStack {
                    RoundedRectangle(cornerRadius: 14).fill(c.card)
                    if let picture {
                        Image(platformImage: picture).resizable().scaledToFit()
                    }
                }
                .aspectRatio(ratio, contentMode: .fit)
                .frame(maxWidth: 420)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(described)
            .accessibilityAddTraits(.isImage)
            if !image.caption.isEmpty {
                Text(image.caption).font(KeloFont.small).foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 4)
                    .accessibilityHidden(true)   // already the picture's label
            }
        }
        .frame(maxWidth: 420, alignment: .leading)
        .task(id: image.id) {
            guard picture == nil, let api = app.api, let bytes = try? await api.download(image.url) else { return }
            picture = PlatformImage(data: bytes)
        }
        .keloFullScreen(isPresented: $open) {
            ZStack {
                Color.black.opacity(0.92).ignoresSafeArea()
                if let picture {
                    Image(platformImage: picture).resizable().scaledToFit()
                        .accessibilityLabel(described)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { open = false }
            .accessibilityAction(named: Text(L(R.string.close))) { open = false }
        }
    }
}
