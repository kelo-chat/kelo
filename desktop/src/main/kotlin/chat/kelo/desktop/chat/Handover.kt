package chat.kelo.desktop.chat

import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.TurnEvent

/**
 * Where a human check handed to the user sits: the site that asks and the browser tab it is on; [check] what it is (a person check,
 * or a sign-in verification with the server's step in its name; null from an older server).
 */
data class CheckPlace(val site: String = "", val tab: String = "owner", val check: chat.kelo.app.chat.CheckInfo? = null)

/**
 * A site's check whether a person is there, handed to the user (the human_check turn event and pending item), as the chat shows
 * it: the card offers "solve it" (the tab's live view with control taken; the server ends the card by itself once the check is
 * gone) and "skip this site" (POST /api/approvals/{id} {"approve": false}). Also the approval titles: the server's whole question
 * when it sends one.
 */
object Handover {
    /** The tool name of a human check's approval card (ChatItem.Approval keeps no kind) */
    const val CHECK_TOOL = "browser_check"

    /** The server's question for an approval card's title ("로그인할까요?"), or null (an older server: the title is built as before). */
    fun question(ask: String): String? = ask.trim().ifEmpty { null }

    fun isCheck(a: ChatItem.Approval): Boolean = a.name == CHECK_TOOL

    /** The site and tab of each human check the chat has seen, kept up to date by its events. */
    fun places(before: Map<String, CheckPlace>, ev: TurnEvent): Map<String, CheckPlace> = when (ev) {
        is TurnEvent.ApprovalRequest -> if (ev.kind != PendingApproval.HUMAN_CHECK || ev.approvalId.isEmpty()) before
            else before + (ev.approvalId to (before[ev.approvalId] ?: CheckPlace()).let { p -> p.copy(tab = ev.tab.ifBlank { "owner" }, check = ev.check ?: p.check) })
        is TurnEvent.HumanCheck -> if (ev.approvalId.isEmpty()) before
            else before + (ev.approvalId to CheckPlace(ev.site, ev.tab.ifBlank { before[ev.approvalId]?.tab ?: "owner" }, ev.check ?: before[ev.approvalId]?.check))
        else -> before
    }

    /**
     * A human check ended on the server: solved (in the live view, here or on a phone) folds the card as answered yes, skipped as
     * answered no, expired or cancelled as run out; "waiting" changes nothing.
     */
    fun settle(items: List<ChatItem>, ev: TurnEvent.HumanCheck): List<ChatItem> = when (ev.state) {
        "solved" -> ChatReducer.answer(items, ev.approvalId, true)
        "skipped" -> ChatReducer.answer(items, ev.approvalId, false)
        "expired", "cancelled" -> items.map { if (it is ChatItem.Approval && it.id == ev.approvalId && it.open) it.copy(expired = true) else it }
        else -> items
    }
}
