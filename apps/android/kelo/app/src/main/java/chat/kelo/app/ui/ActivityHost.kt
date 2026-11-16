package chat.kelo.app.ui

import androidx.fragment.app.FragmentActivity

/**
 * The activity that is on screen, for the device services that need one (pickers, permission questions, the owner's check).
 * [chat.kelo.app.MainActivity] sets it in onCreate and clears it in onDestroy; the services read it only when asked to act.
 */
class ActivityHost {
    @Volatile var current: FragmentActivity? = null
}
