package chat.kelo.app.wallet

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.util.Size
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.SecureFlagPolicy
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import chat.kelo.app.BuildConfig
import chat.kelo.app.R
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.android.gms.common.moduleinstall.ModuleInstall
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest
import com.google.android.gms.tasks.Task
import com.google.android.gms.wallet.PaymentCardRecognitionIntentRequest
import com.google.android.gms.wallet.PaymentCardRecognitionResult
import com.google.android.gms.wallet.PaymentsClient
import com.google.android.gms.wallet.Wallet
import com.google.android.gms.wallet.WalletConstants
import com.google.mlkit.common.MlKitException
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Scanning a card into the card form: Google Pay's card recognition first (its own screen in Google Play services, no camera
 * permission for Kelo), else Kelo's camera with ML Kit's on-device text recognition (the recognizer comes with Google Play
 * services, so the APK stays small). Either way the picture never leaves the phone, and only the number and expiry come back; the
 * CVC is never read. Which way works is checked once per app run ([CardScanAvailability]); without either, there is no button.
 */
internal object CardScanAvailability {
    @Volatile private var cache: ScanPathCache? = null

    fun cache(context: Context): ScanPathCache {
        cache?.let { return it }
        val app = context.applicationContext
        return synchronized(this) { cache ?: ScanPathCache { probe(app) }.also { cache = it } }
    }

    /**
     * Google Pay's card recognition runs in the test environment on debug builds; a release build asks the production one, which
     * opens only for an app registered for it in the Google Pay & Wallet Console (until then the camera scan is used).
     */
    fun payments(context: Context): PaymentsClient = Wallet.getPaymentsClient(
        context,
        Wallet.WalletOptions.Builder()
            .setEnvironment(if (BuildConfig.CARD_SCAN_TEST) WalletConstants.ENVIRONMENT_TEST else WalletConstants.ENVIRONMENT_PRODUCTION)
            .build(),
    )

    private suspend fun probe(context: Context): ScanProbe {
        val camera = context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA)
        val play = runCatching {
            GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(context) == ConnectionResult.SUCCESS
        }.getOrDefault(false)
        if (!camera || !play) return ScanProbe(camera, play, googlePay = false, textRecognizer = false)
        val googlePay = withTimeoutOrNull(8_000) {
            runCatching { payments(context).getPaymentCardRecognitionIntent(PaymentCardRecognitionIntentRequest.getDefaultInstance()).await() }
                .getOrNull()?.paymentCardRecognitionPendingIntent != null
        } ?: false
        // the text recognizer is a Play services module: fetched ahead of time when the camera scan is the way this phone scans
        // (ML Kit would otherwise fetch it on first use)
        if (!googlePay) runCatching {
            val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
            val modules = ModuleInstall.getClient(context)
            val ready = withTimeoutOrNull(5_000) { modules.areModulesAvailable(recognizer).await().areModulesAvailable() } ?: false
            if (!ready) modules.installModules(ModuleInstallRequest.newBuilder().addApi(recognizer).build())
            recognizer.close()
        }
        return ScanProbe(camera, play, googlePay, textRecognizer = true)
    }
}

internal suspend fun <T> Task<T>.await(): T = suspendCancellableCoroutine { cont ->
    addOnCompleteListener { t ->
        val e = t.exception
        when {
            e != null -> cont.resumeWithException(e)
            t.isCanceled -> cont.cancel()
            else -> cont.resume(t.result)
        }
    }
}

/**
 * Google Pay's card recognition is its own screen, so the app is stopped while it is up. The open card pages do not lock for that
 * (WalletPage), or the form waiting for the scan would be gone when it comes back; their two-minute limit still runs.
 */
internal object CardScanAway {
    private var away = false

    @Synchronized fun started() { away = true }
    @Synchronized fun ended() { away = false }
    @Synchronized fun active(): Boolean = away
}

private enum class ScanNote { FILLED, NOTHING, CAMERA_NEEDED, CAMERA_FAILED }

/**
 * The 카드 스캔 button under a card number field, and the line under it after a scan. [onResult] gets what was read; the form puts it
 * into its own fields. Shows nothing on a phone that cannot scan.
 */
