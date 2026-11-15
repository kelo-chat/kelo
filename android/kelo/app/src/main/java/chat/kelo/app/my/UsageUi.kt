package chat.kelo.app.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.theme.Kelo

/** The English phrases the CLIs report, paired with their translations. */
@Composable
fun usageLabels(): List<Pair<String, String>> = UsageLogic.LABEL_SOURCES.zip(
    listOf(
        stringResource(R.string.usage_label_session), stringResource(R.string.usage_label_week),
        stringResource(R.string.usage_label_all_models), stringResource(R.string.usage_label_weekly),
        stringResource(R.string.usage_label_five_hour),
    ),
)

/** The limit groups of [usage] for [model], named in the app's language. */
@Composable
fun usageGroups(usage: Usage?, model: String): List<UsageGroup> {
    val models = stringResource(R.string.usage_group_models)
    val joiner = stringResource(R.string.usage_group_joiner)
    return UsageLogic.groups(usage, model) { g -> UsageLogic.groupName(g, { models.format(it) }, joiner) }
}

/**
 * When a limit resets, in the phone's own zone and language: "3시간 후 초기화" when near, "11월 7일 오후 8:40에 초기화" further off,
 * or the server's words (shortened, never split in a word) when they cannot be read.
 */
@Composable
fun resetText(w: UsageWindow): String? {
    val now = System.currentTimeMillis()
    val r = UsageLogic.reset(w, now) ?: return null
    return when (val p = UsageLogic.phrase(r, now)) {
        UsageLogic.Phrase.Soon -> stringResource(R.string.usage_reset_soon_phrase)
        is UsageLogic.Phrase.InMinutes -> stringResource(R.string.usage_reset_in_minutes, p.n)
        is UsageLogic.Phrase.InHours -> stringResource(R.string.usage_reset_in_hours, p.n)
        is UsageLogic.Phrase.On -> stringResource(R.string.usage_reset_on, localDayTime(p.epochMillis))
        is UsageLogic.Phrase.Raw -> stringResource(R.string.usage_resets, chat.kelo.app.ui.components.TokenText.fit(p.text, 24))
    }
}

/** "11월 7일 오후 8:40": month, day and time in the phone's zone and the app's language. */
@Composable
private fun localDayTime(millis: Long): String {
    val locale = appLocale()
    val pattern = android.text.format.DateFormat.getBestDateTimePattern(locale, "MMMdjmm")
    return java.time.format.DateTimeFormatter.ofPattern(pattern, locale).format(java.time.Instant.ofEpochMilli(millis).atZone(java.time.ZoneId.systemDefault()))
}

/** The label and "42% 사용", the bar, and when it resets (with what is left, in the full form). */
@Composable
fun UsageBar(w: UsageWindow, compact: Boolean, labels: List<Pair<String, String>>) {
    val c = Kelo.colors
    val reset = resetText(w)
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth()) {
            Text(UsageLogic.label(w.label, labels), color = c.ink, modifier = Modifier.weight(1f), maxLines = 1,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            Text(stringResource(R.string.usage_used, w.usedPercent), fontWeight = FontWeight.Bold, color = c.ink)
        }
        Bar(UsageLogic.barFraction(w.usedPercent), UsageLogic.isHot(w.usedPercent))
        val line = if (compact) {
            reset.orEmpty()
        } else {
            val left = stringResource(R.string.usage_left, UsageLogic.left(w.usedPercent))
            if (reset != null) stringResource(R.string.usage_left_and_reset, left, reset) else left
        }
        if (line.isNotEmpty()) MutedText(line, small = true)
    }
}

/**
 * One block per group of models with its name. Where the model in use is known, its group is first and
 * marked, and (in the short form) the other groups take one line each with their percentages; otherwise every group is in full.
 */
