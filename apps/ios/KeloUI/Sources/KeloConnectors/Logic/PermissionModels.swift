import Foundation
import KeloCore

// What Kelo may do with a connector (server permissions.py "PERMISSIONS API"; Android connectors/ConnectorModels.kt).

/// GET /api/connectors/{id}/permissions: only the kinds the connector has.
public struct ConnectorPermissions: Decodable, Equatable, Sendable {
    public var connector: String
    public var name: String
    public var kinds: [PermissionKind]
    /// "Use when I'm not chatting": allow (the default) or off for schedules, background jobs and messages Kelo sends first.
    public var unattended: String
    public var unattendedDefault: String

    public init(connector: String = "", name: String = "", kinds: [PermissionKind] = [], unattended: String = "allow", unattendedDefault: String = "allow") {
        self.connector = connector; self.name = name; self.kinds = kinds
        self.unattended = unattended; self.unattendedDefault = unattendedDefault
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        connector = j.str("connector"); name = j.str("name"); kinds = j.list("kinds")
        unattended = j.obj("unattended").str("mode", "allow"); unattendedDefault = j.obj("unattended").str("default", "allow")
    }
}

/// One kind of action (read / change / send / delete) with its mode now, its default and its single actions.
public struct PermissionKind: Decodable, Equatable, Sendable {
    public var kind: String
    public var mode: String
    public var `default`: String
    public var actions: [PermissionAction]

    public init(kind: String, mode: String, default def: String, actions: [PermissionAction] = []) {
        self.kind = kind; self.mode = mode; self.default = def; self.actions = actions
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        kind = try j.require("kind", j.optStr("kind")); mode = j.str("mode"); self.default = j.str("default"); actions = j.list("actions")
    }
}

public struct PermissionAction: Decodable, Equatable, Sendable {
    public var id: String
    public var label: String
    public var mode: String
    public var `default`: String

    public init(id: String, label: String = "", mode: String, default def: String) {
        self.id = id; self.label = label; self.mode = mode; self.default = def
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id")); label = j.str("label"); mode = j.str("mode"); self.default = j.str("default")
    }
}

public enum PermissionRules {
    public static let modes = ["allow", "ask", "off"]

    /// Anything set away from its default (the "back to the defaults" link shows then).
    public static func changed(_ p: ConnectorPermissions) -> Bool {
        p.unattended != p.unattendedDefault || p.kinds.contains { k in k.mode != k.default || k.actions.contains { $0.mode != $0.default } }
    }

    /// "Use when I'm not chatting" is on while the server has it as anything but off.
    public static func unattendedOn(_ p: ConnectorPermissions) -> Bool { p.unattended != "off" }

    /// The PUT body for the switch: on is the default again, so it clears the setting (null); off sends "off".
    public static func unattendedBody(_ on: Bool) -> [String: JSON] { ["unattended": on ? .null : .string("off")] }

    /// The PUT body for one kind's new mode.
    public static func kindBody(_ kind: String, _ mode: String) -> [String: JSON] { [kind: .string(mode)] }

    /// The PUT body for one action: its default again clears the setting (null) so it follows the kind later.
    public static func actionBody(_ a: PermissionAction, _ mode: String) -> [String: JSON] {
        ["actions": .object([a.id: mode == a.default ? .null : .string(mode)])]
    }
}
