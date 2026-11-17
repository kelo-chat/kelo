package chat.kelo.app.my

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SectionLabel
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/**
 * The notices area at the top of 마이 (above 계정과 기기): one compact card per unread notice. Nothing at all when there are none.
 * A tap opens the whole notice in a sheet (the desktop: a dialog); opening it reads it, so its card goes.
 */
@Composable
fun NoticesArea(model: NoticesModel, modifier: Modifier = Modifier) {
    val state by model.state.collectAsStateWithLifecycle()
    if (state.notices.isNotEmpty()) {
        Column(modifier.fillMaxWidth()) {
            SectionLabel(string(Res.string.notices_title))
            state.notices.forEach { n ->
                NoticeCard(n, Modifier.padding(bottom = 8.dp), onClick = { model.open(n) })
            }
        }
    }
    state.opened?.let { n -> NoticeSheet(n, onClose = { model.close() }) }
}

@Composable
private fun NoticeCard(n: Notice, modifier: Modifier, onClick: () -> Unit) {
    val c = Kelo.colors
    val important = NoticesRules.isImportant(n)
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(c.card)
            .border(if (important) 2.dp else 1.dp, if (important) c.accent else c.line, RoundedCornerShape(12.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        if (important) {
            Text(string(Res.string.notices_important), color = c.accent, style = MaterialTheme.typography.labelLarge)
        }
        Text(
            TokenText.fit(n.title), color = c.ink, style = MaterialTheme.typography.titleMedium,
            maxLines = 2, overflow = TextOverflow.Ellipsis,
        )
        if (n.body.isNotBlank()) {
            Text(
                n.body, color = c.muted, style = MaterialTheme.typography.bodyMedium,
                maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun NoticeSheet(n: Notice, onClose: () -> Unit) {
    val c = Kelo.colors
    val services = LocalAppServices.current
    val link = NoticesRules.safeLink(n.link)
    KeloModal(onDismiss = onClose) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (NoticesRules.isImportant(n)) {
                Text(string(Res.string.notices_important), color = c.accent, style = MaterialTheme.typography.labelLarge)
            }
            Text(TokenText.fit(n.title), color = c.ink, style = MaterialTheme.typography.titleLarge)
            if (n.date.isNotBlank()) MutedText(n.date, small = true)
            Text(n.body, color = c.ink, style = MaterialTheme.typography.bodyLarge)
            if (link != null) {
                PrimaryButton(string(Res.string.notices_more), onClick = { services.browser.open(link) }, modifier = Modifier.fillMaxWidth())
            }
            SecondaryButton(string(Res.string.notices_close), onClick = onClose, modifier = Modifier.fillMaxWidth())
        }
    }
}
