package chat.kelo.app.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion

/** How far the reaction chip hangs below the user's bubble (the row keeps this much room under the bubble). */
val ReactionOverhang = 11.dp

/**
 * How far in from the bubble's end edge (right, left in RTL) the chip's box sits. The chip draws inside a 2 dp halo for its shadow,
 * so its visible edge is 12 dp in from the bubble's edge.
 */
val ReactionEndInset = 10.dp

private val ChipShape = RoundedCornerShape(50)

/**
 * The user's [bubble] with its [reaction] chip (nothing more when it is empty). The chip overlaps the bubble's bottom edge near its end
 * corner ([ReactionEndInset] in from the end side, so it mirrors in RTL) and hangs [ReactionOverhang] below it; this takes exactly
 * that much room under the bubble, so the chip never covers the next row. A bubble too short for the chip and the inset gets the chip
 * centred under it instead ([Reactions.chipStart]), so it never sticks out past the bubble's start edge.
 */
@Composable
fun BubbleWithReaction(reaction: String, modifier: Modifier = Modifier, bubble: @Composable () -> Unit) {
    Layout(
        content = {
            Box { bubble() }
            if (reaction.isNotEmpty()) ReactionChip(reaction)
        },
        modifier = modifier,
    ) { measurables, constraints ->
        val loose = constraints.copy(minWidth = 0, minHeight = 0)
        val b = measurables[0].measure(loose)
        val chip = measurables.getOrNull(1)?.measure(Constraints())
        val below = if (chip != null) ReactionOverhang.roundToPx() else 0
        val w = b.width.coerceIn(constraints.minWidth, constraints.maxWidth)
        val h = (b.height + below).coerceIn(constraints.minHeight, constraints.maxHeight)
        layout(w, h) {
            b.placeRelative(0, 0)
            if (chip != null) chip.placeRelative(Reactions.chipStart(b.width, chip.width, ReactionEndInset.roundToPx()), b.height + below - chip.height)
        }
    }
}

/**
 * The emoji on the user's own message ([Reactions]): a small round chip over the bubble's bottom edge near its end corner
 * ([BubbleWithReaction]). 👀 pops in when Kelo starts on the message and gives way to the final emoji with a small bounce (a plain
 * swap with reduced motion). The emoji is drawn with the system's emoji font, so a ZWJ sequence (🏃‍♂️) is one picture. A screen
 * reader hears "읽음" / "Read", then the emoji.
 */
@Composable
fun ReactionChip(emoji: String, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val read = stringResource(R.string.chat_reaction_read)
    val reacted = stringResource(R.string.chat_reaction_done, emoji)
    val spoken = Reactions.spoken(emoji, read) { reacted }
    AnimatedContent(
        targetState = emoji,
        transitionSpec = {
            if (reduce) fadeIn(snap()) togetherWith fadeOut(snap())
            else (scaleIn(spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessMediumLow), initialScale = 0.4f) +
                fadeIn(tween(120))) togetherWith (scaleOut(tween(120), targetScale = 0.6f) + fadeOut(tween(120)))
        },
        contentAlignment = Alignment.Center,
        label = "reaction",
        modifier = modifier.clearAndSetSemantics { contentDescription = spoken; liveRegion = LiveRegionMode.Polite },
    ) { shown ->
        Box(
            Modifier.padding(2.dp).defaultMinSize(minWidth = 22.dp, minHeight = 20.dp).shadow(1.5.dp, ChipShape).clip(ChipShape).background(c.bubble)
                .border(1.25.dp, c.bg, ChipShape).padding(horizontal = 4.dp, vertical = 2.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(shown, fontSize = 11.sp, lineHeight = 15.sp, color = c.ink, maxLines = 1, softWrap = false)
        }
    }
}
