// Ported from android/kelo .../agent/SkillParts.kt and the custom-skill calls of agent/AgentViewModel.kt (keep in step with them).
// The models and rules (SkillProposal, SkillRules) are the Android app's own agent/SkillModels.kt, shared at build time.
package chat.kelo.desktop.pages.work

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.SkillDiffLine
import chat.kelo.app.agent.SkillProposal
import chat.kelo.app.agent.SkillProposals
import chat.kelo.app.agent.SkillRules
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The calls behind the skills awaiting approval and "Save as skill" (android AgentViewModel: loadSkillProposals, answerSkill, saveAsSkill). */
internal object SkillCalls {
    /** GET /api/skills/proposals; an older server without custom skills answers 404, read as "not available". */
    suspend fun load(api: KeloApi): SkillProposals = try {
        api.get<SkillProposals>("/api/skills/proposals")
    } catch (e: ApiError) {
        if (e.status == 404) SkillProposals(available = false) else throw e
    }

    /** Approve (written as shown; an edited skill only as the owner saw it, so its hash goes along) or discard one proposal. */
    suspend fun answer(api: KeloApi, p: SkillProposal, approve: Boolean) {
        if (approve) api.post<JsonElement>(SkillRules.approvePath(p), p.contentHash?.let { h -> buildJsonObject { put("expected_hash", h) } })
        else api.post<JsonElement>(SkillRules.discardPath(p))
    }

    /** Ask Kelo to draft a skill from a finished chat task or background job (202; 409 busy, 404 not here, 503 no model). */
    suspend fun draft(api: KeloApi, taskId: Long? = null, workerId: String? = null) {
        api.post<JsonElement>(SkillRules.draftPath(taskId, workerId))
    }

    /** The message for a failed "Save as skill", or null when the server's own words should be shown. */
    fun draftError(status: Int): Int? = when (status) {
        409 -> R.string.skill_save_busy
        404 -> R.string.skill_save_unavailable
        503 -> R.string.skill_save_no_model
        else -> null
    }
}

/**
 * The skills waiting for the owner on the Records page, under the learned rules: what Kelo proposed or drafted from a finished task
 * (the full text, or the changed lines of a change) and skills whose files changed outside the app. Approve writes exactly the text
 * shown and Kelo uses it from then on; Discard drops it (for a skill changed outside the app: puts the approved text back).
 * [onAnswer] gets a callback that runs with true once the server took the answer (the card then shows the outcome until it leaves).
 */
@Composable
internal fun SkillProposalsBlock(data: SkillProposals?, onAnswer: (SkillProposal, Boolean, (Boolean) -> Unit) -> Unit) {
    if (data == null || !data.available) return
    val items = SkillRules.ordered(data.items)
    if (items.isEmpty() && data.drafting.isEmpty()) return
    val c = Kelo.colors
    Text(stringResource(R.string.skill_review_title), style = MaterialTheme.typography.labelLarge, color = c.accent,
        modifier = Modifier.padding(start = 4.dp, top = 10.dp, bottom = 2.dp))
    MutedText(stringResource(R.string.skill_review_about), small = true)
    if (data.drafting.isNotEmpty()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            CircularProgressIndicator(Modifier.size(14.dp), color = c.muted, strokeWidth = 2.dp)
            Text(stringResource(R.string.skill_review_drafting), style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(1f))
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        items.forEach { p -> androidx.compose.runtime.key(p.id) { SkillProposalCard(p, onAnswer) } }
    }
}

