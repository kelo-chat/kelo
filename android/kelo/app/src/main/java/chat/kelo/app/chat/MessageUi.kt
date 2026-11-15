package chat.kelo.app.chat

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import kotlinx.coroutines.delay

/** How much a pressed-and-held bubble lifts. */
private const val LIFT_SCALE = 1.03f
/** How long the menu shows the check after 복사 before it closes. */
private const val COPY_CLOSE_MS = 650L

/**
 * A chat bubble that answers a long press: it lifts a little (scale and shadow), the phone gives a long-press tick, and a menu with
 * [actions] opens under it. 복사 is done here (the icon turns into a check for a moment, then the menu closes); the other actions go
 * to [onAction]. [copyText] gives what 복사 puts on the clipboard (worked out only then). [fromEnd]: the bubble sits on the trailing side (the user's own).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ActionBubble(
    actions: List<MsgAction>,
    copyText: () -> String,
    shape: Shape,
    fromEnd: Boolean,
    onAction: (MsgAction) -> Unit,
    content: @Composable () -> Unit,
) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val haptics = rememberHaptics()
    val clipboard = LocalClipboardManager.current
    var open by remember { mutableStateOf(false) }
    var copied by remember { mutableStateOf(false) }
    val lift by animateFloatAsState(if (open && !reduce) LIFT_SCALE else 1f, if (reduce) snap() else Motion.bump(), label = "lift")
    val shadow by animateDpAsState(if (open) 10.dp else 0.dp, Motion.small(reduce), label = "liftShadow")
    LaunchedEffect(copied) {
        if (copied) {
            delay(COPY_CLOSE_MS)
            open = false
            copied = false
        }
    }
    val menuLabel = stringResource(R.string.msg_actions_label)
    Box {
        Box(
            Modifier
                .graphicsLayer {
                    scaleX = lift
                    scaleY = lift
                    transformOrigin = TransformOrigin(if (fromEnd) 1f else 0f, 0.5f)
                }
                .shadow(shadow, shape, clip = false)
                .then(
                    if (actions.isEmpty()) Modifier
                    else Modifier.combinedClickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onLongClickLabel = menuLabel,
                        onLongClick = {
                            haptics.longPress()
                            copied = false
                            open = true
                        },
                        onClick = {},
                    ),
                ),
        ) { content() }
        DropdownMenu(
            expanded = open,
            onDismissRequest = { open = false; copied = false },
            containerColor = c.card,
            shape = RoundedCornerShape(14.dp),
            offset = DpOffset(0.dp, 6.dp),
        ) {
            for (a in actions) {
                val (icon, label) = when (a) {
                    MsgAction.COPY -> (if (copied) KeloIcons.Check else KeloIcons.Copy) to
                        stringResource(if (copied) R.string.msg_action_copied else R.string.msg_action_copy)
                    MsgAction.SHARE -> KeloIcons.Share to stringResource(R.string.msg_action_share)
                    MsgAction.SELECT -> KeloIcons.TextSelect to stringResource(R.string.msg_action_select)
                    MsgAction.RETRY -> KeloIcons.Retry to stringResource(R.string.msg_action_retry)
                    MsgAction.EDIT -> KeloIcons.Edit to stringResource(R.string.msg_action_edit)
                    MsgAction.REPORT -> KeloIcons.Flag to stringResource(R.string.msg_action_report)
                }
                MenuRow(icon, label, accent = a == MsgAction.COPY && copied) {
                    if (a == MsgAction.COPY) {
                        if (!copied) {
                            clipboard.setText(AnnotatedString(copyText()))
                            haptics.tick()
                            copied = true
                        }
                    } else {
                        open = false
                        onAction(a)
                    }
                }
            }
        }
    }
}

@Composable
private fun MenuRow(icon: ImageVector, label: String, accent: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    DropdownMenuItem(
        text = { Text(label, color = c.ink, style = MaterialTheme.typography.bodyMedium) },
        leadingIcon = { Icon(icon, contentDescription = null, tint = if (accent) c.success else c.muted, modifier = Modifier.size(20.dp)) },
        onClick = onClick,
    )
}

/** The system share sheet with [text]. */
fun shareText(context: Context, text: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
    try {
        context.startActivity(Intent.createChooser(send, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
    }
}

/**
 * 텍스트 선택: the message on a page of its own, where any part of it can be selected (press and hold, then drag the handles) and
 * copied with the system's selection menu, or all of it with 복사. The header and tab bar step aside; back closes it.
 */
@Composable
fun SelectTextPage(text: String, onClose: () -> Unit) {
    val c = Kelo.colors
    HideShellChrome("select-text", true)
    BackHandler(onBack = onClose)
    Column(
        Modifier
            .fillMaxSize()
            .background(c.bg)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {}   // nothing under it gets touches
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onClose) { Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = c.ink) }
            Text(
                stringResource(R.string.msg_select_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
            )
            CopyButton(text)
        }
        Text(
            stringResource(R.string.msg_select_hint), style = MaterialTheme.typography.bodySmall, color = c.muted,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
        )
        SelectionContainer(Modifier.weight(1f).fillMaxWidth()) {
            Text(
                text, style = MaterialTheme.typography.bodyLarge, color = c.ink,
                modifier = Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp),
            )
        }
    }
}

