package chat.kelo.app.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppServices
import chat.kelo.app.chat.vault.CardBox
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** The server's facts after `save_found_secret` (agent/facts.py: saved_key {service, hosts}) as a card's facts; pure, so it is tested. */
object KeySavedRules {
    data class Saved(val name: String, val hosts: List<String>)

    fun of(ev: TurnEvent.ToolResult): Saved? {
        val k = ev.facts["saved_key"] as? JsonObject ?: return null
        val name = (k["service"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf { it.isNotEmpty() } ?: return null
        val hosts = (k["hosts"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content?.trim()?.ifEmpty { null } }
        return Saved(name, hosts)
    }
}

/** "Saved in the Keychain": the key's name, where it is used, and the way to the Keychain (behind its own lock). */
@Composable
fun KeySavedCard(item: ChatItem.KeySaved) {
    val c = Kelo.colors
    val services = LocalAppServices.current
    CardBox {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(KeloIcons.Key, contentDescription = null, tint = c.accent)
                Text(string(Res.string.keysaved_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
                    color = c.ink, modifier = Modifier.weight(1f).semantics { heading() })
            }
            Text(item.name, style = MaterialTheme.typography.bodyMedium, color = c.ink)
            if (item.hosts.isNotEmpty()) {
                Text(string(Res.string.keysaved_hosts, item.hosts.joinToString(", ")), style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
            SecondaryButton(string(Res.string.keysaved_open), onClick = { services.openRequests.value = OpenTarget.Keychain },
                modifier = Modifier.fillMaxWidth())
        }
    }
}