@Composable
private fun SkillProposalCard(p: SkillProposal, onAnswer: (SkillProposal, Boolean, (Boolean) -> Unit) -> Unit) {
    val c = Kelo.colors
    val haptics = rememberHaptics()
    val shape = RoundedCornerShape(12.dp)
    var all by remember(p.id) { mutableStateOf(false) }
    // null: not answered; true / false: approved / discarded (shown until the card leaves the list)
    var decided by remember(p.id) { mutableStateOf<Boolean?>(null) }
    var sending by remember(p.id) { mutableStateOf(false) }
    val edited = p.kind == SkillProposal.EDITED
    Column(
        Modifier.fillMaxWidth().clip(shape).background(c.accent.copy(alpha = 0.08f)).border(1.dp, c.accent.copy(alpha = 0.45f), shape)
            .padding(start = 14.dp, end = 14.dp, top = 12.dp, bottom = 10.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            stringResource(when (p.kind) {
                SkillProposal.CHANGE -> R.string.skill_review_kind_change
                SkillProposal.EDITED -> R.string.skill_review_kind_edited
                else -> R.string.skill_review_kind_new
            }),
            style = MaterialTheme.typography.labelMedium, color = c.accent,
        )
        Text(SkillRules.name(p), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink)
        if (p.description.isNotBlank()) Text(p.description, style = MaterialTheme.typography.bodyMedium, color = c.ink)
        Text(originText(p), style = MaterialTheme.typography.bodySmall, color = c.muted)
        if (edited) Note(stringResource(if (p.neverApproved) R.string.skill_review_edited_new_note else R.string.skill_review_edited_note))
        else if (p.outsideText) Note(stringResource(R.string.skill_review_outside_note))
        if (p.secretsRemoved > 0) Text(stringResource(R.string.skill_review_secrets_removed), style = MaterialTheme.typography.bodySmall, color = c.muted)
        // the text as it will be saved, or the lines a change adds and removes
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(8.dp)).padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(1.dp),
        ) {
            if (SkillRules.showsDiff(p)) {
                val (added, removed) = SkillRules.counts(p)
                Text(stringResource(R.string.skill_review_diff_counts, added, removed), style = MaterialTheme.typography.labelSmall, color = c.muted,
                    modifier = Modifier.padding(bottom = 4.dp))
                SkillRules.preview(p.diff, all).forEach { DiffLine(it) }
            } else {
                Text(SkillRules.preview(SkillRules.textLines(p.text), all).joinToString("\n"), style = MaterialTheme.typography.bodySmall,
                    color = c.ink)
            }
        }
        val more = if (SkillRules.showsDiff(p)) SkillRules.hasMore(p.diff) else SkillRules.hasMore(SkillRules.textLines(p.text))
        if (more) LinkButton(stringResource(if (all) R.string.skill_review_show_less else R.string.skill_review_show_all), onClick = { all = !all })
        val choice = decided
        if (choice == null) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                SecondaryButton(stringResource(if (edited && !p.neverApproved) R.string.skill_review_put_back else R.string.skill_review_discard),
                    modifier = Modifier.weight(1f), enabled = !sending, onClick = {
                        haptics.reject(); sending = true; decided = false
                        onAnswer(p, false) { ok -> sending = false; if (!ok) decided = null }
                    })
                PrimaryButton(stringResource(R.string.skill_review_approve), modifier = Modifier.weight(1f), enabled = !sending, onClick = {
                    haptics.confirm(); sending = true; decided = true
                    onAnswer(p, true) { ok -> sending = false; if (!ok) decided = null }
                })
            }
        } else {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(if (choice) KeloIcons.Check else KeloIcons.Minus, contentDescription = null, tint = if (choice) c.accent else c.muted,
                    modifier = Modifier.size(18.dp))
                Text(stringResource(if (choice) R.string.skill_review_approved else R.string.skill_review_discarded),
                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium, color = c.ink, modifier = Modifier.weight(1f))
                if (sending) CircularProgressIndicator(Modifier.size(16.dp), color = c.muted, strokeWidth = 2.dp)
            }
        }
    }
}

@Composable
private fun originText(p: SkillProposal): String {
    val from = stringResource(when (p.origin.type) {
        "draft" -> R.string.skill_review_from_draft
        "disk" -> R.string.skill_review_from_disk
        else -> R.string.skill_review_from_kelo
    })
    return if (p.reason.isNotBlank() && p.origin.type != "disk") "$from · ${p.reason}" else from
}

@Composable
private fun Note(text: String) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.infoSoft).padding(horizontal = 10.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top,
    ) {
        Icon(KeloIcons.Info, contentDescription = null, tint = c.infoInk, modifier = Modifier.size(16.dp).padding(top = 1.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = c.infoInk, modifier = Modifier.weight(1f))
    }
}

/** One line of a change: "+" on a soft green band, "−" on a soft red band, a gap as "⋯", a file name in bold. */
@Composable
private fun DiffLine(l: SkillDiffLine) {
    val c = Kelo.colors
    when (l.op) {
        "gap" -> Text("⋯", style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(vertical = 2.dp))
        "file" -> Text(l.text, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold, color = c.ink,
            modifier = Modifier.padding(top = 4.dp, bottom = 2.dp))
        else -> {
            val add = l.op == "add"
            val del = l.op == "del"
            val ink = if (add) c.successInk else if (del) c.dangerInk else c.muted
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(4.dp))
                    .background(if (add) c.successSoft else if (del) c.dangerSoft else Color.Transparent)
                    .padding(horizontal = 4.dp, vertical = 1.dp),
            ) {
                Text(if (add) "+" else if (del) "−" else " ", style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                    color = ink, modifier = Modifier.width(14.dp).alignByBaseline())
                Text(l.text, style = MaterialTheme.typography.bodySmall, color = ink, modifier = Modifier.weight(1f).alignByBaseline())
            }
        }
    }
}

/** The names of the tab and page where drafts wait (마이 → 기록), for the message after "Save as skill". */
@Composable
internal fun skillReviewPlace(): List<String> = listOf(stringResource(R.string.tab_my), stringResource(R.string.my_records_title))

/**
 * "Save as skill" under a finished chat task or background job: Kelo drafts a skill from the run (when to use it, the steps, pitfalls,
 * how to check it worked), and the draft waits for approval on the Records page.
 */
@Composable
internal fun SaveAsSkillButton(drafting: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        SecondaryButton(
            stringResource(if (drafting) R.string.skill_review_drafting else R.string.skill_save),
            onClick = onClick, enabled = !drafting, modifier = Modifier.fillMaxWidth(),
        )
        Text(stringResource(R.string.skill_save_about, stringResource(R.string.tab_my), stringResource(R.string.my_records_title)),
            style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(horizontal = 4.dp))
    }
}
