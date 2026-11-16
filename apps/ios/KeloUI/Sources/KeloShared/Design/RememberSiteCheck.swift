import SwiftUI

/// "Don't ask again on this site" under an approval's question (OFF by default) and, under it, what a rule really covers:
/// the site and all its sub-addresses.
public struct RememberSiteCheck: View {
    public let site: String
    public let checked: Bool
    public var enabled: Bool = true
    public let onChange: (Bool) -> Void
    @Environment(\.kelo) private var c

    public init(site: String, checked: Bool, enabled: Bool = true, onChange: @escaping (Bool) -> Void) {
        self.site = site
        self.checked = checked
        self.enabled = enabled
        self.onChange = onChange
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Button {
                if enabled {
                    Haptics.tick()
                    onChange(!checked)
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: checked ? "checkmark.square.fill" : "square")
                        .font(.title3)
                        .foregroundStyle(checked ? c.accent : c.muted)
                    Text(L(R.string.approval_remember_site))
                        .font(KeloFont.body)
                        .foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!enabled)
            .accessibilityAddTraits(.isButton)
            .accessibilityValue(checked ? "on" : "off")

            Text(L(R.string.approval_remember_scope, site))
                .font(KeloFont.small)
                .foregroundStyle(c.muted)
                .padding(.leading, 34)
        }
    }
}
