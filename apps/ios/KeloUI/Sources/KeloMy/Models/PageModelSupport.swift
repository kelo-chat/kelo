import SwiftUI
import KeloShared

// What the My page models share. A page model is `@MainActor @Observable final class <Page>Model`: the page's state as
// `private(set) var`s and plain methods for what the person does. It gets its dependencies in `init` (the `KeloApi`, small
// protocols), never reaches into the AppModel or a `.shared` singleton, and the view builds it once:
//
//     @State private var model: ProfileModel
//     init(api: KeloApi, host: ...) { _model = State(initialValue: ProfileModel(api: api, host: host)) }
//
// The view reads the model and calls its methods; it never calls `api.*` or starts a Task that talks to the server (`.task { await
// model.load() }` is fine, and it is where anything that polls runs, so it stops when the page goes away).

extension View {
    /// A model's `notice` as the floating message; it asks the model to forget it when it has been shown.
    func toast(_ message: String?, clear: @escaping () -> Void) -> some View {
        keloToast(Binding(get: { message }, set: { if $0 == nil { clear() } }))
    }
}
