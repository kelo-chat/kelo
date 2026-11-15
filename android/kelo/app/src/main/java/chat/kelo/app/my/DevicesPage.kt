package chat.kelo.app.my

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.ColumnScope
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Device
import chat.kelo.app.core.DeviceList
import chat.kelo.app.core.ServerAddress
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.theme.Kelo
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.WriterException
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** A pairing in progress: the link and when it stops working. */
private data class Pairing(val link: String, val untilMillis: Long)

/**
 * Every phone or computer that can open this assistant. Add one with a QR code (made here from the link the server
 * returns); remove one without touching the others. The server has no way to rename a device, so names are shown as they are.
 */
@Composable
fun DevicesPage(onBack: () -> Unit, onSignedOut: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var devices by remember { mutableStateOf<List<Device>>(emptyList()) }
    var current by remember { mutableStateOf<Long?>(null) }
    var pair by remember { mutableStateOf<Pairing?>(null) }
    var left by remember { mutableStateOf(0L) }
    var err by remember { mutableStateOf("") }
    var copied by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<Device?>(null) }
    val noLink = stringResource(R.string.devices_no_link)
    var scanning by remember { mutableStateOf(false) }
    var keyNote by remember { mutableStateOf<Int?>(null) }
    val api by sessions.api.collectAsStateWithLifecycle()
    val pin = api?.keyPin

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
                if (d.id == current) onSignedOut() else load()
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }

    if (scanning) {
        // a pairing QR from the server machine: the same key as the pinned one marks it verified (the code is not used)
        chat.kelo.app.start.QrScanScreen(
            onClose = { scanning = false },
            accept = { raw -> chat.kelo.app.core.PairingLinks.parse(raw) != null },
            onScanned = { raw ->
                scanning = false
                val k = chat.kelo.app.core.PairingLinks.parse(raw)?.serverKey
                    ?.let { runCatching { chat.kelo.app.core.e2e.ServerKey.fromBase64Url(it) }.getOrNull() }
                val link = chat.kelo.app.core.PairingLinks.parse(raw)
                keyNote = when {
                    link?.serverKey == null -> R.string.devices_e2e_verify_no_key
                    k != null && sessions.verifyWith(k) -> R.string.devices_e2e_verify_done
                    else -> R.string.devices_e2e_verify_mismatch
                }
            },
        )
        return
    }
    SubPage(stringResource(R.string.devices_title), onBack) {
        MyCard {
            Text(stringResource(R.string.devices_e2e_title), fontWeight = FontWeight.Bold, color = Kelo.colors.ink)
            MutedText(stringResource(R.string.devices_e2e_on), small = true)
            if (pin != null) {
                MutedText(stringResource(R.string.devices_e2e_fingerprint), small = true)
                SelectionContainer {
                    Text(pin.key.fingerprint, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                }
                Tag(stringResource(if (pin.verified) R.string.devices_e2e_verified else R.string.devices_e2e_not_verified))
                if (!pin.verified) {
                    MutedText(stringResource(R.string.devices_e2e_not_verified_body), small = true)
                    SecondaryButton(stringResource(R.string.devices_e2e_verify_scan), onClick = { keyNote = null; scanning = true })
                }
            }
            keyNote?.let { n ->
                if (n == R.string.devices_e2e_verify_done) MutedText(stringResource(n), small = true) else ErrorText(stringResource(n), small = true)
            }
        }
        MyCard {
            MutedText(stringResource(R.string.devices_intro), small = true)
            if (devices.isEmpty()) MutedText(stringResource(R.string.devices_empty), small = true)
            devices.forEachIndexed { i, d ->
                KeyBox(first = i == 0) {
                    Text(
                        chat.kelo.app.ui.components.TokenText.fit(if (d.id == current) stringResource(R.string.devices_this_one, d.name) else d.name),
                        fontWeight = FontWeight.Bold, color = Kelo.colors.ink, maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                    )
                    // given on the server machine (assistant phone owner): this phone can open the operator settings
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
    }
    removing?.let { d ->
        ConfirmDialog(
            text = if (d.id == current) stringResource(R.string.my_disconnect_confirm) else stringResource(R.string.devices_remove_confirm, d.name),
            yes = stringResource(R.string.my_disconnect_button),
            onYes = { remove(d) }, onDismiss = { removing = null },
        )
    }
}

private fun isLocal(link: String): Boolean {
    val server = ServerAddress.normalize(link.substringBefore('#'), defaultScheme = null) ?: return false
    return runCatching { java.net.URI(server).host in setOf("localhost", "127.0.0.1") }.getOrDefault(false)
}

/** The QR for the new phone, the time left, and the link to copy or share. */
@Composable
private fun ColumnScope.PairingBox(p: Pairing, left: Long, copied: Boolean, onCopied: () -> Unit, onClose: () -> Unit) {
    val context = LocalContext.current
    val qr = remember(p.link) { qrBitmap(p.link) }
    val qrLabel = stringResource(R.string.devices_qr_label)
    if (qr != null) {
        Image(
            qr.asImageBitmap(), contentDescription = qrLabel, contentScale = ContentScale.Fit, filterQuality = FilterQuality.None,
            modifier = Modifier
                .align(Alignment.CenterHorizontally)
                .size(220.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(Color.White)
                .padding(4.dp),
        )
    }
    MutedText(stringResource(R.string.devices_qr_help, left / 60, left % 60), small = true)
    SelectionContainer {
        Text(p.link, style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
    }
    val shareTitle = stringResource(R.string.devices_share_title)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SecondaryButton(stringResource(if (copied) R.string.devices_copied else R.string.devices_copy), onClick = {
            val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText(qrLabel, p.link))
            onCopied()
        })
        SecondaryButton(stringResource(R.string.devices_share), onClick = {
            val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, p.link)
            runCatching { context.startActivity(Intent.createChooser(send, shareTitle)) }
        })
        SecondaryButton(stringResource(R.string.close), onClick = onClose)
    }
}

/** The link as a QR code (error correction M, like the server's own), one pixel per module; it is scaled up without smoothing. */
private fun qrBitmap(text: String): Bitmap? = try {
    val m = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, 0, 0, mapOf(EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M, EncodeHintType.MARGIN to 2))
    val w = m.width
    val h = m.height
    val pixels = IntArray(w * h) { i -> if (m.get(i % w, i / w)) android.graphics.Color.BLACK else android.graphics.Color.WHITE }
    Bitmap.createBitmap(pixels, w, h, Bitmap.Config.ARGB_8888)
} catch (_: WriterException) {
    null
} catch (_: IllegalArgumentException) {
    null
}
