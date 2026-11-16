import SwiftUI
import Charts
import KeloCore

// The native renderer of Nest screens (docs/nest-ui.md Part 1; Android shell/NestRenderer.kt): the server sends a plain tree of fixed
// components and this file lays it out with the app's own colours, type and spacing. No web view, no HTML, no Markdown; every text is
// drawn as plain text; pictures come only through the server's `/api/nest/...` paths; an address is opened only in the system browser.

/// The renderer the app root puts in the environment.
public struct NestRenderer: NestSurfaceView {
    public init() {}
    @MainActor public func view(for entry: NestEntry) -> AnyView { AnyView(NestSurface(entry: entry)) }
}

/// One surface in its place: a tab page, a My section, a home card, or the body of the chat's sheet.
struct NestSurface: View {
    let entry: NestEntry
    @Environment(AppModel.self) private var app

    var body: some View {
        if let api = app.api { NestSurfaceBody(entry: entry, api: api) }
    }
}

private struct NestSurfaceBody: View {
    let entry: NestEntry
    @State private var model: NestScreenModel
    @State private var full = false
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c

    init(entry: NestEntry, api: KeloApi) {
        self.entry = entry
        _model = State(initialValue: NestScreenModel(api: api, entry: entry))
    }

    var body: some View {
        place
            .nestScreenLifecycle(model: model, entry: entry)
    }

    @ViewBuilder private var place: some View {
        switch entry.place {
        case NestPlace.tab:
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    NestHeader(entry: entry, title: model.screen.title, onTap: nil)
                    NestScreenBody(model: model)
                }
                .padding(.horizontal, 16).padding(.top, 4).padding(.bottom, 24)
                .keloPageWidth()
            }
            .keloTopScroll(root: true)
            .refreshable { await model.load() }
            .keloShellInsets()
        case NestPlace.homeCard:
            MyCard {
                NestHeader(entry: entry, title: model.screen.title, onTap: { full = true })
                NestScreenBody(model: model)
            }
            .keloFullScreen(isPresented: $full) {
                NestFullPage(entry: entry, onBack: { full = false }).environment(app)
            }
        case NestPlace.mySection:
            VStack(alignment: .leading, spacing: 10) {
                if entry.draft { KeloBadge(L(R.string.nest_draft)) }
                NestScreenBody(model: model)
            }
            .padding(.horizontal, 16).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
        default:   // the chat's sheet
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    NestHeader(entry: entry, title: model.screen.title, onTap: nil)
                    NestScreenBody(model: model)
                }
                .padding(16)
            }
        }
    }
}

/// A surface full screen under a back arrow (a home card's header, "Open" on a draft in My → Nest).
public struct NestFullPage: View {
    let entry: NestEntry
    let onBack: () -> Void
    @Environment(AppModel.self) private var app

    public init(entry: NestEntry, onBack: @escaping () -> Void) { self.entry = entry; self.onBack = onBack }

    public var body: some View {
        if let api = app.api { Inner(entry: entry, api: api, onBack: onBack) }
    }

    private struct Inner: View {
        let entry: NestEntry
        let onBack: () -> Void
        @State private var model: NestScreenModel
        init(entry: NestEntry, api: KeloApi, onBack: @escaping () -> Void) {
            self.entry = entry; self.onBack = onBack
            _model = State(initialValue: NestScreenModel(api: api, entry: entry))
        }
        var body: some View {
            SubPage(model.screen.title.isEmpty ? (entry.title.isEmpty ? entry.packageName : entry.title) : model.screen.title, onBack: onBack) {
                if entry.draft { KeloBadge(L(R.string.nest_draft)) }
                NestScreenBody(model: model)
            }
            .nestScreenLifecycle(model: model, entry: entry)
        }
    }
}

// ---- lifecycle: load, live reload, effects, messages, dialogs

private struct NestLifecycle: ViewModifier {
    let model: NestScreenModel
    let entry: NestEntry
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var started: (message: String, turnId: String)?
    @State private var note: String?

