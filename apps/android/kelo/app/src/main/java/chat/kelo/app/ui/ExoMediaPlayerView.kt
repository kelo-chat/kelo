package chat.kelo.app.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import chat.kelo.app.core.KeloApi
import chat.kelo.app.platform.MediaPlayerView

/**
 * Plays a video or sound file in My documents with Media3 ExoPlayer, streamed from the server with the user's token over the
 * session's own HTTP client (so Range requests work and the end-to-end layer applies). Paused when the app goes to the background.
 */
@OptIn(UnstableApi::class)
class ExoMediaPlayerView : MediaPlayerView {
    @Composable
    override fun Show(api: KeloApi, path: String, audio: Boolean, modifier: Modifier, onFailed: () -> Unit) {
        val context = LocalContext.current
        val player = remember(path) {
            val http = OkHttpDataSource.Factory(api.client).apply {
                if (api.token.isNotEmpty()) setDefaultRequestProperties(mapOf("Authorization" to "Bearer ${api.token}"))
            }
            ExoPlayer.Builder(context).setMediaSourceFactory(DefaultMediaSourceFactory(context).setDataSourceFactory(http)).build().apply {
                setMediaItem(MediaItem.fromUri(api.url(path)))
                prepare()
                playWhenReady = true
            }
        }
        DisposableEffect(player) {
            val listener = object : Player.Listener {
                override fun onPlayerError(error: PlaybackException) { onFailed() }
            }
            player.addListener(listener)
            onDispose { player.removeListener(listener); player.release() }
        }
        val owner = LocalLifecycleOwner.current
        DisposableEffect(owner, player) {
            val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_PAUSE) player.pause() }
            owner.lifecycle.addObserver(obs)
            onDispose { owner.lifecycle.removeObserver(obs) }
        }
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    this.player = player
                    setShowNextButton(false)
                    setShowPreviousButton(false)
                    if (audio) {
                        controllerShowTimeoutMs = 0
                        controllerHideOnTouch = false
                        setShutterBackgroundColor(android.graphics.Color.TRANSPARENT)
                        defaultArtwork = null
                    }
                }
            },
            modifier = modifier,
        )
    }
}
