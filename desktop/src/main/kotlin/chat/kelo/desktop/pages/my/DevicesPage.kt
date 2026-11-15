package chat.kelo.desktop.pages.my

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import chat.kelo.desktop.core.LinkVerify
import chat.kelo.desktop.ui.FieldWithAction
import chat.kelo.desktop.ui.KeloTextField
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Device
import chat.kelo.app.core.DeviceList
import chat.kelo.app.my.PairStart
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.Browser
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeyBox
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SubPage
import chat.kelo.desktop.ui.Tag
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.WriterException
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** A pairing in progress: the link and when it stops working. */
private data class Pairing(val link: String, val untilMillis: Long)

/**
 * 마이 → 내 기기 (android/kelo .../my/DevicesPage.kt): every phone or computer that can open this assistant, the owner's marked
 * with a badge and this one with "(this device)". Add one with a QR code made here from the link the server returns (scan it with
 * the phone app), or copy the link; remove one without touching the others. Removing this device signs it out.
 */
@Composable
fun DevicesPage(app: AppContainer, onBack: () -> Unit, onSignedOut: () -> Unit) {
    val sessions = app.sessions
    val scope = rememberCoroutineScope()
    var devices by remember { mutableStateOf<List<Device>>(emptyList()) }
    var current by remember { mutableStateOf<Long?>(null) }
    var pair by remember { mutableStateOf<Pairing?>(null) }
    var left by remember { mutableStateOf(0L) }
    var err by remember { mutableStateOf("") }
    var copied by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<Device?>(null) }
    val noLink = stringResource(R.string.devices_no_link)

    suspend fun load() {
        runCatching { sessions.requireApi().get<DeviceList>("/api/devices") }.onSuccess { devices = it.devices; current = it.current }
    }
    LaunchedEffect(Unit) { load() }
    LaunchedEffect(pair) {
        val p = pair ?: return@LaunchedEffect
        while (true) {
            val s = maxOf(0L, Math.round((p.untilMillis - System.currentTimeMillis()) / 1000.0))
            left = s
            if (s == 0L) { pair = null; break }
            delay(2000)
            load()   // the new device shows up in the list as soon as it joins
        }
    }

    fun add() {
        err = ""; copied = false
        scope.launch {
            try {
                val api = sessions.requireApi()
                val p = api.post<PairStart>("/api/devices/pair", buildJsonObject { put("origin", api.baseUrl) })
                val link = p.link
                if (link.isNullOrEmpty()) { err = noLink; return@launch }
                pair = Pairing(link, System.currentTimeMillis() + p.expiresIn * 1000L)
                left = p.expiresIn.toLong()
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }

    fun remove(d: Device) {
        scope.launch {
            try {
                sessions.requireApi().delete<JsonElement>("/api/devices/${d.id}")
                if (d.id == current) { sessions.signOut(); onSignedOut() } else load()
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }

    // KE2E: the server's key this device pinned, its fingerprint, and whether it was verified (a link carrying the same key)
    val api by sessions.api.collectAsState()
    val pin = api?.keyPin
    var verifyText by remember { mutableStateOf("") }
    var verifyResult by remember { mutableStateOf<LinkVerify.Result?>(null) }
    SubPage(stringResource(R.string.devices_title), onBack) {
        MyCard {
            MutedText(stringResource(R.string.devices_intro), small = true)
            if (devices.isEmpty()) MutedText(stringResource(R.string.devices_empty), small = true)
            devices.forEachIndexed { i, d ->
                KeyBox(first = i == 0) {
                    Text(
                        TokenText.fit(if (d.id == current) stringResource(R.string.devices_this_one, d.name) else d.name),
                        fontWeight = FontWeight.Bold, color = Kelo.colors.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    )
                    // given on the server machine (assistant phone owner): this device can open the operator settings
                    if (d.owner) Tag(stringResource(R.string.devices_owner_badge))
                    MutedText(stringResource(R.string.devices_meta, fullDate(d.createdAt), agoText(d.lastSeen)), small = true)
                    LinkButton(stringResource(R.string.my_disconnect_button), onClick = { removing = d }, danger = true)
                }
            }
            val p = pair
            if (p != null) {
                KeyBox(first = devices.isEmpty()) { PairingBox(p, left, copied, onCopied = { copied = true }, onClose = { pair = null }) }
            } else {
                SecondaryButton(stringResource(R.string.devices_add), onClick = ::add)
            }
            if (p != null && !p.link.startsWith("https://") && !isLocal(p.link)) ErrorText(stringResource(R.string.devices_insecure), small = true)
            if (err.isNotEmpty()) ErrorText(err, small = true)
        }
        MyCard {
            Text(stringResource(R.string.devices_e2e_title), fontWeight = FontWeight.Bold, color = Kelo.colors.ink)
            MutedText(stringResource(R.string.desktop_e2e_on), small = true)
            if (pin != null) {
                MutedText(stringResource(R.string.devices_e2e_fingerprint), small = true)
                SelectionContainer {
                    Text(pin.key.fingerprint, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                }
                Tag(stringResource(if (pin.verified) R.string.devices_e2e_verified else R.string.devices_e2e_not_verified))
                if (!pin.verified) {
                    MutedText(stringResource(R.string.desktop_e2e_not_verified_body), small = true)
                    FieldWithAction(
                        field = { m -> KeloTextField(verifyText, { verifyText = it; verifyResult = null }, stringResource(R.string.desktop_e2e_paste_hint), m) },
                        action = {
                            SecondaryButton(stringResource(R.string.desktop_e2e_verify_paste), enabled = verifyText.isNotBlank(), onClick = {
                                verifyResult = LinkVerify.check(verifyText, sessions::verifyWith)
                                if (verifyResult == LinkVerify.Result.DONE) verifyText = ""
                            })
                        },
                    )
                }
            }
            when (verifyResult) {
                LinkVerify.Result.DONE -> MutedText(stringResource(R.string.devices_e2e_verify_done), small = true)
                LinkVerify.Result.MISMATCH -> ErrorText(stringResource(R.string.desktop_e2e_verify_mismatch), small = true)
                LinkVerify.Result.NO_KEY -> ErrorText(stringResource(R.string.desktop_e2e_verify_no_key), small = true)
                LinkVerify.Result.NOT_A_LINK -> ErrorText(stringResource(R.string.connect_link_invalid), small = true)
                null -> {}
            }
        }
    }
    removing?.let { d ->
        ConfirmDialog(
            text = if (d.id == current) stringResource(R.string.my_disconnect_confirm) else stringResource(R.string.devices_remove_confirm, d.name),
            yes = stringResource(R.string.my_disconnect_button),
            onYes = { remove(d) }, onDismiss = { removing = null },
        )
    }
}

private fun isLocal(link: String): Boolean =
    runCatching { java.net.URI(link.substringBefore('#')).host in setOf("localhost", "127.0.0.1") }.getOrDefault(false)

/** The QR for the new device, the time left, and the link to copy. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ColumnScope.PairingBox(p: Pairing, left: Long, copied: Boolean, onCopied: () -> Unit, onClose: () -> Unit) {
    val qr = remember(p.link) { qrImage(p.link) }
    if (qr != null) {
        Image(
            qr, contentDescription = stringResource(R.string.devices_qr_label), contentScale = ContentScale.Fit, filterQuality = FilterQuality.None,
            modifier = Modifier.align(Alignment.CenterHorizontally).size(220.dp).clip(RoundedCornerShape(8.dp)).background(Color.White).padding(4.dp),
        )
    }
    MutedText(stringResource(R.string.devices_qr_help, left / 60, left % 60), small = true)
    SelectionContainer { Text(p.link, style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted) }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SecondaryButton(stringResource(if (copied) R.string.devices_copied else R.string.devices_copy), onClick = { Browser.copy(p.link); onCopied() })
        SecondaryButton(stringResource(R.string.close), onClick = onClose)
    }
}

/** The link as a QR code (error correction M, like the server's own), one pixel per module, drawn without smoothing. */
fun qrImage(text: String): ImageBitmap? = try {
    val m = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, 0, 0, mapOf(EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M, EncodeHintType.MARGIN to 2))
    val img = java.awt.image.BufferedImage(m.width, m.height, java.awt.image.BufferedImage.TYPE_INT_RGB)
    for (y in 0 until m.height) for (x in 0 until m.width) img.setRGB(x, y, if (m.get(x, y)) 0x000000 else 0xFFFFFF)
    img.toComposeImageBitmap()
} catch (_: WriterException) {
    null
} catch (_: IllegalArgumentException) {
    null
}