    func body(content: Content) -> some View {
        content
            .task(id: entry.key) { await model.load() }
            // the screen reloads at once when Kelo changes it (the live event nest_changed {"id"})
            .task { for await id in NestLive.ids() { await model.changed(id: id) } }
            .onChange(of: model.effects) { _, _ in drain() }
            .onChange(of: model.message) { _, m in if let m { note = m.isEmpty ? nil : m; model.message = nil } }
            .onChange(of: model.gone) { _, gone in if gone { Task { await app.nest?.reload() } } }
            .overlay(alignment: .bottom) {
                if let note {
                    Text(note).font(KeloFont.small).foregroundStyle(.white).multilineTextAlignment(.center)
                        .padding(.horizontal, 14).padding(.vertical, 10)
                        .background(Capsule().fill(Color.black.opacity(0.82)))
                        .padding(.bottom, 24).padding(.horizontal, 16)
                        .transition(.opacity)
                        .task(id: note) { try? await Task.sleep(nanoseconds: 3_000_000_000); self.note = nil }
                        .accessibilityAddTraits(.updatesFrequently)
                }
            }
            .alert(model.confirm?.title.isEmpty == false ? model.confirm!.title : L(R.string.nest_confirm),
                   isPresented: Binding(get: { model.confirm != nil }, set: { if !$0 { model.dismissConfirm() } })) {
                Button(L(R.string.nest_confirm)) { Task { await model.confirmPending() } }
                Button(L(R.string.cancel), role: .cancel) { model.dismissConfirm() }
            } message: {
                Text(confirmText)
            }
            .keloConfirm(started?.message.isEmpty == false ? started!.message : L(R.string.nest_started_chat),
                         isPresented: Binding(get: { started != nil }, set: { if !$0 { started = nil } }),
                         yes: L(R.string.nest_open_chat), danger: false) {
                if let s = started {
                    app.router.chatFollowTurn = s.turnId
                    app.router.workOpen = false
                    app.router.go(.chat)
                }
            }
    }

    private var confirmText: String {
        guard let c = model.confirm else { return "" }
        var parts: [String] = []
        if !c.detail.isEmpty { parts.append(c.detail) }
        if !c.sends.isEmpty { parts.append(L(R.string.nest_sends) + "\n" + c.sends.map { "\($0.key): \($0.value)" }.joined(separator: "\n")) }
        return parts.joined(separator: "\n\n")
    }

    @MainActor private func drain() {
        while let fx = model.nextEffect() {
            switch fx {
            case .openUrl(let u): if nestOpenable(u), let url = URL(string: u) { openURL(url) }   // the system browser, never a web view
            case .started(let m, let t): started = (m, t)
            }
        }
    }
}

extension View {
    fileprivate func nestScreenLifecycle(model: NestScreenModel, entry: NestEntry) -> some View { modifier(NestLifecycle(model: model, entry: entry)) }
}

// ---- the header

/// The title of a surface with the small "Draft" badge of a draft, and under it whose screen it is (the package's name and author).
struct NestHeader: View {
    let entry: NestEntry
    let title: String
    let onTap: (() -> Void)?
    @Environment(\.kelo) private var c

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                Text(title.isEmpty ? (entry.title.isEmpty ? entry.packageName : entry.title) : title)
                    .font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                if entry.draft { KeloBadge(L(R.string.nest_draft)) }
            }
            let from = entry.author.isEmpty ? entry.packageName : "\(entry.packageName) · \(entry.author)"
            if !from.isEmpty { MutedText(L(R.string.nest_from, from), small: true) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        if let onTap { Button(action: onTap) { content.contentShape(Rectangle()) }.buttonStyle(.plain) } else { content }
    }
}

// ---- the body

/// The screen's body: the quiet notice of data that could not be fetched, then the components top to bottom.
public struct NestScreenBody: View {
    let model: NestScreenModel
    @Environment(\.kelo) private var c

