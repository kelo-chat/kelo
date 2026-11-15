package chat.kelo.desktop.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.ConsentService
import chat.kelo.app.chat.EffortChoice
import chat.kelo.app.chat.ModelChoice
import chat.kelo.app.chat.ModelChooserRules
import chat.kelo.desktop.chat.model.ConsentDialog
import chat.kelo.desktop.chat.model.rememberConsentGate
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.contentDescription
import chat.kelo.app.chat.ModelFacts
import chat.kelo.app.chat.ModelList
import chat.kelo.app.chat.ModelNames
import chat.kelo.app.chat.ModelTrait
import chat.kelo.app.chat.ModelTraits
import chat.kelo.app.chat.ProviderConsentRules
import chat.kelo.app.chat.ProviderInfo
import chat.kelo.app.chat.SetupRules
import chat.kelo.app.my.ProviderDetail
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Profile
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.MutedText
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

@Composable
fun effortLabel(v: String): String = when (v) {
    "" -> stringResource(R.string.chat_effort_default)
    "low" -> stringResource(R.string.chat_effort_low)
    "medium" -> stringResource(R.string.chat_effort_medium)
    "high" -> stringResource(R.string.chat_effort_high)
    "xhigh" -> stringResource(R.string.chat_effort_xhigh)
    "max" -> stringResource(R.string.chat_effort_max)
    else -> v
}

@Composable
fun modelBlurb(id: String, facts: ModelFacts?): String {
    val traits = ModelTraits.of(id, facts)
    if (traits.isEmpty()) return stringResource(R.string.model_trait_plain)
    return traits.map {
        stringResource(when (it) {
            ModelTrait.FAST -> R.string.model_trait_fast
            ModelTrait.REASONING -> R.string.model_trait_reasoning
            ModelTrait.LARGE -> R.string.model_trait_large
            ModelTrait.IMAGES -> R.string.model_trait_images
            ModelTrait.LONG -> R.string.model_trait_long
        })
    }.joinToString(stringResource(R.string.model_trait_separator))
}

/** The composer chip's words: "<model> <thinking level>" (the main model only; the fallback is shown in the chooser's settings). */
@Composable
fun modelChipText(profile: Profile, providers: List<ProviderInfo>, list: ModelList?): String {
    if (profile.model.isEmpty()) return stringResource(R.string.chat_model_none)
    val name = ModelNames.short(ModelNames.display(profile.model, list?.takeIf { it.provider == profile.provider }?.labels?.get(profile.model)))
    val effort = ModelChooserRules.effort(providers.find { it.name == profile.provider },
        list?.takeIf { it.provider == profile.provider }?.info?.get(profile.model))
    return if (effort is EffortChoice.Levels) stringResource(R.string.chat_model_chip, name, effortLabel(ModelChooserRules.shownLevel(effort, profile.effort))) else name
}

@Composable
fun providerLabel(id: String, providers: List<ProviderInfo>): String = when (ProviderConsentRules.service(id)) {
    ConsentService.CLAUDE_SUBSCRIPTION -> stringResource(R.string.consent_service_claude)
    ConsentService.CHATGPT_SUBSCRIPTION -> stringResource(R.string.consent_service_chatgpt)
    ConsentService.ANTIGRAVITY -> "Antigravity"
    ConsentService.QWEN -> "Qwen"
    ConsentService.MINIMAX -> "MiniMax"
    ConsentService.OTHER -> providers.firstOrNull { it.name == id }?.displayName?.trim()?.takeIf { it.isNotEmpty() && it != id }
        ?: stringResource(R.string.usage_provider_generic)
}

/**
 * The model chooser, as a panel over the chat (the phone's sheet, android/kelo .../chat/ModelChooser.kt): a folding "모델" row with
 * the model in use; opened, the current provider's models and the other ready providers, each section folding with its arrow
 * (the current one too); then the thinking level (folding the same way) and "자세한 설정". A click switches (PUT /api/profile; the
 * server empties a fallback on the same provider). An unofficial provider asks for the user's agreement first. Below the models,
 * every service that is not ready yet but can be set up from here ("OpenAI (API key) (needs setup) ›") opens the model settings on
 * it ([onSetup]), so a brand-new server with no model and no key can be set up from this panel alone (the desktop's own rows).
 */
