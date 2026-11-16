package chat.kelo.app.ui

import androidx.compose.runtime.Composable
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.compose.LifecycleEventEffect
import chat.kelo.app.ui.platform.AppLifecycle

/** The activity's lifecycle as the shared pages see it (resume and stop, and whether it is in front). */
class AndroidAppLifecycle(private val owner: LifecycleOwner) : AppLifecycle {
    override val resumed: Boolean get() = owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
    override val started: Boolean get() = owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)

    @Composable override fun OnEvents(onResume: () -> Unit, onStop: () -> Unit) {
        LifecycleEventEffect(Lifecycle.Event.ON_RESUME, owner) { onResume() }
        LifecycleEventEffect(Lifecycle.Event.ON_STOP, owner) { onStop() }
    }
}