    public init(model: NestScreenModel) { self.model = model }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !model.loaded {
                SkeletonRows(rows: 3)
            } else if model.gone {
                MutedText(L(R.string.nest_gone))
            } else {
                if let e = model.error, model.screen.components.isEmpty { MutedText(e.isEmpty ? L(R.string.nest_load_failed) : e) }
                ForEach(Array(model.screen.errors.enumerated()), id: \.offset) { _, e in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "info.circle").font(.system(size: 13)).foregroundStyle(c.muted).padding(.top, 2).accessibilityHidden(true)
                        Text(e).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    }
                }
                NestComponentsView(components: model.screen.components, busy: model.busy,
                                   run: { a, f in Task { await model.run(a, form: f) } }, image: { path in await model.image(path) })
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A column of components.
public struct NestComponentsView: View {
    let components: [NestComponent]
    let busy: Set<String>
    let run: (NestAction, [String: JSON]?) -> Void
    let image: (String) async -> Data?

    public init(components: [NestComponent], busy: Set<String>, run: @escaping (NestAction, [String: JSON]?) -> Void,
                image: @escaping (String) async -> Data?) {
        self.components = components; self.busy = busy; self.run = run; self.image = image
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(components.enumerated()), id: \.offset) { _, comp in
                NestPart(component: comp, busy: busy, run: run, image: image)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct NestPart: View {
    let component: NestComponent
    let busy: Set<String>
    let run: (NestAction, [String: JSON]?) -> Void
    let image: (String) async -> Data?
    @Environment(\.kelo) private var c

    var body: some View {
        switch component {
        case .header(let text, let subtitle):
            if !text.isEmpty || !subtitle.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    if !text.isEmpty { Text(text).font(KeloFont.titleMedium).foregroundStyle(c.ink).accessibilityAddTraits(.isHeader) }
                    if !subtitle.isEmpty { MutedText(subtitle, small: true) }
                }
                .padding(.top, 6)
            }
        case .text(let text, let style):
            if !text.isEmpty {
                switch style {
                case "title": Text(text).font(KeloFont.titleLarge).foregroundStyle(c.ink)
                case "caption": Text(text).font(KeloFont.caption).foregroundStyle(c.muted)
                case "label": Text(text).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                default: Text(text).font(KeloFont.body).foregroundStyle(c.ink)
                }
            }
        case .stat(let label, let value, let unit, let caption, let icon):
            MyCard {
                HStack(spacing: 12) {
                    if !icon.isEmpty { NestIconView(icon, size: 28, tint: c.accent) }
                    VStack(alignment: .leading, spacing: 2) {
                        if !label.isEmpty { Text(label).font(KeloFont.small).foregroundStyle(c.muted) }
                        HStack(alignment: .lastTextBaseline, spacing: 4) {
                            Text(value).font(KeloFont.headline).foregroundStyle(c.ink)
                            if !unit.isEmpty { Text(unit).font(KeloFont.body).foregroundStyle(c.muted) }
                        }
                        if !caption.isEmpty { Text(caption).font(KeloFont.small).foregroundStyle(c.muted) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .accessibilityElement(children: .combine)
        case .list(let rows, let emptyText, let emptyIcon):
            if rows.isEmpty {
                if let emptyText { NestEmptyBlock(text: emptyText, icon: emptyIcon, action: nil, busy: busy, run: run) }
            } else {
                NestListView(rows: rows, busy: busy, run: run, image: image)
            }
        case .card(let title, let subtitle, let children, let action):
            let inner = VStack(alignment: .leading, spacing: 10) {
                if !title.isEmpty { Text(title).font(KeloFont.titleMedium).foregroundStyle(c.ink) }
                if !subtitle.isEmpty { MutedText(subtitle, small: true) }
                NestComponentsView(components: children, busy: busy, run: run, image: image)
            }
            if let action, !busy.contains(action.key) {
                MyCard(action: { run(action, nil) }) { inner }
            } else {
                MyCard { inner }
            }
        case .chart(let kind, let label, let points):
            if !points.isEmpty { NestChartView(kind: kind, label: label, points: points) }
        case .image(let src, let alt, let height):
            NestPicture(src: src, alt: alt, image: image).frame(maxWidth: .infinity, maxHeight: CGFloat(height))
        case .button(let label, let style, let action):
            NestButton(label: label, style: style, action: action, busy: busy.contains(action.key), run: run)
        case .form(let fields, let submitLabel, let submit):
            NestFormView(fields: fields, submitLabel: submitLabel, submit: submit, busy: busy.contains(submit.key), run: run)
        case .empty(let text, let icon, let action):
            if !text.isEmpty || !icon.isEmpty { NestEmptyBlock(text: text, icon: icon, action: action, busy: busy, run: run) }
        case .unsupported(let text):
            HStack(spacing: 8) {
                Image(systemName: "info.circle").font(.system(size: 15)).foregroundStyle(c.muted).accessibilityHidden(true)
                Text(text.isEmpty ? L(R.string.nest_update_part) : text).font(KeloFont.small).foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
        }
    }
}

private struct NestListView: View {
    let rows: [NestRow]
    let busy: Set<String>
    let run: (NestAction, [String: JSON]?) -> Void
    let image: (String) async -> Data?
    @State private var removing: NestAction?
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { i, row in
                if i > 0 { Hairline() }
                rowView(row)
            }
        }
        .keloConfirm(L(R.string.nest_remove_q), isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                     yes: removing.map { $0.label.isEmpty ? L(R.string.nest_remove) : $0.label } ?? L(R.string.nest_remove)) {
            if let a = removing { run(a, nil) }
        }
    }

    @ViewBuilder private func rowView(_ r: NestRow) -> some View {
        let a = r.action
        let tappable = a != nil && !(a!.removes)
        let content = HStack(spacing: 12) {
            // an image wins over an icon
            if !r.image.isEmpty {
                NestPicture(src: r.image, alt: r.title, image: image).frame(width: 40, height: 40).clipShape(RoundedRectangle(cornerRadius: 8))
            } else if !r.icon.isEmpty {
                NestIconView(r.icon, size: 24, tint: c.muted)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(r.title).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
                if !r.subtitle.isEmpty { Text(r.subtitle).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !r.trailing.isEmpty {
                Text(r.trailing).font(KeloFont.small).foregroundStyle(c.muted).multilineTextAlignment(.trailing).frame(maxWidth: 120, alignment: .trailing)
            }
            if let a, a.removes {
                if busy.contains(a.key) { ProgressView().controlSize(.small) }
                else { LinkButton(a.label.isEmpty ? L(R.string.nest_remove) : a.label, danger: true) { removing = a } }
            }
        }
        .padding(.vertical, 8).frame(minHeight: 56)
        .contentShape(Rectangle())
        if tappable, let a {
            Button { if !busy.contains(a.key) { run(a, nil) } } label: { content }.buttonStyle(.plain)
        } else {
            content
        }
    }
}

private struct NestChartView: View {
    let kind: String
    let label: String
    let points: [NestPoint]
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !label.isEmpty { Text(label).font(KeloFont.small).foregroundStyle(c.muted) }
            Chart(Array(points.enumerated()), id: \.offset) { i, p in
                if kind == "bar" {
                    BarMark(x: .value("x", "\(i)"), y: .value("y", p.y)).foregroundStyle(c.accent)
                } else {
                    LineMark(x: .value("x", "\(i)"), y: .value("y", p.y)).foregroundStyle(c.accent)
                    PointMark(x: .value("x", "\(i)"), y: .value("y", p.y)).foregroundStyle(c.accent)
                }
            }
            .chartXAxis {
                // the category labels in order; a long series shows its first, middle and last
                AxisMarks(values: shownIndexes.map { "\($0)" }) { v in
                    AxisValueLabel { if let s = v.as(String.self), let i = Int(s) { Text(points[i].x).font(KeloFont.label).foregroundStyle(c.muted) } }
                }
            }
            .frame(height: 120)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(points.map { "\($0.x) \($0.y)" }.joined(separator: ", "))
        }
    }

    private var shownIndexes: [Int] {
        points.count <= 7 ? Array(points.indices) : [0, points.count / 2, points.count - 1]
    }
}

/// A picture of a screen: fetched through the server (`/api/nest/...` only, with the auth header) and fitted inside its box.
private struct NestPicture: View {
    let src: String
    let alt: String
    let image: (String) async -> Data?
    @State private var data: Data?
    @Environment(\.kelo) private var c

    var body: some View {
        Group {
            if let data, let img = nestImage(data) {
                img.resizable().scaledToFit()
            } else {
                Rectangle().fill(c.bubble)
            }
        }
        .accessibilityLabel(alt)
        .task(id: src) { data = src.hasPrefix("/api/nest/") ? await image(src) : nil }
    }
}

private func nestImage(_ data: Data) -> Image? {
    #if canImport(UIKit)
    return UIImage(data: data).map { Image(uiImage: $0) }
    #elseif canImport(AppKit)
    return NSImage(data: data).map { Image(nsImage: $0) }
    #else
    return nil
    #endif
}

private struct NestButton: View {
    let label: String
    let style: String
    let action: NestAction
    let busy: Bool
    let run: (NestAction, [String: JSON]?) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        let text = !label.isEmpty ? label : (!action.label.isEmpty ? action.label : L(R.string.ok))
        // hidden while its request runs, so it cannot be sent twice
        if busy {
            ProgressView().frame(maxWidth: .infinity, minHeight: 44)
        } else {
            switch style {
            case "secondary": SecondaryButton(text, fill: true) { run(action, nil) }
            case "danger":
                Button { run(action, nil) } label: {
                    Text(text).font(KeloFont.button).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: 24).padding(.horizontal, 20).padding(.vertical, 12)
                        .foregroundStyle(c.onDanger).background(Capsule().fill(c.danger))
                }
                .buttonStyle(PressShrinkStyle())
            default: PrimaryButton(text) { run(action, nil) }
            }
        }
    }
}

private struct NestEmptyBlock: View {
    let text: String
    let icon: String
    let action: NestAction?
    let busy: Set<String>
    let run: (NestAction, [String: JSON]?) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(spacing: 8) {
            if !icon.isEmpty { NestIconView(icon, size: 32, tint: c.muted) }
            if !text.isEmpty { Text(text).font(KeloFont.body).foregroundStyle(c.muted).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true) }
            if let action { NestButton(label: action.label, style: "secondary", action: action, busy: busy.contains(action.key), run: run) }
        }
        .frame(maxWidth: .infinity).padding(.vertical, 16)
    }
}

