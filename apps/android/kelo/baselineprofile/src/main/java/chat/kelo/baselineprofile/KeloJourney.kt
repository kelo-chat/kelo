package chat.kelo.baselineprofile

import androidx.benchmark.macro.MacrobenchmarkScope
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.UiObject2
import androidx.test.uiautomator.Until
import java.util.regex.Pattern

/**
 * The journey the profile is made from, as a person uses the app: open it, the chat (scroll), the connectors (scroll), My (scroll,
 * open a sub page, back). Labels are the app's Korean strings (res/values/strings.xml); tabs are found by their content
 * description. On a device that is not signed in only the start-up (the connect screen) is covered.
 */
internal object KeloJourney {
    const val PACKAGE = "chat.kelo.app"
    private const val WAIT_MS = 5_000L

    private val TAB_CHAT = Pattern.compile("채팅")
    private val TAB_CONNECTORS = Pattern.compile("커넥터")
    private val TAB_MY = Pattern.compile("마이")
    private const val MY_DEVICES = "내 기기"

    fun MacrobenchmarkScope.launch() {
        pressHome()
        startActivityAndWait()
    }

    /** True when the signed-in app (its tab bar) showed. */
    fun MacrobenchmarkScope.signedIn(): Boolean = device.wait(Until.hasObject(By.desc(TAB_CHAT)), WAIT_MS) == true

    fun MacrobenchmarkScope.browse() {
        // the chat: older messages above
        scrollBothWays()
        tab(TAB_CONNECTORS)
        device.wait(Until.hasObject(By.text("커넥터")), WAIT_MS)
        scrollBothWays()
        tab(TAB_MY)
        device.wait(Until.hasObject(By.text(MY_DEVICES)), WAIT_MS)
        scrollBothWays()
        // a sub page slides in, and back out
        findScrolledTo(By.text(MY_DEVICES))?.click()
        device.waitForIdle()
        device.wait(Until.gone(By.desc(TAB_MY)), WAIT_MS)
        device.pressBack()
        device.wait(Until.hasObject(By.desc(TAB_MY)), WAIT_MS)
        device.waitForIdle()
        tab(TAB_CHAT)
        device.waitForIdle()
    }

    /** Taps a tab; if the tab bar is not there (a detail or sub page is open), steps back once first. */
    private fun MacrobenchmarkScope.tab(label: Pattern) {
        val t = device.wait(Until.findObject(By.desc(label)), WAIT_MS) ?: run {
            device.pressBack()
            device.wait(Until.findObject(By.desc(label)), WAIT_MS)
        }
        t?.click()
        device.waitForIdle()
    }

    /**
     * Quick swipes in the middle of the screen (never from an edge: a swipe from the top opens the notification shade, one from
     * the bottom lands on the tab bar). Few steps make them flings.
     */
    private fun MacrobenchmarkScope.swipe(up: Boolean) {
        val x = device.displayWidth / 2
        val low = (device.displayHeight * 0.72).toInt()
        val high = (device.displayHeight * 0.32).toInt()
        if (up) device.swipe(x, low, x, high, FLING_STEPS) else device.swipe(x, high, x, low, FLING_STEPS)
        device.waitForIdle()
    }

    private fun MacrobenchmarkScope.scrollBothWays() {
        repeat(2) { swipe(up = true) }
        repeat(2) { swipe(up = false) }
    }

    private fun MacrobenchmarkScope.findScrolledTo(selector: BySelector): UiObject2? {
        // found and clear of the tab bar at the bottom
        fun usable(o: UiObject2?) = o?.takeIf { it.visibleCenter.y < device.displayHeight * 0.75 }
        repeat(6) {
            usable(device.findObject(selector))?.let { return it }
            swipe(up = true)
        }
        return usable(device.findObject(selector))
    }

    private const val FLING_STEPS = 8
}