// ---- attachments

enum class AttachSource { PHOTO, CAMERA, FILE }

/** "+": 사진 (the system photo picker, no permission), 카메라, 파일. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AttachSheet(onPick: (AttachSource) -> Unit, onClose: () -> Unit) {
    val c = Kelo.colors
    ModalBottomSheet(
        onDismissRequest = onClose,
        sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(),
        containerColor = c.card,
    ) {
        Column(Modifier.fillMaxWidth().padding(bottom = 16.dp).navigationBarsPadding()) {
            Text(
                stringResource(R.string.attach_sheet_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
            )
            SheetRow(KeloIcons.Image, stringResource(R.string.attach_photo), stringResource(R.string.attach_photo_hint)) { onPick(AttachSource.PHOTO) }
            SheetRow(KeloIcons.Camera, stringResource(R.string.attach_camera), stringResource(R.string.attach_camera_hint)) { onPick(AttachSource.CAMERA) }
            SheetRow(KeloIcons.File, stringResource(R.string.attach_file), stringResource(R.string.attach_file_hint)) { onPick(AttachSource.FILE) }
        }
    }
}

@Composable
private fun SheetRow(icon: ImageVector, label: String, hint: String, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Box(Modifier.size(42.dp).clip(CircleShape).background(c.bubble), contentAlignment = Alignment.Center) {
            Icon(icon, contentDescription = null, tint = c.ink, modifier = Modifier.size(22.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(label, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(hint, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
    }
}

/** The picked photos and files above the composer: a picture or a file icon, the name, how far the upload is (or why it failed), and ×. */
@Composable
fun AttachmentTray(items: List<Attachment>, onRemove: (String) -> Unit) {
    if (items.isEmpty()) return
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 14.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        for (a in items) AttachmentChip(a, onRemove = { onRemove(a.id) })
    }
}

@Composable
private fun AttachmentChip(a: Attachment, onRemove: () -> Unit) {
    val c = Kelo.colors
    val failed = a.state == AttachState.FAILED
    Column(
        Modifier
            .widthIn(min = 150.dp, max = 230.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(c.card)
            .border(1.dp, if (failed) c.danger.copy(alpha = 0.6f) else c.line, RoundedCornerShape(14.dp)),
    ) {
        Row(Modifier.padding(start = 6.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).clip(RoundedCornerShape(10.dp)).background(c.bubble), contentAlignment = Alignment.Center) {
                val thumb = a.thumbnail
                if (thumb != null) Image(thumb, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.size(44.dp))
                else Icon(if (a.image) KeloIcons.Image else KeloIcons.File, contentDescription = null, tint = c.muted, modifier = Modifier.size(22.dp))
            }
            Column(Modifier.padding(start = 8.dp).width(130.dp)) {
                Text(a.name, style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                val status = when (a.state) {
                    AttachState.UPLOADING -> a.progress?.let { stringResource(R.string.attach_progress, (it * 100).toInt()) }
                        ?: stringResource(R.string.attach_preparing)
                    AttachState.DONE -> stringResource(R.string.attach_done)
                    AttachState.FAILED -> attachErrorText(a.error)
                }
                Text(
                    status, style = MaterialTheme.typography.labelSmall, color = if (failed) c.danger else c.muted,
                    maxLines = if (failed) 3 else 1, overflow = TextOverflow.Ellipsis,
                )
            }
            val removeLabel = stringResource(if (a.state == AttachState.UPLOADING) R.string.attach_cancel else R.string.attach_remove, a.name)
            IconButton(onClick = onRemove, modifier = Modifier.size(36.dp)) {
                Icon(KeloIcons.Close, contentDescription = removeLabel, tint = c.muted, modifier = Modifier.size(16.dp))
            }
        }
        if (a.state == AttachState.UPLOADING) {
            val p = a.progress
            if (p == null) {
                LinearProgressIndicator(Modifier.fillMaxWidth(), color = c.accent, trackColor = c.line, strokeCap = StrokeCap.Butt)
            } else {
                val shown by animateFloatAsState(p, Motion.small(LocalReduceMotion.current), label = "upload")
                LinearProgressIndicator(
                    progress = { shown }, modifier = Modifier.fillMaxWidth(), color = c.accent, trackColor = c.line,
                    strokeCap = StrokeCap.Butt, drawStopIndicator = {},
                )
            }
        }
    }
}

@Composable
private fun attachErrorText(e: AttachError?): String = when (e) {
    is AttachError.Server -> e.text
    AttachError.TooLarge -> stringResource(R.string.chat_upload_too_large)
    AttachError.Network -> stringResource(R.string.attach_error_network)
    AttachError.Unreadable, null -> stringResource(R.string.attach_error_unreadable)
}