// ---- forms

private struct NestFormView: View {
    let fields: [NestField]
    let submitLabel: String
    let submit: NestAction
    let busy: Bool
    let run: (NestAction, [String: JSON]?) -> Void
    @State private var values: [String: String]
    @State private var missing: Set<String> = []
    @Environment(\.kelo) private var c

    init(fields: [NestField], submitLabel: String, submit: NestAction, busy: Bool, run: @escaping (NestAction, [String: JSON]?) -> Void) {
        self.fields = fields; self.submitLabel = submitLabel; self.submit = submit; self.busy = busy; self.run = run
        _values = State(initialValue: NestFormRules.initial(fields))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(fields, id: \.id) { f in
                NestFieldView(field: f, value: Binding(get: { values[f.id] ?? "" }, set: { values[f.id] = $0; missing.remove(f.id) }), error: missing.contains(f.id))
            }
            if busy {
                ProgressView().frame(maxWidth: .infinity, minHeight: 44)
            } else {
                PrimaryButton(!submitLabel.isEmpty ? submitLabel : (!submit.label.isEmpty ? submit.label : L(R.string.ok))) {
                    let bad = NestFormRules.missing(fields, values)
                    if !bad.isEmpty { missing = Set(bad); return }
                    run(submit, NestFormRules.payload(fields, values))
                    if submit.type == "store_add" { values = NestFormRules.initial(fields) }   // ready for the next entry
                }
            }
        }
    }
}

