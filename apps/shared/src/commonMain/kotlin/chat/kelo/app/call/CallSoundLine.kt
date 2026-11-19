package chat.kelo.app.call

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.withFrameNanos
import chat.kelo.app.agent.AgentPicture
import chat.kelo.app.res.*
import chat.kelo.app.ui.character.KeloCharacter
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.voice.LevelTrail

/** The live sound line's shape (pure; CallLogicTest). Points are -1..1 offsets from the centre line, left to right. */
object CallSoundLineRules {
    /**
     * [levels] holds [n] levels oldest → newest (LevelTrail.copyInto). The newest [count] levels give the height at each point; a soft
     * travelling wave gives the shape. [synthetic]: Kelo speaks with the device voice, which gives no levels: a gentle level of 0.35 moves the line.
     */
    fun points(levels: FloatArray, n: Int, count: Int, timeSec: Float, synthetic: Boolean): FloatArray {
        val out = FloatArray(count)
        for (i in 0 until count) {
            val k = n - count + i
            val level = when {
                synthetic -> 0.35f
                k in 0 until n -> levels[k].coerceIn(0f, 1f)
                else -> 0f
            }
            val wave = kotlin.math.sin(i * 0.55f + timeSec * 7f)
            val edge = kotlin.math.sin(kotlin.math.PI.toFloat() * (i + 0.5f) / count)   // calm at both ends
            out[i] = (level * wave * edge).coerceIn(-1f, 1f)
        }
        return out
    }
}

/** A line across the call screen that moves with whoever speaks: the owner's mic while listening, Kelo's playback while speaking. */
@Composable
fun CallSoundLine(trail: LevelTrail, phase: CallSteps.Phase, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val active = phase == CallSteps.Phase.LISTENING || phase == CallSteps.Phase.HEARING || phase == CallSteps.Phase.SPEAKING
    val frame = remember { mutableLongStateOf(0L) }
    LaunchedEffect(active, reduce) {
        if (active && !reduce) while (true) withFrameNanos { frame.longValue = it }
    }
    val buf = remember { FloatArray(256) }
    val count = 48
    val lineColor = when (phase) {
        CallSteps.Phase.SPEAKING -> c.accent
        CallSteps.Phase.LISTENING, CallSteps.Phase.HEARING -> c.ink
        else -> c.line
    }
    Canvas(modifier.semantics { contentDescription = "" }) {
        frame.longValue          // a frame redraws the line
        trail.version            // a new level redraws it
        val n = trail.copyInto(buf)
        val synthetic = phase == CallSteps.Phase.SPEAKING && System.nanoTime() - trail.lastPushNanos > 300_000_000L
        val timeSec = if (reduce) 0f else frame.longValue / 1_000_000_000f
        val pts = CallSoundLineRules.points(buf, n, count, timeSec, synthetic)
        val cy = size.height / 2f
        val xs = FloatArray(count) { size.width * it / (count - 1) }
        val ys = FloatArray(count) { cy - pts[it] * size.height * 0.45f }
        val path = Path()
        path.moveTo(xs[0], ys[0])
        for (i in 1 until count) {
            val midX = (xs[i - 1] + xs[i]) / 2f
            val midY = (ys[i - 1] + ys[i]) / 2f
            path.quadraticBezierTo(xs[i - 1], ys[i - 1], midX, midY)
        }
        path.lineTo(xs[count - 1], ys[count - 1])
        drawPath(path, color = lineColor, style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round))
    }
}

/** This call's lines: the owner's words (right) and Kelo's reply (left), the newest one kept in view. */
@Composable
fun CallTranscript(lines: List<CallLine>, agentName: String, photo: ImageBitmap?, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val state = rememberLazyListState()
    LaunchedEffect(lines.size, lines.lastOrNull()?.text?.length) {
        if (lines.isNotEmpty()) {
            if (reduce) state.scrollToItem(lines.lastIndex) else state.animateScrollToItem(lines.lastIndex)
        }
    }
    LazyColumn(
        state = state,
        modifier = modifier,
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(lines, key = { it.id }) { line -> CallLineRow(line, agentName, photo) }
    }
}

/** One line of a call: the owner's words on the right, Kelo's on the left with its picture and name (the call screen, the chat's call card). */
@Composable
fun CallLineRow(line: CallLine, agentName: String, photo: ImageBitmap?) {
    val c = Kelo.colors
    if (line.mine) {
        Row(
            Modifier.fillMaxWidth().padding(start = 48.dp).testTag("call.line.mine").semantics(mergeDescendants = true) {},
            horizontalArrangement = Arrangement.End,
        ) {
            Text(
                line.text,
                style = MaterialTheme.typography.bodyLarge,
                color = c.ink,
                modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(c.mine).padding(horizontal = 15.dp, vertical = 11.dp),
            )
        }
    } else {
        Row(
            Modifier.fillMaxWidth().padding(end = 48.dp).testTag("call.line.kelo").semantics(mergeDescendants = true) {},
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.Top,
        ) {
            if (photo != null) {
                AgentPicture(agentName, photo, 28)
            } else {
                Box(Modifier.size(28.dp).clip(CircleShape).background(c.card), contentAlignment = Alignment.Center) {
                    KeloCharacter(KeloMood.Idle, Modifier.size(28.dp))
                }
            }
            Column {
                Text(agentName, style = MaterialTheme.typography.labelSmall, color = c.muted)
                Spacer(Modifier.height(2.dp))
                Text(
                    line.text,
                    style = MaterialTheme.typography.bodyLarge,
                    color = c.ink,
                    modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(c.bubble).padding(horizontal = 15.dp, vertical = 11.dp),
                )
            }
        }
    }
}

/** The words typed into the call: sent as a call turn (answered by voice). Takes focus when it shows. */
@Composable
fun CallTypeBar(onSend: (String) -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    var text by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    val send = { if (text.isNotBlank()) { onSend(text); text = "" } }
    Row(
        modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        KeloTextField(
            value = text,
            onValueChange = { text = it },
            placeholder = string(Res.string.call_type_hint),
            modifier = Modifier.weight(1f).focusRequester(focus).testTag("call.typeField"),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { send() }),
        )
        IconButton(onClick = send, enabled = text.isNotBlank(), modifier = Modifier.testTag("call.typeSend")) {
            Icon(KeloIcons.Send, string(Res.string.call_type_send), tint = c.accent)
        }
    }
}