@Composable
internal fun CardScanButton(onResult: (CardScanResult) -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val cache = remember { CardScanAvailability.cache(context) }
    var path by remember { mutableStateOf(cache.path) }
    LaunchedEffect(Unit) { if (path == null) path = cache.get() }
    var note by remember { mutableStateOf<ScanNote?>(null) }
    var camera by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val deliver by rememberUpdatedState(onResult)
    fun got(r: CardScanResult?) {
        if (r == null) { note = ScanNote.NOTHING; return }
        note = ScanNote.FILLED
        deliver(r)
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) { note = null; camera = true } else note = ScanNote.CAMERA_NEEDED
    }
    fun openCamera() {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) { note = null; camera = true }
        else permission.launch(Manifest.permission.CAMERA)
    }
    val googlePay = rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { r ->
        CardScanAway.ended()
        val data = r.data
        if (r.resultCode == Activity.RESULT_OK && data != null) {
            val read = runCatching { PaymentCardRecognitionResult.getFromIntent(data) }.getOrNull()
            val exp = read?.creditCardExpirationDate
            got(CardScanFill.fromGooglePay(read?.pan, exp?.month, exp?.year))
            data.replaceExtras(Bundle())   // the result's copy of the number goes with the intent
        }
    }
    fun start() {
        note = null
        when (path) {
            ScanPath.GOOGLE_PAY -> {
                busy = true
                scope.launch {
                    val pending = runCatching {
                        CardScanAvailability.payments(context)
                            .getPaymentCardRecognitionIntent(PaymentCardRecognitionIntentRequest.getDefaultInstance()).await()
                            .paymentCardRecognitionPendingIntent
                    }.getOrNull()
                    busy = false
                    if (pending != null) CardScanAway.started()
                    val launched = pending != null && runCatching { googlePay.launch(IntentSenderRequest.Builder(pending.intentSender).build()) }.isSuccess
                    if (!launched) {
                        CardScanAway.ended()
                        path = cache.googlePayFailed()
                        if (path == ScanPath.ON_DEVICE) openCamera()
                    }
                }
            }
            ScanPath.ON_DEVICE -> openCamera()
            else -> Unit
        }
    }

    val p = path
    if (p == null || p == ScanPath.NONE) return
    val c = Kelo.colors
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedButton(
            onClick = ::start, enabled = !busy, shape = RoundedCornerShape(50), border = BorderStroke(1.dp, c.line),
            colors = ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink, disabledContainerColor = c.card, disabledContentColor = c.disabledInk),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
            modifier = Modifier.widthIn(min = 48.dp),
        ) {
            Icon(KeloIcons.Camera, contentDescription = null, modifier = Modifier.size(18.dp))
            Text(stringResource(R.string.card_scan_button), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 8.dp))
        }
        when (note) {
            ScanNote.FILLED -> MutedText(stringResource(R.string.card_scan_filled), small = true, modifier = Modifier.padding(horizontal = 4.dp))
            ScanNote.NOTHING -> MutedText(stringResource(R.string.card_scan_nothing), small = true, modifier = Modifier.padding(horizontal = 4.dp))
            ScanNote.CAMERA_FAILED -> MutedText(stringResource(R.string.card_scan_camera_failed), small = true, modifier = Modifier.padding(horizontal = 4.dp))
            ScanNote.CAMERA_NEEDED -> {
                MutedText(stringResource(R.string.card_scan_camera_needed), small = true, modifier = Modifier.padding(horizontal = 4.dp))
                LinkButton(stringResource(R.string.scan_open_settings), modifier = Modifier.padding(horizontal = 4.dp), onClick = {
                    runCatching {
                        context.startActivity(
                            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    }
                })
            }
            null -> Unit
        }
    }
    if (camera) {
        CardScanCamera(
            onClose = { camera = false },
            onResult = { r -> camera = false; got(r) },
            onFailed = { camera = false; note = ScanNote.CAMERA_FAILED },
        )
    }
}

/**
 * The camera scan, over the whole screen and kept out of screenshots (FLAG_SECURE on its own window). Frames are read on the phone
 * by ML Kit and closed at once; only the text lines go to [CardScanText], and the scan ends once [CardScanAgreement]'s frames agree.
 */
