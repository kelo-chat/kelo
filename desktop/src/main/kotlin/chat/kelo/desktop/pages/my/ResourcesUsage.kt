package chat.kelo.desktop.pages.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.ProviderInfo
import chat.kelo.app.my.Resources
import chat.kelo.app.my.ResourcesLoad
import chat.kelo.app.my.Usage
import chat.kelo.app.my.UsageGroup
import chat.kelo.app.my.UsageLogic
import chat.kelo.app.my.UsageWindow
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.providerLabel
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.Bar
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeyBox
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SubPage
import chat.kelo.desktop.ui.Tag
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/* 마이 → 리소스 and 사용량 (android/kelo .../my/ResourcesPage.kt and my/UsageUi.kt). */

/** The size profile in words. */
fun profileText(profile: String, long: Boolean): Int? = when (profile) {
    "recommended" -> R.string.resources_recommended
    "minimum" -> R.string.resources_minimum
    "unlimited" -> if (long) R.string.resources_unlimited_long else R.string.resources_unlimited
    "custom" -> R.string.resources_custom
    else -> null
}

private val PARTS = mapOf(
    "database" to R.string.resources_part_database, "uploads" to R.string.resources_part_uploads,
    "images" to R.string.resources_part_images,
    "notes" to R.string.resources_part_notes, "history" to R.string.resources_part_history, "other" to R.string.resources_part_other,
)

/**
 * What this assistant may use on the server machine (RAM, CPUs, storage). Where nothing was set the ceiling is that machine
 * itself, so that is what is shown (never just "no limit"). Reloaded every 10 seconds while the page is open. Until the first numbers
 * arrive it says it is loading, or, when reading them failed, why with "try again" (never a blank page).
 */
