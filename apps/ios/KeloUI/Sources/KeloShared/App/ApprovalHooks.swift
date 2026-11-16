import Foundation

/// What runs after the owner allowed an approval anywhere in the app (the chat, the work page): an approved calendar change or call
/// may now wait for this phone (마이 sets it; Android DeviceTasks.afterApproval).
@MainActor public enum ApprovalHooks {
    public static var afterAllow: () -> Void = {}
}
