package chat.kelo.app.my

import chat.kelo.app.ui.text.*

import chat.kelo.app.res.*

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.border
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.clockText
import chat.kelo.app.agent.dayClockText
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.components.EmptyNote
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.theme.Kelo
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One item Kelo is keeping for a later message (GET /api/proactive/held). [why]: over_cap | waiting_for_reply. */
@Serializable
data class HeldItem(
    val key: String = "",
    val source: String = "",
    val title: String = "",
    val urgency: String? = null,
    val message: String = "",
    val why: String = "",
    @SerialName("held_at") val heldAt: Double = 0.0,
    @SerialName("expires_at") val expiresAt: Double = 0.0,
)

/** GET /api/proactive/held */
@Serializable
data class HeldList(val items: List<HeldItem> = emptyList())

/** The held items, apart from the UI. */
object DeliveryRules {
    enum class Why { OVER_CAP, WAITING_FOR_REPLY, OTHER }

    /** The server's reason code as a case; a code this app does not know, an old one ("slots", retired 2026-11-19) or none is [Why.OTHER]. */
    fun why(code: String): Why = when (code.trim()) {
        "over_cap" -> Why.OVER_CAP
        "waiting_for_reply" -> Why.WAITING_FOR_REPLY
        else -> Why.OTHER
    }

    /** Whether the server counts held items (and so has the list). */
    fun hasHeld(p: ProactiveSettings): Boolean = p.held != null

    /** The held count the settings show: null when the server has none or nothing is held (the link is then hidden). */
    fun heldCount(held: Int?): Int? = held?.takeIf { it > 0 }

    /** The line over a held item: its title, else the first line of its message. */
    fun heading(item: HeldItem): String =
        item.title.trim().ifEmpty { item.message.trim().lineSequence().firstOrNull().orEmpty() }

    /** The message as a one-line-per-paragraph preview (blank lines collapsed); empty when it only repeats the heading. */
    fun preview(item: HeldItem): String {
        val text = item.message.trim().lines().map { it.trim() }.filter { it.isNotEmpty() }.joinToString("\n")
        return if (text == heading(item)) "" else text
    }

    /** Whether a deadline falls on the same local day as [now] (then it is shown as a time only). */
    fun sameDay(epochSeconds: Double, nowSeconds: Double, zone: ZoneId = ZoneId.systemDefault()): Boolean =
        Instant.ofEpochMilli((epochSeconds * 1000).toLong()).atZone(zone).toLocalDate() ==
            Instant.ofEpochMilli((nowSeconds * 1000).toLong()).atZone(zone).toLocalDate()
}

/** What the held page shows: [items] is null while loading; [missing] is a server without the list (404). */
data class HeldUiState(val items: List<HeldItem>? = null, val missing: Boolean = false, val error: String = "")

/** Loads the held items once when the page opens (read-only). */
class ProactiveHeldViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(HeldUiState())
    val state: StateFlow<HeldUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@launch
            try {
                val list: HeldList = api.get("/api/proactive/held")
                _state.update { it.copy(items = list.items) }
            } catch (e: ApiError) {
                if (e.status == 404) _state.update { it.copy(missing = true) } else _state.update { it.copy(error = e.detail) }
            }
        }
    }
}

/**
 * The line under "하루 최대": "오늘 n번 보냈어요 · 보관 중 m개 ›" (the held part opens the list). Each part shows only when it is above 0,
 * the separator only when both show, and nothing at all when both are 0.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun SentHeldLine(sent: Int, held: Int?, onOpenHeld: () -> Unit) {
    val c = Kelo.colors
    val kept = DeliveryRules.heldCount(held)
    if (sent <= 0 && kept == null) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        if (sent > 0) Text(plural(Res.plurals.heads_sent_today, sent, sent), style = MaterialTheme.typography.bodySmall, color = c.muted)
        if (sent > 0 && kept != null) Text(string(Res.string.deliveries_separator), style = MaterialTheme.typography.bodySmall, color = c.muted)
        if (kept != null) {
            Row(
                Modifier.clickable(onClick = onOpenHeld), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(plural(Res.plurals.deliveries_held, kept, kept), style = MaterialTheme.typography.bodySmall, color = c.accent)
                Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.accent, modifier = Modifier.size(14.dp))
            }
        }
    }
}

/** The list of held items (read-only): title, message preview, why it is held, and until when. */
@Composable
fun ProactiveHeldPage(onBack: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = rememberViewModel { ProactiveHeldViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    SubPage(string(Res.string.deliveries_held_title), onBack) {
        val items = ui.items
        when {
            ui.error.isNotEmpty() -> ErrorText(ui.error, small = true)
            ui.missing -> MutedText(string(Res.string.notify_page_unavailable), small = true)
            items == null -> SkeletonRows(3)
            items.isEmpty() -> EmptyNote(string(Res.string.deliveries_held_empty))
            else -> {
                MutedText(string(Res.string.deliveries_held_about), small = true, modifier = Modifier.padding(horizontal = 16.dp))
                Column(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, Kelo.colors.line, RoundedCornerShape(12.dp)),
                ) {
                    items.forEachIndexed { i, item ->
                        if (i > 0) HorizontalDivider(color = Kelo.colors.line)
                        HeldRow(item)
                    }
                }
            }
        }
    }
}

@Composable
internal fun HeldRow(item: HeldItem) {
    val c = Kelo.colors
    val preview = DeliveryRules.preview(item)
    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(DeliveryRules.heading(item), style = MaterialTheme.typography.bodyLarge, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (preview.isNotEmpty()) Text(preview, style = MaterialTheme.typography.bodyMedium, color = c.muted, maxLines = 3, overflow = TextOverflow.Ellipsis)
        Text(whyText(DeliveryRules.why(item.why)), style = MaterialTheme.typography.bodySmall, color = c.muted)
        if (item.expiresAt > 0) Text(untilText(item.expiresAt), style = MaterialTheme.typography.bodySmall, color = c.muted)
    }
}

@Composable
internal fun whyText(why: DeliveryRules.Why): String = string(
    when (why) {
        DeliveryRules.Why.OVER_CAP -> Res.string.deliveries_why_cap
        DeliveryRules.Why.WAITING_FOR_REPLY -> Res.string.deliveries_why_reply
        DeliveryRules.Why.OTHER -> Res.string.deliveries_why_other
    },
)

/** "오후 6:00까지 보관" today, "11월 8일 (일) 오전 9:00까지 보관" another day. */
@Composable
internal fun untilText(expiresAt: Double, nowSeconds: Double = System.currentTimeMillis() / 1000.0): String =
    string(Res.string.deliveries_held_until, if (DeliveryRules.sameDay(expiresAt, nowSeconds)) clockText(expiresAt) else dayClockText(expiresAt))