@Composable
private fun CardScanCamera(onClose: () -> Unit, onResult: (CardScanResult) -> Unit, onFailed: () -> Unit) {
    // the dialog is a window of its own, so the activity's FLAG_SECURE does not reach it: it is always kept out of screenshots
    // and the recent-apps preview here (also on debug builds). The words sit at the top: a dialog window can be taller than the
    // part of the screen it shows, so nothing is put at its bottom edge.
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false, securePolicy = SecureFlagPolicy.SecureOn)) {
        var preparing by remember { mutableStateOf(false) }
        val c = Kelo.colors
        Box(Modifier.fillMaxSize().background(c.media)) {
            CardScanPreview(onResult = onResult, onFailed = onFailed, onPreparing = { preparing = it })
            // a card-shaped frame to hold the card in
            Box(
                Modifier.align(Alignment.Center).fillMaxWidth(0.86f).aspectRatio(1.586f)
                    .border(3.dp, c.onMedia.copy(alpha = 0.85f), RoundedCornerShape(16.dp)),
            )
            Column(
                Modifier.fillMaxWidth().background(c.media.copy(alpha = 0.6f)).safeDrawingPadding().padding(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onClose) { Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = c.onMedia) }
                    Text(stringResource(R.string.card_scan_title), color = c.onMedia, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                }
                Text(
                    stringResource(if (preparing) R.string.card_scan_preparing else R.string.card_scan_hint),
                    color = c.onMedia, style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(start = 12.dp, end = 12.dp, bottom = 8.dp),
                )
            }
        }
    }
}

@OptIn(ExperimentalGetImage::class)
@Composable
private fun CardScanPreview(onResult: (CardScanResult) -> Unit, onFailed: () -> Unit, onPreparing: (Boolean) -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val done = remember { AtomicBoolean(false) }
    val executor = remember { Executors.newSingleThreadExecutor() }
    val recognizer = remember { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }
    val agreement = remember { CardScanAgreement() }
    val providerFuture = remember { ProcessCameraProvider.getInstance(context) }
    val result by rememberUpdatedState(onResult)
    val failed by rememberUpdatedState(onFailed)
    val preparing by rememberUpdatedState(onPreparing)

    DisposableEffect(Unit) {
        onDispose {
            done.set(true)
            runCatching { providerFuture.get().unbindAll() }
            recognizer.close()
            executor.shutdown()
            agreement.clear()
        }
    }

    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            val view = PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
            providerFuture.addListener({
                try {
                    val provider = providerFuture.get()
                    val preview = Preview.Builder().build().also { it.surfaceProvider = view.surfaceProvider }
                    // card digits are small: a 720p frame reads them far better than the 480p default
                    val size = ResolutionSelector.Builder()
                        .setResolutionStrategy(ResolutionStrategy(Size(1280, 720), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                        .build()
                    val analysis = ImageAnalysis.Builder()
                        .setResolutionSelector(size)
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                    analysis.setAnalyzer(executor) { proxy ->
                        val media = proxy.image
                        if (media == null || done.get()) { proxy.close(); return@setAnalyzer }
                        recognizer.process(InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees))
                            .addOnSuccessListener { text ->
                                preparing(false)
                                if (done.get()) return@addOnSuccessListener
                                val lines = text.textBlocks.flatMap { b -> b.lines.map { it.text } }
                                val read = agreement.offer(CardScanText.read(lines)) ?: return@addOnSuccessListener
                                if (done.compareAndSet(false, true)) result(read)
                            }
                            .addOnFailureListener { e ->
                                // the recognizer is still being fetched by Play services: keep the camera open and try the next frames
                                if (e is MlKitException && e.errorCode == MlKitException.UNAVAILABLE) preparing(true)
                            }
                            .addOnCompleteListener { proxy.close() }
                    }
                    provider.unbindAll()
                    provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                } catch (e: Exception) {
                    failed()
                }
            }, ContextCompat.getMainExecutor(ctx))
            view
        },
    )
}

/** The shared [CardScanner] on this phone: the scan button above, and whether Google Pay's scan screen is up. */
class AndroidCardScanner : CardScanner {
    @Composable override fun Button(onResult: (CardScanResult) -> Unit, modifier: Modifier) = CardScanButton(onResult, modifier)
    override fun active(): Boolean = CardScanAway.active()
}