@Composable
fun ResourcesPage(app: AppContainer, onBack: () -> Unit, onCleanup: (() -> Unit)? = null) {
    var load by remember { mutableStateOf(ResourcesLoad()) }
    var attempt by remember { mutableIntStateOf(0) }
    LaunchedEffect(attempt) {
        while (true) {
            load = try {
                load.loaded(app.sessions.requireApi().get<Resources>("/api/resources"))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {   // offline, an error answer, or numbers this app cannot read
                load.readFailed()
            }
            delay(10_000)
        }
    }
    SubPage(stringResource(R.string.resources_title), onBack) {
        val res = load.data
        if (res == null) {
            if (load.showError) {
                ErrorText(stringResource(R.string.resources_load_failed))
                SecondaryButton(stringResource(R.string.retry), onClick = { load = load.retrying(); attempt++ })
            } else {
                MutedText(stringResource(R.string.loading), small = true)
            }
            return@SubPage
        }
        val memory = res.memory
        val disk = res.disk
        val host = res.host
        val ramCeiling = memory.limit ?: host.memory
        val cpuCeiling = res.cpus ?: host.cpus
        val diskCeiling = disk.limit ?: host.diskFree?.let { disk.used + it }
        val state = chat.kelo.app.my.StorageRules.state(res.alert)
        val clean = onCleanup?.takeIf { res.canClean }
        // a server from before the storage warnings sends no alert: its old 90% line still shows
        val full = state == chat.kelo.app.my.StorageState.OK && disk.limit != null && disk.used >= disk.limit!! * 0.9

        if (state != chat.kelo.app.my.StorageState.OK) {
            StorageWarning(state, res, partName = { k -> PARTS[k]?.let { stringResource(it) } ?: k }, onCleanup = clean)
        }

        MyCard {
            Text(profileText(res.profile, long = true)?.let { stringResource(it) } ?: res.profile, style = MaterialTheme.typography.titleSmall, color = Kelo.colors.ink)
            KeyBox {
                val used = memory.used?.let { stringResource(R.string.resources_used, storageSize(it)) }.orEmpty()
                val ceiling = ramCeiling?.let {
                    stringResource(if (memory.limit != null) R.string.resources_limit else R.string.resources_whole_computer, storageSize(it))
                }.orEmpty()
                Text(stringResource(R.string.resources_ram, used, ceiling).trimEnd(), color = Kelo.colors.ink)
                memory.used?.let { UseBar(it, ramCeiling) }
                Hint(storageSize(res.minimum.memory), storageSize(res.recommended.memory), memory.limit != null && memory.limit!! < res.minimum.memory)
            }
            KeyBox(first = false) {
                Text(
                    when {
                        cpuCeiling == null -> stringResource(R.string.resources_cpu_whole)
                        res.cpus != null -> stringResource(R.string.resources_cpu_max, cpuCount(cpuCeiling))
                        else -> stringResource(R.string.resources_cpu_all, cpuCount(cpuCeiling))
                    },
                    color = Kelo.colors.ink,
                )
                Hint(
                    stringResource(R.string.resources_count, cpuCount(res.minimum.cpus)),
                    stringResource(R.string.resources_count, cpuCount(res.recommended.cpus)),
                    res.cpus != null && res.cpus!! < res.minimum.cpus,
                )
            }
            KeyBox(first = false) {
                val ceiling = diskCeiling?.let {
                    stringResource(if (disk.limit != null) R.string.resources_limit else R.string.resources_disk_with_free, storageSize(it))
                }.orEmpty()
                Text(stringResource(R.string.resources_disk, storageSize(disk.used), ceiling).trimEnd(), color = Kelo.colors.ink)
                UseBar(disk.used, diskCeiling)
                Hint(storageSize(res.minimum.disk), storageSize(res.recommended.disk), disk.limit != null && disk.limit!! < res.minimum.disk)
                val parts = disk.parts.filter { it.value > 0 }.map { (k, n) ->
                    stringResource(R.string.resources_part, PARTS[k]?.let { stringResource(it) } ?: k, storageSize(n))
                }
                MutedText(parts.joinToString(stringResource(R.string.usage_group_joiner)).ifEmpty { stringResource(R.string.resources_empty) }, small = true)
                if (full) ErrorText(stringResource(R.string.resources_full), small = true)
                if (clean != null) SecondaryButton(stringResource(R.string.storage_clean_up), onClick = clean)
            }
            val q = stringResource(R.string.resources_unknown)
            Text(
                emphasized(
                    stringResource(R.string.resources_host, host.memory?.let { storageSize(it) } ?: q, host.cpus?.let { cpuCount(it) } ?: q, host.diskFree?.let { storageSize(it) } ?: q),
                    "assistant limits --choose", code = true,
                ),
                color = Kelo.colors.muted, style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun UseBar(used: Long, of: Long?) {
    if (of == null || of <= 0) return
    val f = (used.toFloat() / of).coerceIn(0f, 1f)
    Bar(f, hot = f > 0.9f)
}

@Composable
private fun Hint(min: String, rec: String, under: Boolean) {
    val text = stringResource(R.string.resources_hint, min, rec)
    if (under) ErrorText(stringResource(R.string.resources_under_min, text), small = true) else MutedText(text, small = true)
}

// ---- usage

@Composable
fun usageLabels(): List<Pair<String, String>> = UsageLogic.LABEL_SOURCES.zip(
    listOf(
        stringResource(R.string.usage_label_session), stringResource(R.string.usage_label_week),
        stringResource(R.string.usage_label_all_models), stringResource(R.string.usage_label_weekly),
        stringResource(R.string.usage_label_five_hour),
    ),
)

@Composable
fun usageGroups(usage: Usage?, model: String): List<UsageGroup> {
    val models = stringResource(R.string.usage_group_models)
    val joiner = stringResource(R.string.usage_group_joiner)
    return UsageLogic.groups(usage, model) { g -> UsageLogic.groupName(g, { models.format(it) }, joiner) }
}

@Composable
fun resetText(w: UsageWindow): String? {
    val now = System.currentTimeMillis()
    val r = UsageLogic.reset(w, now) ?: return null
    return when (val p = UsageLogic.phrase(r, now)) {
        UsageLogic.Phrase.Soon -> stringResource(R.string.usage_reset_soon_phrase)
        is UsageLogic.Phrase.InMinutes -> stringResource(R.string.usage_reset_in_minutes, p.n)
        is UsageLogic.Phrase.InHours -> stringResource(R.string.usage_reset_in_hours, p.n)
        is UsageLogic.Phrase.On -> stringResource(R.string.usage_reset_on, DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
            .withLocale(appLocale()).format(Instant.ofEpochMilli(p.epochMillis).atZone(ZoneId.systemDefault())))
        is UsageLogic.Phrase.Raw -> stringResource(R.string.usage_resets, TokenText.fit(p.text, 24))
    }
}

@Composable
fun UsageBar(w: UsageWindow, compact: Boolean, labels: List<Pair<String, String>>) {
    val c = Kelo.colors
    val reset = resetText(w)
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth()) {
            Text(UsageLogic.label(w.label, labels), color = c.ink, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(stringResource(R.string.usage_used, w.usedPercent), fontWeight = FontWeight.Bold, color = c.ink)
        }
        Bar(UsageLogic.barFraction(w.usedPercent), UsageLogic.isHot(w.usedPercent))
        val line = if (compact) reset.orEmpty() else {
            val left = stringResource(R.string.usage_left, UsageLogic.left(w.usedPercent))
            if (reset != null) stringResource(R.string.usage_left_and_reset, left, reset) else left
        }
        if (line.isNotEmpty()) MutedText(line, small = true)
    }
}

@Composable
fun UsageGroupsView(groups: List<UsageGroup>, full: Boolean) {
    val c = Kelo.colors
    val labels = usageLabels()
    Column {
        groups.forEachIndexed { i, g ->
            if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(top = 12.dp, bottom = 10.dp))
            if (!g.group.isNullOrEmpty()) {
                Row(Modifier.padding(top = if (i == 0) 6.dp else 0.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(g.name, fontWeight = FontWeight.Bold, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (g.active) Tag(stringResource(R.string.usage_in_use))
                }
            }
            if (UsageLogic.oneLine(groups, g, full)) {
                val parts = g.windows.map { stringResource(R.string.usage_one_line_part, UsageLogic.label(it.label, labels), it.usedPercent) }
                MutedText(parts.joinToString(stringResource(R.string.usage_group_joiner)), small = true, modifier = Modifier.padding(top = 4.dp))
            } else {
                g.windows.forEach { UsageBar(it, compact = !full, labels = labels) }
            }
        }
    }
}

/** The 사용량 row's value: "42% 사용" for providers with limits, else today's tokens; null while unknown. */
@Composable
fun usageSummary(usage: Usage?, model: String): String? {
    val groups = usageGroups(usage, model)
    UsageLogic.summaryPercent(groups)?.let { return stringResource(R.string.usage_used, it) }
    if (usage != null && usage.subscription == null) return stringResource(R.string.my_usage_today_tokens, fmtNumber(usage.today.sum))
    return null
}

/** All the limits of the chosen model, or (when it reports none) the tokens this app has used; and providers' balances. */
@Composable
fun UsagePage(app: AppContainer, usage: Usage?, model: String, onRefresh: () -> Unit, onBack: () -> Unit) {
    val c = Kelo.colors
    var providers by remember { mutableStateOf<List<ProviderInfo>>(emptyList()) }
    LaunchedEffect(Unit) { runCatching { app.sessions.requireApi().get<List<ProviderInfo>>("/api/providers") }.onSuccess { providers = it } }
    SubPage(stringResource(R.string.usage_title), onBack) {
        MyCard {
            val sub = usage?.subscription
            when {
                usage == null -> MutedText(stringResource(R.string.loading), small = true)
                sub != null -> {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(providerLabel(sub.provider, providers), style = MaterialTheme.typography.titleSmall, color = c.ink, modifier = Modifier.weight(1f),
                            maxLines = 2, overflow = TextOverflow.Ellipsis)
                        LinkButton(stringResource(R.string.refresh), onClick = onRefresh)
                    }
                    if (sub.error != null) MutedText(sub.error!!, small = true) else UsageGroupsView(usageGroups(usage, model), full = true)
                }
                else -> {
                    MutedText(stringResource(R.string.usage_no_limits_app_tokens), small = true)
                    Stat(stringResource(R.string.usage_today), fmtNumber(usage.today.sum))
                    Stat(stringResource(R.string.usage_total), fmtNumber(usage.total.sum))
                }
            }
        }
        usage?.jobs?.let { JobUsageCard(it) }
        usage?.accounts.orEmpty().filter { it.error == null }.forEach { a ->
            MyCard {
                Text(a.displayName.ifBlank { providerLabel(a.provider, providers) }, style = MaterialTheme.typography.titleSmall, color = c.ink)
                a.balance?.let { Stat(stringResource(R.string.balance_left), money(it, a.currency)) }
                a.limitRemaining?.let { Stat(stringResource(R.string.balance_limit_left), money(it, a.currency) + (a.limit?.let { l -> " / " + money(l, a.currency) } ?: "")) }
                a.usedToday?.let { Stat(stringResource(R.string.balance_used_today), money(it, a.currency)) }
                a.usedWeek?.let { Stat(stringResource(R.string.balance_used_week), money(it, a.currency)) }
                a.usedMonth?.let { Stat(stringResource(R.string.balance_used_month), money(it, a.currency)) }
            }
        }
    }
}

/** A kind of work in the app's words (android/kelo .../my/UsageUi.kt jobKindLabel). */
@Composable
fun jobKindLabel(kind: String): String = stringResource(
    when (chat.kelo.app.my.UsageLogic.jobKind(kind)) {
        "chat" -> R.string.usage_kind_chat
        "schedule" -> R.string.usage_kind_schedule
        "checklist" -> R.string.usage_kind_checklist
        "task" -> R.string.usage_kind_task
        "worker" -> R.string.usage_kind_worker
        "reflection" -> R.string.usage_kind_reflection
        "memory" -> R.string.usage_kind_memory
        "journal" -> R.string.usage_kind_journal
        "proactive" -> R.string.usage_kind_proactive
        "compaction" -> R.string.usage_kind_compaction
        "followup" -> R.string.usage_kind_followup
        "briefing" -> R.string.usage_kind_briefing
        "probe" -> R.string.usage_kind_probe
        else -> R.string.usage_kind_other
    },
)

/** Where the tokens went, today and this month, by kind of work, with the share reused from the provider's cache when it says. */
@Composable
fun JobUsageCard(jobs: chat.kelo.app.my.JobUsage) {
    val c = Kelo.colors
    MyCard {
        Text(stringResource(R.string.usage_jobs_title), style = MaterialTheme.typography.titleSmall, color = c.ink)
        listOf(stringResource(R.string.usage_today) to jobs.today, stringResource(R.string.usage_jobs_month) to jobs.month)
            .forEachIndexed { i, (title, part) ->
                if (i > 0) androidx.compose.material3.HorizontalDivider(color = c.line, modifier = Modifier.padding(vertical = 8.dp))
                val rows = chat.kelo.app.my.UsageLogic.jobRows(part)
                JobLine(title, chat.kelo.app.my.UsageLogic.partTokens(part).takeIf { rows.isNotEmpty() },
                    chat.kelo.app.my.UsageLogic.cachePercent(part.cacheReadTokens, part.inputTokens), bold = true)
                if (rows.isEmpty()) MutedText(stringResource(R.string.usage_jobs_empty), small = true)
                rows.forEach { r -> JobLine(jobKindLabel(r.kind), r.tokens, r.cachePercent) }
            }
        MutedText(stringResource(R.string.usage_jobs_note), small = true, modifier = Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun JobLine(label: String, tokens: Long?, cachePercent: Int?, bold: Boolean = false) {
    val c = Kelo.colors
    androidx.compose.foundation.layout.Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(label, color = c.ink, fontWeight = if (bold) FontWeight.Bold else null, modifier = Modifier.weight(1f))
            if (tokens != null) Text(stringResource(R.string.usage_jobs_tokens, fmtNumber(tokens)), fontWeight = FontWeight.Bold, color = c.ink)
        }
        if (cachePercent != null && tokens != null) MutedText(stringResource(R.string.usage_jobs_cache, cachePercent), small = true)
    }
}

@Composable
private fun Stat(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, color = Kelo.colors.ink, modifier = Modifier.weight(1f))
        Text(value, fontWeight = FontWeight.Bold, color = Kelo.colors.ink)
    }
}