private struct NestFieldView: View {
    let field: NestField
    @Binding var value: String
    let error: Bool
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if field.type == "toggle" {
                ToggleRow(field.label, isOn: Binding(get: { value == "true" }, set: { value = $0 ? "true" : "false" }))
            } else {
                if !field.label.isEmpty { Text(field.label).font(KeloFont.titleSmall).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true) }
                input
                if error { Text(L(R.string.nest_required)).font(KeloFont.small).foregroundStyle(c.danger) }
            }
        }
    }

    @ViewBuilder private var input: some View {
        switch field.type {
        case "number": KeloTextField(field.placeholder, text: $value, keyboard: .decimal)
        // a multi-line field is message-like (a note, a comment): it may wrap
        case "multiline": KeloTextField(field.placeholder, text: $value, axis: .vertical, maxLength: field.maxLength ?? NestLimits.maxText)
        case "date":
            DatePicker("", selection: Binding(get: { NestDates.date(value) ?? Date() }, set: { value = NestDates.dayString($0) }), displayedComponents: .date)
                .labelsHidden().tint(c.accent).frame(maxWidth: .infinity, alignment: .leading)
                .overlay(alignment: .leading) { if NestDates.date(value) == nil { pickHint(L(R.string.nest_pick_date)) } }
        case "time":
            DatePicker("", selection: Binding(get: { NestDates.time(value) ?? Date() }, set: { value = NestDates.timeString($0) }), displayedComponents: .hourAndMinute)
                .labelsHidden().tint(c.accent).frame(maxWidth: .infinity, alignment: .leading)
                .overlay(alignment: .leading) { if NestDates.time(value) == nil { pickHint(L(R.string.nest_pick_time)) } }
        case "select":
            Menu {
                ForEach(field.options, id: \.value) { o in Button(o.label) { value = o.value } }
            } label: {
                let chosen = field.options.first { $0.value == value }
                ChoiceChipLabel(title: chosen?.label ?? (field.placeholder.isEmpty ? L(R.string.nest_pick_option) : field.placeholder), selected: chosen != nil)
            }
        default: KeloTextField(field.placeholder, text: $value, maxLength: field.maxLength ?? NestLimits.maxText)
        }
    }

    /// Until a date or time is picked the picker shows today's: this line says nothing is chosen yet.
    private func pickHint(_ text: String) -> some View {
        Text(text).font(KeloFont.body).foregroundStyle(c.muted).padding(.horizontal, 10).background(c.bg).allowsHitTesting(false)
    }
}

