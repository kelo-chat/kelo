package chat.kelo.app.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.PointerIcon
import androidx.compose.ui.input.pointer.pointerHoverIcon
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.isSpecified
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.BackTitleBar
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalBackHandler
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.platform.LocalPageFrame
import chat.kelo.app.ui.platform.formFactor
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map

/** Pause after the last key before the search runs (a word typed in one go is one search). */
private const val SEARCH_DEBOUNCE_MS = 250L

/**
 * The chat's search page: a field (focused on open), the results newest first with their date and the match in bold, and the older
 * results on request near the end. Tapping a result calls [onOpen] with its message id; the chat opens at that message.
 * [agentName] names the assistant's rows; the user's own rows say "You".
 */
@Composable
fun ChatSearchScreen(agentName: String, onOpen: (Long) -> Unit, onBack: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val services = LocalAppServices.current
    val vm = rememberViewModel { ChatSearchViewModel(services.sessions) }
    val state by vm.state.collectAsStateWithLifecycle()
    LocalBackHandler.current(onBack)
    val time = rememberTimeLabeler()
    val you = string(Res.string.chat_search_you)
    val title = string(Res.string.chat_search_title)
    val clearWords = string(Res.string.chat_search_clear)
    val focus = remember { FocusRequester() }
    var field by remember { mutableStateOf(state.query) }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    // typing: the search runs once the typing pauses (the Search key runs it at once; the same text is not asked twice)
    LaunchedEffect(field) {
        delay(SEARCH_DEBOUNCE_MS)
        if (field != vm.state.value.query) vm.setQuery(field)
    }
    val list = rememberLazyListState()
    // the older results when the end is near
    LaunchedEffect(list) {
        snapshotFlow { (list.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0) to list.layoutInfo.totalItemsCount }
            .map { (lastShown, total) -> total > 0 && lastShown >= total - 3 }
            .distinctUntilChanged()
            .collect { nearEnd -> if (nearEnd) vm.loadMore() }
    }

    // the desktop page: Escape closes it (the window has no system back), the page sits below the floating header, centred and at
    // most the sub pages' width; the phone's page is unchanged
    val desktop = LocalFormFactor.current == FormFactor.Desktop
    val frame = LocalPageFrame.current
    val escape = if (desktop) Modifier.onPreviewKeyEvent { e ->
        if (e.type == KeyEventType.KeyDown && e.key == Key.Escape) { onBack(); true } else false
    } else Modifier
    Box(modifier.fillMaxSize().background(c.bg).then(escape)) {
        val column = if (desktop) {
            Modifier.align(Alignment.TopCenter).fillMaxSize()
                .then(if (frame.maxWidth.isSpecified) Modifier.widthIn(max = frame.maxWidth) else Modifier)
                .padding(top = frame.topInset)
        } else Modifier.fillMaxSize().statusBarsPadding().imePadding()
        Column(column) {
            BackTitleBar(title, onBack)
            KeloTextField(
                value = field,
                onValueChange = { field = it },
                placeholder = string(Res.string.chat_search_field),
                modifier = Modifier.padding(horizontal = 16.dp).focusRequester(focus),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { vm.setQuery(field) }),
                trailingIcon = if (field.isEmpty()) null else {
                    {
                        IconButton(onClick = { field = "" }) {
                            Icon(KeloIcons.Close, contentDescription = clearWords, tint = c.muted)
                        }
                    }
                },
            )
            Spacer(Modifier.height(12.dp))
            val hits = state.hits
            when {
                !ChatSearchRules.hasQuery(state.query) -> HintLine(string(formFactor(Res.string.chat_search_hint, Res.string.desktop_chat_search_hint)))
                hits.isEmpty() && state.failed -> HintLine(string(Res.string.chat_search_failed))
                hits.isEmpty() && state.searched && !state.loading -> HintLine(string(Res.string.chat_search_none))
                hits.isEmpty() -> Unit   // the first page is on its way
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    state = list,
                    contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 24.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(hits, key = { it.id }) { hit ->
                        HitRow(
                            who = if (ChatSearchRules.isMine(hit.role)) you else agentName,
                            date = time.label(ChatSearchRules.timeMs(hit.createdAt)),
                            snippet = snippetText(hit.snippet, c.accent),
                            onClick = { onOpen(hit.id) },
                        )
                    }
                    if (state.failed) item { HintLine(string(Res.string.chat_search_failed)) }
                }
            }
        }
    }
}

/** The tag of a result row (the desktop UI tests count and open the rows). */
const val HIT_TAG = "chat-search-hit"

/** A result: who said it and when (muted), then the snippet with the match in bold accent. */
@Composable
private fun HitRow(who: String, date: String, snippet: AnnotatedString, onClick: () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth()
            .background(c.card, RoundedCornerShape(12.dp))
            .testTag(HIT_TAG)
            .pointerHoverIcon(PointerIcon.Hand)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(who, modifier = Modifier.weight(1f), color = c.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            Text(date, color = c.muted, fontSize = 12.sp)
        }
        Text(snippet, color = c.ink, fontSize = 15.sp)
    }
}

/** A line in the page's middle: the hint, nothing found, a failed search. */
@Composable
private fun HintLine(text: String) {
    val c = Kelo.colors
    Box(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 32.dp), contentAlignment = Alignment.TopCenter) {
        Text(text, color = c.muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
    }
}

/** The snippet's plain parts as one text, the match in bold accent. */
private fun snippetText(raw: String, accent: Color): AnnotatedString = buildAnnotatedString {
    ChatSearchRules.snippet(raw).forEach { part ->
        if (part.match) withStyle(SpanStyle(fontWeight = FontWeight.Bold, color = accent)) { append(part.text) } else append(part.text)
    }
}
