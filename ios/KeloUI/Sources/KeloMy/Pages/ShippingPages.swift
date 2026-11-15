import SwiftUI
import KeloCore
import KeloShared

// Shipping addresses ("배송 정보") in the wallet, next to the cards (SHIPPING API in server.py): the list with the default one first,
// adding and editing one with its country's own form (from the server), and deleting one. Nicknames like the cards' ("집", "회사").

/// The wallet list's shipping section: each address with its nickname, a Default tag, the address on up to two lines and the name;
/// an empty note; "Add an address".
struct ShippingSection: View {
    let profiles: [ShippingProfile]
    let go: (WalletView) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        SectionLabel(L(R.string.shipping_section)).padding(.horizontal, -16)
            .accessibilityIdentifier("kelo.shipping.section")
        if profiles.isEmpty { EmptyNote(L(R.string.shipping_empty)) }
        VStack(spacing: 0) {
            ForEach(profiles, id: \.id) { p in row(p) }
        }
        MenuGroup {
            MenuRow(.plus, L(R.string.shipping_add)) { go(.address(nil)) }
                .accessibilityIdentifier("kelo.shipping.add")
        }
    }

    private func row(_ p: ShippingProfile) -> some View {
        Button { go(.address(p.id)) } label: {
            HStack(alignment: .top, spacing: 16) {
                KeloIconView(.mapPin, size: 24, tint: c.muted).padding(.top, 2)
                VStack(alignment: .leading, spacing: 3) {
                    WalletWrap {
                        Text(p.label.isEmpty ? L(R.string.shipping_home) : p.label).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        if p.isDefault { WalletTag(text: L(R.string.wallet_tag_default), tone: .accent) }
                    }
                    let one = p.oneLine.isEmpty ? AddressRules.oneLine(p.formatted) : p.oneLine
                    if !one.isEmpty {
                        Text(one).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2).truncationMode(.tail)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    let who = [p.name, p.phone].filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }.joined(separator: " · ")
                    if !who.isEmpty {
                        Text(who).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("kelo.shipping.row.\(p.id)")
    }
}

/// Adding or editing a shipping address: the nickname first, the country, the country's own rows in order (from the server), the
/// phone with its calling code, the delivery note, and "Use as the default address". Required rows and the postal code are checked
/// here before sending; the server checks again and its words show under the form. Deleting asks first.
struct ShippingEditPage: View {
    /// nil: a new address.
    let profile: ShippingProfile?
    /// How many other addresses there are (the first one becomes the default by itself).
    let others: Int
    let onBack: () -> Void
    let onSaved: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var editor: AddressEditor
    @State private var label: String
    @State private var makeDefault: Bool
    @State private var saving = false
    @State private var deleting = false
    @State private var msg = ""

    init(profile: ShippingProfile?, others: Int, onBack: @escaping () -> Void, onSaved: @escaping () -> Void) {
        self.profile = profile
        self.others = others
        self.onBack = onBack
        self.onSaved = onSaved
        _editor = State(initialValue: AddressEditor(draft: profile.map(AddressDraft.of) ?? AddressDraft(country: ""), savedCountry: profile?.country))
        _label = State(initialValue: profile?.label ?? "")
        _makeDefault = State(initialValue: profile?.isDefault ?? (others == 0))
    }

    /// The default can only move to another address (the server never takes "default": false), and the first one is the default.
    private var defaultFixed: Bool { profile?.isDefault == true || (profile == nil && others == 0) }

    var body: some View {
        SubPage(L(profile == nil ? R.string.shipping_add : R.string.shipping_edit_title), onBack: onBack) {
            MyCard {
                AddressRow(editor.form?.labels.nickname.nonEmptyText ?? L(R.string.shipping_nickname)) {
                    KeloTextField(L(R.string.shipping_nickname_hint), text: $label, maxLength: AddressRules.maxLabel)
                        .accessibilityIdentifier("kelo.shipping.nickname")
                }
                AddressFields(editor: editor, api: app.api, onCard: true)
            }
            MyCard {
                ToggleRow(L(R.string.shipping_default_title), detail: L(R.string.shipping_default_note),
                          isOn: $makeDefault, enabled: !defaultFixed)
                    .accessibilityIdentifier("kelo.shipping.default")
            }
            if !msg.isEmpty { ErrorText(msg, small: true).accessibilityIdentifier("kelo.shipping.error") }
            PrimaryButton(L(R.string.save), enabled: editor.form != nil && !saving, busy: saving) { save() }
                .accessibilityIdentifier("kelo.shipping.saveProfile")
            if profile != nil {
                MenuGroup {
                    MenuRow(.trash, L(R.string.shipping_delete), danger: true) { deleting = true }
                        .accessibilityIdentifier("kelo.shipping.delete")
                }
            }
        }
        .task { await editor.start(app.api) }
        .onAppear { if label.trimmingCharacters(in: .whitespaces).isEmpty && profile == nil { label = L(R.string.shipping_home) } }
        .alert(L(R.string.shipping_delete_confirm, (profile?.label).flatMap { $0.isEmpty ? nil : $0 } ?? L(R.string.shipping_home)),
               isPresented: $deleting) {
            Button(L(R.string.delete), role: .destructive) { delete() }
            Button(L(R.string.cancel), role: .cancel) {}
        }
    }

    private func save() {
        msg = ""
        guard editor.check(), var body = editor.body() else { Haptics.warning(); return }
        body["label"] = .string(AddressRules.startLabel(label, fallback: L(R.string.shipping_home)))
        if makeDefault && profile?.isDefault != true { body["default"] = .bool(true) }
        saving = true
        Task {
            defer { saving = false }
            do {
                let api = try app.requireApi()
                if let profile { _ = try await api.updateShippingProfile(profile.id, .object(body)) }
                else { _ = try await api.addShippingProfile(.object(body)) }
                Haptics.success()
                onSaved()
            } catch {
                msg = app.message(for: error)
                Haptics.warning()
            }
        }
    }

    private func delete() {
        guard let profile else { return }
        msg = ""
        Task {
            do {
                try await app.requireApi().deleteShippingProfile(profile.id)
                Haptics.success()
                onSaved()
            } catch {
                msg = app.message(for: error)
                Haptics.warning()
            }
        }
    }
}

private extension String {
    var nonEmptyText: String? { trimmingCharacters(in: .whitespaces).isEmpty ? nil : self }
}