@Composable
fun UsageGroupsView(groups: List<UsageGroup>, full: Boolean) {
    val c = Kelo.colors
    val labels = usageLabels()
    Column {
        groups.forEachIndexed { i, g ->
            if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(top = 12.dp, bottom = 10.dp))
            if (!g.group.isNullOrEmpty()) {
                Row(
                    Modifier.padding(top = if (i == 0) 6.dp else 0.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(g.name, fontWeight = FontWeight.Bold, color = c.ink, maxLines = 1,
                        overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
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


/** All the limits of the chosen model, or (when it reports none) the tokens this app has used. */
@Composable
fun UsagePage(usage: Usage?, model: String, onRefresh: () -> Unit, onBack: () -> Unit) {
    val c = Kelo.colors
    val sessions = chat.kelo.app.LocalAppContainer.current.sessions
    var providers by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf<List<chat.kelo.app.chat.ProviderInfo>>(emptyList()) }
    androidx.compose.runtime.LaunchedEffect(Unit) {
        runCatching { sessions.requireApi().get<List<chat.kelo.app.chat.ProviderInfo>>("/api/providers") }.onSuccess { providers = it }
    }
    SubPage(stringResource(R.string.usage_title), onBack) {
        MyCard {
            val sub = usage?.subscription
            when {
                usage == null -> chat.kelo.app.ui.components.SkeletonLines(3)
                sub != null -> {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            chat.kelo.app.chat.providerLabel(sub.provider, providers),
                            style = MaterialTheme.typography.titleSmall, color = c.ink, modifier = Modifier.weight(1f),
                            maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                        )
                        LinkButton(stringResource(R.string.refresh), onClick = onRefresh)
                    }
                    if (sub.error != null) MutedText(sub.error, small = true)
                    else UsageGroupsView(usageGroups(usage, model), full = true)
                }
                else -> {
                    MutedText(stringResource(R.string.usage_no_limits_app_tokens), small = true)
                    Stat(stringResource(R.string.usage_today), fmtNumber(usage.today.sum))
                    Stat(stringResource(R.string.usage_total), fmtNumber(usage.total.sum))
                }
            }
        }
        usage?.jobs?.let { JobUsageCard(it) }
        // API-key providers that report their balance and spend (only those that can: no rows for "not available")
        usage?.accounts.orEmpty().filter { it.error == null }.forEach { a ->
            MyCard {
                Text(a.displayName.ifBlank { chat.kelo.app.chat.providerLabel(a.provider, providers) }, style = MaterialTheme.typography.titleSmall, color = c.ink)
                a.balance?.let { Stat(stringResource(R.string.balance_left), money(it, a.currency)) }
                a.limitRemaining?.let { Stat(stringResource(R.string.balance_limit_left), money(it, a.currency) + (a.limit?.let { l -> " / " + money(l, a.currency) } ?: "")) }
                a.usedToday?.let { Stat(stringResource(R.string.balance_used_today), money(it, a.currency)) }
                a.usedWeek?.let { Stat(stringResource(R.string.balance_used_week), money(it, a.currency)) }
                a.usedMonth?.let { Stat(stringResource(R.string.balance_used_month), money(it, a.currency)) }
            }
        }
    }
}

/** A kind of work in the app's words. */
@Composable
fun jobKindLabel(kind: String): String = stringResource(
    when (UsageLogic.jobKind(kind)) {
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
fun JobUsageCard(jobs: JobUsage) {
    val c = Kelo.colors
    MyCard {
        Text(stringResource(R.string.usage_jobs_title), style = MaterialTheme.typography.titleSmall, color = c.ink)
        listOf(stringResource(R.string.usage_today) to jobs.today, stringResource(R.string.usage_jobs_month) to jobs.month)
            .forEachIndexed { i, (title, part) ->
                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(vertical = 8.dp))
                val rows = UsageLogic.jobRows(part)
                JobLine(title, UsageLogic.partTokens(part).takeIf { rows.isNotEmpty() }, UsageLogic.cachePercent(part.cacheReadTokens, part.inputTokens), bold = true)
                if (rows.isEmpty()) MutedText(stringResource(R.string.usage_jobs_empty), small = true)
                rows.forEach { r -> JobLine(jobKindLabel(r.kind), r.tokens, r.cachePercent) }
            }
        MutedText(stringResource(R.string.usage_jobs_note), small = true, modifier = Modifier.padding(top = 6.dp))
    }
}

/** A label that wraps on the left, its tokens on the right, and the cached share under it (only when reported). */
@Composable
private fun JobLine(label: String, tokens: Long?, cachePercent: Int?, bold: Boolean = false) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(label, color = c.ink, fontWeight = if (bold) FontWeight.Bold else null, modifier = Modifier.weight(1f))
            if (tokens != null) Text(stringResource(R.string.usage_jobs_tokens, fmtNumber(tokens)), fontWeight = FontWeight.Bold, color = c.ink)
        }
        if (cachePercent != null && tokens != null) MutedText(stringResource(R.string.usage_jobs_cache, cachePercent), small = true)
    }
}

/** "$0.19", "-$0.19", "¥12.30" in the app's language: two decimals, the currency's own symbol ([NumberText.money]). */
@Composable
private fun money(v: Double, currency: String): String = NumberText.money(v, currency, appLocale())

@Composable
private fun Stat(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text(label, color = Kelo.colors.ink, modifier = Modifier.weight(1f))
        Text(value, fontWeight = FontWeight.Bold, color = Kelo.colors.ink)
    }
}