@Composable
fun ModelChooser(
    sessions: Sessions,
    profile: Profile,
    providers: List<ProviderInfo>,
    onProfile: (Profile) -> Unit,
    onEffort: (String) -> Unit,
    onSetup: (provider: String?) -> Unit,
    onClose: () -> Unit,
    /** The chat's list for the current provider (the chip's), so the first section shows what the chip already knows */
    currentList: ModelList? = null,
) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    // the full provider rows (key, address, sign-in) tell which not-ready services can be set up from here
    // (the last ones kept on this computer at once, refreshed in place; see ModelCatalog)
    var details by remember { mutableStateOf(ModelCatalog.keptProviders(sessions).ifEmpty { null }) }
    LaunchedEffect(Unit) { runCatching { ModelCatalog.fetchProviders(sessions) }.onSuccess { details = it } }
    val setupRows = remember(details) { SetupRules.rows(details.orEmpty()) }
    val scope = rememberCoroutineScope()
    val gate = rememberConsentGate(sessions)
    val failed = stringResource(R.string.model_picker_list_failed)
    val sections = remember(profile.provider, providers) { ModelChooserRules.sections(profile.provider, providers) }
    // each provider's last good list shows at once (kept on this computer); opening a section refreshes it in place, once per open
    val lists = remember { mutableStateMapOf<String, ModelList>().apply { putAll(ModelCatalog.keptLists(sessions)) } }
    val fetched = remember { mutableSetOf<String>() }
    val open = remember { mutableStateMapOf<String, Boolean>() }
    var switching by remember { mutableStateOf<ModelChoice?>(null) }
    var err by remember { mutableStateOf("") }
    var levelsOpen by remember { mutableStateOf(false) }
    // the model list folds like the thinking level: closed at first, so the level and 자세한 설정 never sit below every model
    var listOpen by remember { mutableStateOf(ModelChooserRules.listStartsOpen(profile.model, sections)) }
    fun effortOf(provider: String, model: String) = ModelChooserRules.effort(providers.find { it.name == provider }, lists[provider]?.info?.get(model))
    /** After a pick: close when nothing is left to choose here, else fold the list (the thinking level is right under it) */
    fun afterPick(choice: ModelChoice) { if (ModelChooserRules.closeAfterPick(effortOf(choice.provider, choice.model))) onClose() else listOpen = false }

    LaunchedEffect(currentList) { if (currentList != null && currentList.provider.isNotEmpty()) lists[currentList.provider] = currentList }
    suspend fun load(provider: String) {
        if (!fetched.add(provider)) return
        val fresh = try {
            ModelCatalog.fetch(sessions, provider)
        } catch (_: ApiError) {
            ModelList(provider, emptyList(), error = failed)
        }
        ModelCatalog.shown(fresh, lists[provider])?.let { lists[provider] = it }
    }
    LaunchedEffect(sections) { sections.firstOrNull()?.let { if (it == profile.provider) load(it) } }   // also for the folded row's name

    fun switch(choice: ModelChoice) {
        if (switching != null) return
        if (choice.provider == profile.provider && choice.model == profile.model) { afterPick(choice); return }
        err = ""
        switching = choice
        scope.launch {
            try {
                onProfile(sessions.requireApi().put<Profile>("/api/profile", buildJsonObject {
                    put("provider", choice.provider); put("model", choice.model)   // the server empties a fallback on the same provider
                }))
                afterPick(choice)
            } catch (e: ApiError) {
                val needed = ProviderConsentRules.requiredBy(e)?.let { n -> providers.firstOrNull { it.name == n } }
                if (needed != null) { switching = null; gate.request(needed.copy(consent = null)) { switch(choice) } } else err = e.detail
            } finally {
                if (switching == choice) switching = null
            }
        }
    }
    fun pick(choice: ModelChoice) = gate.request(providers.firstOrNull { it.name == choice.provider }) { switch(choice) }

    Box(
        Modifier.fillMaxSize().background(c.scrim.copy(alpha = 0.32f))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClose),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.widthIn(max = 480.dp).fillMaxWidth(0.9f).heightIn(max = 760.dp).fillMaxHeight(0.8f).clip(RoundedCornerShape(20.dp)).background(c.card)
                .border(1.dp, c.line, RoundedCornerShape(20.dp))
                .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {},
        ) {
            Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.model_sheet_title), style = MaterialTheme.typography.titleLarge, color = c.ink, modifier = Modifier.weight(1f))
                IconButton(onClick = onClose) { Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = c.muted) }
            }
            if (err.isNotEmpty()) ErrorText(err, small = true, modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
            LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
                if (sections.isNotEmpty()) item(key = "list") { androidx.compose.foundation.layout.Column {
                    val currentList = lists[profile.provider]
                    ModelListRow(
                        provider = if (profile.provider.isEmpty()) "" else providerLabel(profile.provider, providers),
                        model = if (profile.model.isEmpty()) stringResource(R.string.chat_model_none)
                            else ModelNames.display(profile.model, currentList?.labels?.get(profile.model)),
                        open = listOpen,
                        onToggle = {
                            val (l, v) = ModelChooserRules.toggle(listOpen, levelsOpen, ModelChooserRules.Fold.LIST)
                            listOpen = l; levelsOpen = v
                        },
                    )
                    ChatsGoLine(profile.provider, providers.find { it.name == profile.provider }?.company,
                        Modifier.padding(start = 20.dp, end = 20.dp, bottom = 4.dp))
                } }
                // The whole list is one item that grows and shrinks (AnimatedVisibility), and each provider's models fold inside it
                // the same way, so the rows below move smoothly instead of jumping as rows come and go (as on Android).
                if (sections.isNotEmpty()) item(key = "sections") {
                    AnimatedVisibility(
                        visible = listOpen,
                        enter = expandVertically(Motion.enter(reduce), expandFrom = Alignment.Top) + fadeIn(Motion.standard(reduce)),
                        exit = shrinkVertically(Motion.exit(reduce), shrinkTowards = Alignment.Top) + fadeOut(Motion.small(reduce)),
                    ) {
                        Column(Modifier.fillMaxWidth()) {
                            sections.forEachIndexed { index, provider ->
                                val main = provider == profile.provider
                                // the current provider starts open and folds with its arrow like the others; the rest start folded
                                val expanded = open[provider] ?: main
                                if (sections.size > 1 || !main) {
                                    SectionHeader(providerLabel(provider, providers), expanded = expanded, first = index == 0, onToggle = {
                                        val next = !expanded
                                        open[provider] = next
                                        if (next) scope.launch { load(provider) }
                                    })
                                }
                                AnimatedVisibility(
                                    visible = expanded,
                                    enter = expandVertically(Motion.enter(reduce), expandFrom = Alignment.Top) + fadeIn(Motion.standard(reduce)),
                                    exit = shrinkVertically(Motion.exit(reduce), shrinkTowards = Alignment.Top) + fadeOut(Motion.small(reduce)),
                                ) {
                                    val list = lists[provider]
                                    val rows = ModelChooserRules.rows(provider, list, profile.provider to profile.model)
                                    val names = ModelNames.displayAll(rows, list?.labels.orEmpty())
                                    // the loading row turning into the models grows smoothly too
                                    Column(Modifier.fillMaxWidth().animateContentSize(Motion.standard(reduce))) {
                                        LaunchedEffect(provider) { load(provider) }   // once per open; a kept list shows meanwhile
                                        if (list == null) {
                                            Row(Modifier.padding(horizontal = 20.dp, vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                                                CircularProgressIndicator(Modifier.size(16.dp), color = c.muted, strokeWidth = 2.dp)
                                                MutedText(stringResource(R.string.loading), small = true)
                                            }
                                        }
                                        rows.forEach { id ->
                                            ModelRow(
                                                name = names.getValue(id), blurb = modelBlurb(id, list?.info?.get(id)),
                                                selected = main && id == profile.model, busy = switching == ModelChoice(provider, id),
                                                onClick = { pick(ModelChoice(provider, id)) },
                                            )
                                        }
                                        list?.error?.let { e -> MutedText(e, small = true, modifier = Modifier.padding(horizontal = 20.dp, vertical = 6.dp)) }
                                        if (list?.stale == true) {
                                            MutedText(stringResource(R.string.model_list_stale), small = true, modifier = Modifier.padding(horizontal = 20.dp, vertical = 6.dp))
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if (sections.isEmpty()) item {
                    MutedText(
                        stringResource(if (details != null && setupRows.isEmpty()) R.string.chooser_none_ready else R.string.desktop_chooser_setup_hint),
                        small = true, modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
                    )
                }
                // a model whose thinking level is part of its name (High/Medium/Low rows above) needs no extra row or note here;
                // the level only means something once a model is chosen
                val effortChoice = effortOf(profile.provider, profile.model)
                if (SetupRules.showsEffort(profile) && effortChoice !is EffortChoice.FromModel) item(key = "effort") {
                    val gap by animateDpAsState(if (listOpen) 8.dp else 0.dp, Motion.standard(reduce), label = "gap")
                    HorizontalDivider(Modifier.padding(top = gap), color = c.line)
                    EffortRow(
                        effortChoice, profile.effort, levelsOpen,
                        onToggle = {
                            val (l, v) = ModelChooserRules.toggle(listOpen, levelsOpen, ModelChooserRules.Fold.LEVELS)
                            listOpen = l; levelsOpen = v
                        },
                        onPick = { v -> levelsOpen = false; onEffort(v) },
                    )
                }
                item(key = "more") {
                    HorizontalDivider(color = c.line)
                    Row(Modifier.fillMaxWidth().clickable(role = Role.Button) { onSetup(null) }.padding(horizontal = 20.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(stringResource(R.string.chooser_more), style = MaterialTheme.typography.bodyLarge, color = c.ink)
                            Text(stringResource(R.string.chooser_more_hint), style = MaterialTheme.typography.bodySmall, color = c.muted)
                        }
                        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp))
                    }
                }
                // below the settings: the services that can still be set up from here (the desktop's own rows)
                if (setupRows.isNotEmpty()) {
                    item(key = "setup") {
                        HorizontalDivider(color = c.line)
                        Text(stringResource(R.string.desktop_chooser_setup_title), style = MaterialTheme.typography.labelLarge, color = c.muted,
                            modifier = Modifier.padding(start = 20.dp, end = 16.dp, top = 14.dp, bottom = 6.dp))
                    }
                    var unofficialShown = false
                    setupRows.forEach { p ->
                        if (p.unofficial && !unofficialShown) {
                            unofficialShown = true
                            item(key = "setup-unofficial") {
                                Text(stringResource(R.string.model_picker_unofficial), style = MaterialTheme.typography.labelMedium, color = c.muted,
                                    modifier = Modifier.padding(start = 20.dp, end = 16.dp, top = 10.dp, bottom = 4.dp))
                            }
                        }
                        item(key = "s:${p.name}") {
                            Row(
                                Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(role = Role.Button) { onSetup(p.name) }
                                    .padding(horizontal = 20.dp, vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
                            ) {
                                // the subscriptions by the names people know them by ("ChatGPT subscription", not "openai-codex")
                                val label = if (ProviderConsentRules.service(p.name) != ConsentService.OTHER) providerLabel(p.name, providers) else p.displayName.ifBlank { p.name }
                                Text(stringResource(R.string.model_picker_needs_setup, label), style = MaterialTheme.typography.bodyLarge,
                                    color = c.ink, modifier = Modifier.weight(1f))
                                Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                            }
                        }
                    }
                }
            }
        }
    }
    ConsentDialog(gate, providers)
}

@Composable
private fun SectionHeader(label: String, expanded: Boolean, first: Boolean, onToggle: () -> Unit) {
    val c = Kelo.colors
    val turn by animateFloatAsState(ModelChooserRules.chevronTurn(expanded), Motion.standard(LocalReduceMotion.current), label = "fold")
    val stateText = stringResource(if (expanded) R.string.a11y_expanded else R.string.a11y_collapsed)
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onToggle).semantics { stateDescription = stateText }
            .padding(start = 20.dp, end = 16.dp, top = if (first) 4.dp else 14.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = c.muted, modifier = Modifier.weight(1f))
        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp).rotate(turn))
    }
}

