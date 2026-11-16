import Foundation

// What came from one connector and is still kept (server connector_data.py), and the question the apps ask after a disconnect: "also
// forget the files and items that came from it?". Nothing is deleted without POST /api/connectors/{id}/forget.

/// The counts of what came from one connector: `files` (library files, to the trash), `notes`, `items` (proactive log rows).
public struct ConnectorDataCounts: Equatable, Sendable {
    public var files: Int
    public var notes: Int
    public var items: Int

    public init(files: Int = 0, notes: Int = 0, items: Int = 0) {
        self.files = files; self.notes = notes; self.items = items
    }

    init(_ o: Lenient) {
        files = o.int("files"); notes = o.int("notes"); items = o.int("items")
    }

    /// Something is left to ask about (any count above zero).
    public var any: Bool { files > 0 || notes > 0 || items > 0 }

    /// The counts a disconnect (DELETE /api/credentials/{name}) answers under "data". Nil when the answer has no "data" (an older
    /// server): then nothing is asked.
    public static func afterDisconnect(_ root: JSON) -> ConnectorDataCounts? {
        guard let data = root.objectValue?["data"], data.objectValue != nil else { return nil }
        return ConnectorDataCounts(Lenient(data))
    }

    /// The counts POST /api/connectors/{id}/forget answers: under "data" when wrapped, else at the top level.
    public static func afterForget(_ root: JSON) -> ConnectorDataCounts {
        if let data = root.objectValue?["data"], data.objectValue != nil { return ConnectorDataCounts(Lenient(data)) }
        return ConnectorDataCounts(Lenient(root))
    }
}
