package chat.kelo.app.device

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.KeloTheme

/**
 * What Health Connect shows when the user asks why Kelo wants their health data (Android 13 and older: the rationale action; 14 and
 * newer: the VIEW_PERMISSION_USAGE alias in the manifest): how Kelo uses it, and the privacy policy (the same page as on Google Play).
 */
class HealthRationaleActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            KeloTheme {
                val c = Kelo.colors
                Column(
                    Modifier.fillMaxSize().background(c.bg).safeDrawingPadding().verticalScroll(rememberScrollState()).padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    Text(stringResource(R.string.device_health_rationale_title), style = MaterialTheme.typography.headlineSmall, color = c.ink)
                    Text(stringResource(R.string.device_health_rationale), style = MaterialTheme.typography.bodyLarge, color = c.ink)
                    SecondaryButton(stringResource(R.string.device_privacy_policy), onClick = {
                        runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(PRIVACY_URL))) }
                    })
                }
            }
        }
    }

    companion object {
        const val PRIVACY_URL = "https://kelo.chat/privacy"
    }
}
