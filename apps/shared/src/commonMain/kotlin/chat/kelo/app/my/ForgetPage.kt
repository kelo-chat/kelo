package chat.kelo.app.my

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.ForgetRules
import chat.kelo.app.agent.ForgottenTopic
import chat.kelo.app.agent.GroupCard
import chat.kelo.app.agent.forgetCategoryWord
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.EmptyNote
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.theme.Kelo

/** 마이 → Forgotten topics: each topic with its date and what was removed, and "Allow again" (after a short confirm). */
@Composable
fun ForgetPage(onBack: () -> Unit) {
    val services = LocalAppServices.current
    val vm = rememberViewModel("my-forget") { ForgetViewModel(services.sessions) }
    val state by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.load() }
    val toaster = chat.kelo.app.platform.LocalToaster.current
    LaunchedEffect(vm) { vm.messages.collect { toaster.show(loadString(it)) } }
    val c = Kelo.colors
    SubPage(string(Res.string.forget_title), onBack) {
        MutedText(string(Res.string.forget_intro), small = true)
        val items = state.items
        when {
            items == null -> if (state.failed) ErrorText(string(Res.string.connect_failed), small = true) else SkeletonRows(3)
            items.isEmpty() -> EmptyNote(string(Res.string.forget_empty))
            else -> GroupCard {
                items.forEachIndexed { i, t ->
                    if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                    ForgottenTopicRow(t, busy = t.id in state.busy, onAllow = { vm.allowAgain(t) })
                }
            }
        }
    }
}

/** What was removed as words and counts ("Memory: 3 · Notes: 2"), or that nothing was found. */
@Composable
fun forgottenRemovedLine(t: ForgottenTopic): String {
    val parts = ForgetRules.parts(t.removed)
    if (parts.isEmpty()) return string(Res.string.forget_nothing_removed)
    val counts = parts.map { (cat, n) -> string(Res.string.forget_count, forgetCategoryWord(cat), fmtNumber(n.toLong())) }
    return string(Res.string.forget_removed, counts.joinToString(" · "))
}

@Composable
internal fun ForgottenTopicRow(t: ForgottenTopic, busy: Boolean, onAllow: () -> Unit) {
    val c = Kelo.colors
    var confirm by remember { mutableStateOf(false) }
    val label = TokenText.fit(t.label, 40)
    Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
        if (t.createdAt > 0) Text(string(Res.string.forget_date, fullDate(t.createdAt)), style = MaterialTheme.typography.bodySmall, color = c.muted)
        Text(forgottenRemovedLine(t), style = MaterialTheme.typography.bodySmall, color = c.ink)
        SecondaryButton(string(Res.string.forget_allow_again), enabled = !busy, modifier = Modifier.fillMaxWidth(), onClick = { confirm = true })
    }
    if (confirm) {
        ConfirmDialog(string(Res.string.forget_allow_confirm, label), string(Res.string.forget_allow_again), danger = false,
            onDismiss = { confirm = false }, onYes = { confirm = false; onAllow() })
    }
}