private struct ChoiceChipLabel: View {
    let title: String
    let selected: Bool
    @Environment(\.kelo) private var c
    var body: some View {
        HStack(spacing: 4) {
            if selected { KeloIconView(.check, size: 15, tint: c.ink) }
            Text(title).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
        }
        .padding(.leading, selected ? 8 : 12).padding(.trailing, 12).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(selected ? c.mine : c.card))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(selected ? .clear : c.line, lineWidth: 1))
    }
}

/// Dates and times of form fields: "YYYY-MM-DD" and "HH:MM", whatever the phone's language.
public enum NestDates {
    private static func formatter(_ f: String) -> DateFormatter {
        let d = DateFormatter()
        d.locale = Locale(identifier: "en_US_POSIX"); d.dateFormat = f
        return d
    }
    public static func date(_ s: String) -> Date? { formatter("yyyy-MM-dd").date(from: s) }
    public static func dayString(_ d: Date) -> String { formatter("yyyy-MM-dd").string(from: d) }
    public static func time(_ s: String) -> Date? { formatter("HH:mm").date(from: s) }
    public static func timeString(_ d: Date) -> String { formatter("HH:mm").string(from: d) }
}

extension NestScreenModel {
    /// The bytes of a screen's picture (`/api/nest/...` with the auth header), or nil when they cannot be had.
    func image(_ path: String) async -> Data? { try? await api.download(path) }
}