@Composable
private fun ModelRow(name: String, blurb: String, selected: Boolean, busy: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    val now = stringResource(R.string.chooser_in_use)
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).clickable(role = Role.RadioButton, onClick = onClick)
            .semantics(mergeDescendants = true) { this.selected = selected; if (selected) stateDescription = now }
            .padding(horizontal = 20.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(name, style = MaterialTheme.typography.bodyLarge, color = c.ink, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
            Text(blurb, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Box(Modifier.size(24.dp), contentAlignment = Alignment.Center) {
            when {
                busy -> CircularProgressIndicator(Modifier.size(18.dp), color = c.accent, strokeWidth = 2.dp)
                selected -> Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(22.dp))
            }
        }
    }
}

/**
 * The model list's folding row, built like the thinking level's: "모델" with the provider under it, the model in use on the right,
 * a chevron that turns when it opens. A long name wraps on the right instead of pushing the label away.
 */
@Composable
private fun ModelListRow(provider: String, model: String, open: Boolean, onToggle: () -> Unit) {
    val c = Kelo.colors
    val turn by animateFloatAsState(ModelChooserRules.chevronTurn(open), Motion.standard(LocalReduceMotion.current), label = "models")
    val fold = stringResource(if (open) R.string.a11y_expanded else R.string.a11y_collapsed)
    BoxWithConstraints(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).clickable(role = Role.Button, onClick = onToggle).semantics { stateDescription = fold }
            .padding(horizontal = 20.dp, vertical = 14.dp),
    ) {
        // the name takes at most about three fifths of the row, so "모델" and the provider keep room in any language
        val nameMax = maxWidth * ModelChooserRules.NAME_SHARE
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.model_picker_model), style = MaterialTheme.typography.bodyLarge, color = c.ink)
                if (provider.isNotEmpty()) Text(provider, style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
            Text(model, style = MaterialTheme.typography.bodyLarge, color = c.accent, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.End,
                modifier = Modifier.widthIn(max = nameMax))
            Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp).rotate(turn))
        }
    }
}

