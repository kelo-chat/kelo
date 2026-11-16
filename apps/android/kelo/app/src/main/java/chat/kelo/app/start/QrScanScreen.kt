package chat.kelo.app.start

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import chat.kelo.app.ui.theme.Kelo
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import chat.kelo.app.R
import chat.kelo.app.ui.components.CenterCard
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Camera QR scanner (CameraX + ML Kit, the barcode model is bundled so it works without Play services).
 * Calls [onScanned] once with the first QR text [accept] takes; other QR codes only show a hint.
 */
@Composable
fun QrScanScreen(onClose: () -> Unit, accept: (String) -> Boolean, onScanned: (String) -> Unit) {
    val context = LocalContext.current
    fun granted() = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
    var hasPermission by remember { mutableStateOf(granted()) }
    var asked by rememberSaveable { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        hasPermission = it
        asked = true
    }
    // coming back from the system settings with the permission given
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) hasPermission = granted() }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }
    LaunchedEffect(Unit) { if (!hasPermission && !asked) launcher.launch(Manifest.permission.CAMERA) }

    if (!hasPermission) {
        CenterCard {
            Text(stringResource(R.string.scan_title), style = MaterialTheme.typography.headlineMedium)
            MutedText(stringResource(R.string.scan_permission_needed))
            PrimaryButton(stringResource(R.string.scan_permission_allow), onClick = { launcher.launch(Manifest.permission.CAMERA) })
            if (asked) {
                SecondaryButton(stringResource(R.string.scan_open_settings), modifier = Modifier.fillMaxWidth(), onClick = {
                    context.startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                    )
                })
            }
            SecondaryButton(stringResource(R.string.close), onClick = onClose, modifier = Modifier.fillMaxWidth())
        }
        return
    }

    var wrongCode by remember { mutableStateOf(false) }
    var cameraFailed by remember { mutableStateOf(false) }
    val currentAccept by rememberUpdatedState(accept)
    val currentOnScanned by rememberUpdatedState(onScanned)

    Box(Modifier.fillMaxSize().background(Kelo.colors.media)) {
        if (!cameraFailed) {
            CameraPreview(
                onCode = { raw ->
                    if (currentAccept(raw)) { currentOnScanned(raw); true } else { wrongCode = true; false }
                },
                onFailed = { cameraFailed = true },
            )
        }
        // the aiming square
        Box(
            Modifier
                .align(Alignment.Center)
                .size(240.dp)
                .border(3.dp, Kelo.colors.onMedia.copy(alpha = 0.85f), RoundedCornerShape(24.dp)),
        )
        Column(Modifier.fillMaxWidth().safeDrawingPadding()) {
            Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onClose) {
                    Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = Kelo.colors.onMedia)
                }
                Text(stringResource(R.string.scan_title), color = Kelo.colors.onMedia, style = MaterialTheme.typography.titleMedium)
            }
        }
        Column(
            Modifier.align(Alignment.BottomCenter).fillMaxWidth().safeDrawingPadding().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            val text = when {
                cameraFailed -> R.string.scan_camera_failed
                wrongCode -> R.string.scan_not_kelo
                else -> R.string.scan_hint
            }
            Text(stringResource(text), color = Kelo.colors.onMedia, style = MaterialTheme.typography.bodyLarge)
        }
    }
}

/** The camera preview with a QR analyser. [onCode] returns true to stop scanning. */
@OptIn(ExperimentalGetImage::class)
@Composable
private fun CameraPreview(onCode: (String) -> Boolean, onFailed: () -> Unit) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val done = remember { AtomicBoolean(false) }
    val executor: ExecutorService = remember { Executors.newSingleThreadExecutor() }
    val scanner: BarcodeScanner = remember {
        BarcodeScanning.getClient(BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
    }
    val providerFuture = remember { ProcessCameraProvider.getInstance(context) }

    DisposableEffect(Unit) {
        onDispose {
            runCatching { providerFuture.get().unbindAll() }
            scanner.close()
            executor.shutdown()
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
                    val analysis = ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                    analysis.setAnalyzer(executor) { proxy ->
                        val media = proxy.image
                        if (media == null || done.get()) {
                            proxy.close(); return@setAnalyzer
                        }
                        val image = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
                        scanner.process(image)
                            .addOnSuccessListener { codes ->
                                val raw = codes.firstNotNullOfOrNull { it.rawValue } ?: return@addOnSuccessListener
                                if (!done.get() && onCode(raw)) done.set(true)
                            }
                            .addOnCompleteListener { proxy.close() }
                    }
                    provider.unbindAll()
                    provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                } catch (e: Exception) {
                    onFailed()
                }
            }, ContextCompat.getMainExecutor(ctx))
            view
        },
    )
}
