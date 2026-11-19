package chat.kelo.app.chat

/**
 * The chat's call cards (owner 2026-11-20): the messages of one in-app call (stored with via=call and the call's id, server calls.py)
 * show as ONE row, [ChatItem.CallCard], that opens to the call's lines. The messages themselves are unchanged (Kelo's memory of the
 * call too); only what the list draws is folded. An ordinary message or another call's message between two lines ends the card (the
 * rest of the call makes a second card); rows that are not messages (tool chips, approval cards) stay in the list after the card.
 */
object CallCards {
    fun fold(items: List<ChatItem>): List<ChatItem> {
        if (items.none { it is ChatItem.Msg && it.callId != null }) return items
        val out = ArrayList<ChatItem>(items.size)
        var openAt = -1
        var openCall: String? = null
        for (row in items) {
            val m = row as? ChatItem.Msg
            val call = m?.callId
            when {
                m == null -> out += row
                call == null -> { openCall = null; out += row }
                call == openCall -> { val card = out[openAt] as ChatItem.CallCard; out[openAt] = card.copy(lines = card.lines + m) }
                else -> { openCall = call; openAt = out.size; out += ChatItem.CallCard("call:$call:${m.key}", call, listOf(m)) }
            }
        }
        return out
    }

    /** The rows a reply event added to the list ([after] minus [before]) that are messages get the call of the turn ([callId]). */
    fun tagNew(before: List<ChatItem>, after: List<ChatItem>, callId: String): List<ChatItem> {
        val old = before.mapTo(HashSet()) { it.key }
        return after.map { r -> if (r is ChatItem.Msg && r.callId == null && r.key !in old) r.copy(callId = callId, fromCall = true) else r }
    }

    /** The card in [rows] that holds message [id], or null. */
    fun cardWith(rows: List<ChatItem>, id: Long): ChatItem.CallCard? =
        rows.firstOrNull { it is ChatItem.CallCard && it.lines.any { l -> l.id == id } } as? ChatItem.CallCard

    /** The call's length for its title in whole minutes: from its first line to its last, rounded, at least 1. */
    fun minutes(card: ChatItem.CallCard): Int {
        val first = card.lines.first().at
        val last = card.lines.last().at
        if (first <= 0L || last <= first) return 1
        return maxOf(1, ((last - first + 30_000L) / 60_000L).toInt())
    }

    /** One line under the title: the owner's first words, else Kelo's; spaces and line breaks folded to one space. */
    fun preview(card: ChatItem.CallCard): String {
        val line = card.lines.firstOrNull { it.mine && it.text.isNotBlank() } ?: card.lines.firstOrNull { it.text.isNotBlank() }
        return line?.text?.replace(Regex("\\s+"), " ")?.trim().orEmpty()
    }
}