@Composable
private fun EffortRow(choice: EffortChoice, effort: String, open: Boolean, onToggle: () -> Unit, onPick: (String) -> Unit) {
    val c = Kelo.colors
    val title = stringResource(R.string.chat_effort)
    when (choice) {
        is EffortChoice.Levels -> Column {
            val shown = ModelChooserRules.shownLevel(choice, effort)
            val reduce = LocalReduceMotion.current
            val turn by animateFloatAsState(ModelChooserRules.chevronTurn(open), Motion.standard(reduce), label = "levels")
            val fold = stringResource(if (open) R.string.a11y_expanded else R.string.a11y_collapsed)
            Row(
                Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onToggle).semantics { stateDescription = fold }
                    .padding(horizontal = 20.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Column(Modifier.weight(1f)) {
                    Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
                    Text(stringResource(R.string.chooser_effort_hint), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                Text(effortLabel(shown), style = MaterialTheme.typography.bodyLarge, color = c.accent, fontWeight = FontWeight.SemiBold)
                Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp).rotate(turn))
            }
            AnimatedVisibility(
                visible = open,
                enter = expandVertically(Motion.enter(reduce), expandFrom = Alignment.Top) + fadeIn(Motion.standard(reduce)),
                exit = shrinkVertically(Motion.exit(reduce), shrinkTowards = Alignment.Top) + fadeOut(Motion.small(reduce)),
            ) { Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp).padding(bottom = 8.dp)) {
                choice.levels.forEach { v ->
                    val on = v == shown
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).then(if (on) Modifier.background(c.bubble) else Modifier)
                            .clickable(role = Role.RadioButton) { onPick(v) }.semantics(mergeDescendants = true) { selected = on }.padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(effortLabel(v), style = MaterialTheme.typography.bodyLarge, color = c.ink, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
                            if (v.isEmpty()) Text(stringResource(R.string.chooser_effort_default_hint), style = MaterialTheme.typography.bodySmall, color = c.muted)
                        }
                        if (on) Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(20.dp))
                    }
                }
            } }
        }
        else -> {
            // shown, but greyed, with the reason in words
            val reason = ModelChooserRules.effortNote(choice) ?: stringResource(R.string.chooser_effort_none)
            Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = "$title. $reason" }.padding(horizontal = 20.dp, vertical = 14.dp)) {
                Text(title, style = MaterialTheme.typography.bodyLarge, color = c.disabledInk)
                Text(reason, style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
        }
    }
}
