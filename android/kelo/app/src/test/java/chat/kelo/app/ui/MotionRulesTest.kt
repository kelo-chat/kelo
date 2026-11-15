package chat.kelo.app.ui

import android.view.HapticFeedbackConstants
import chat.kelo.app.shell.ChromeRules
import chat.kelo.app.shell.ShellChrome
import chat.kelo.app.ui.components.SkeletonTiming
import chat.kelo.app.ui.theme.Motion
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MotionRulesTest {
    @Test fun pushOrPopByDepth() {
        assertEquals(PushDirection.PUSH, PushRules.direction(0, 1))
        assertEquals(PushDirection.POP, PushRules.direction(2, 1))
        assertEquals(PushDirection.NONE, PushRules.direction(1, 1))
    }

    @Test fun animationsOffOnlyAtZeroScale() {
        assertTrue(Motion.isOff(0f))
        assertFalse(Motion.isOff(0.5f))
        assertFalse(Motion.isOff(1f))
    }

    @Test fun theCurvesAreMaterial3() {
        // emphasized decelerate: most of the way there early (arrivals)
        assertEquals(0f, Motion.EmphasizedDecelerate.transform(0f), 0.001f)
        assertEquals(1f, Motion.EmphasizedDecelerate.transform(1f), 0.001f)
        assertTrue(Motion.EmphasizedDecelerate.transform(0.3f) > 0.75f)
        // emphasized accelerate: slow to leave, then gone (exits)
        assertTrue(Motion.EmphasizedAccelerate.transform(0.5f) < 0.3f)
        assertEquals(1f, Motion.EmphasizedAccelerate.transform(1f), 0.001f)
        // standard sits between the two
        val mid = Motion.Standard.transform(0.5f)
        assertTrue(mid > Motion.EmphasizedAccelerate.transform(0.5f) && mid < Motion.EmphasizedDecelerate.transform(0.5f))
    }

    @Test fun durationsFollowTheMaterialScale() {
        assertEquals(400, Motion.ENTER_MS)       // medium 4
        assertEquals(200, Motion.EXIT_MS)        // short 4
        assertEquals(300, Motion.STANDARD_MS)    // medium 2
        assertEquals(150, Motion.SMALL_MS)       // short 3
        assertEquals(100, Motion.PRESS_MS)       // short 2
        assertTrue(Motion.EXIT_MS < Motion.ENTER_MS)
        assertEquals(0.96f, Motion.PRESS_SHRINK, 0.0001f)
        assertEquals(1200, SkeletonTiming.SWEEP_MS)
    }

    @Test fun hapticsFallBackBeforeApi30() {
        assertEquals(HapticFeedbackConstants.CONFIRM, HapticMap.constant(Haptic.CONFIRM, 30))
        assertEquals(HapticFeedbackConstants.VIRTUAL_KEY, HapticMap.constant(Haptic.CONFIRM, 29))
        assertEquals(HapticFeedbackConstants.REJECT, HapticMap.constant(Haptic.REJECT, 34))
        assertEquals(HapticFeedbackConstants.LONG_PRESS, HapticMap.constant(Haptic.REJECT, 26))
        assertEquals(HapticFeedbackConstants.CLOCK_TICK, HapticMap.constant(Haptic.TICK, 26))
    }

    @Test fun skeletonStaysAtLeastItsMinimum() {
        assertEquals(500L, SkeletonTiming.remaining(1000, 1000))
        assertEquals(400L, SkeletonTiming.remaining(1000, 1100))
        assertEquals(0L, SkeletonTiming.remaining(1000, 1600))
    }

    @Test fun theKeyboardHidesOnlyTheTabBar() {
        assertFalse(ChromeRules.headerHidden(setOf(ShellChrome.KEY_KEYBOARD)))
        assertTrue(ChromeRules.barHidden(setOf(ShellChrome.KEY_KEYBOARD)))
        assertTrue(ChromeRules.headerHidden(setOf("my-sub")))
        assertTrue(ChromeRules.barHidden(setOf("my-sub", ShellChrome.KEY_KEYBOARD)))
        assertFalse(ChromeRules.barHidden(emptySet()))
    }

    @Test fun theVaultAsksUnlessThereIsNoLock() {
        val success = androidx.biometric.BiometricManager.BIOMETRIC_SUCCESS
        val none = androidx.biometric.BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED
        assertEquals(GateDecision.PROMPT, VaultGateRules.decide(success, deviceSecure = false))
        assertEquals(GateDecision.PROMPT, VaultGateRules.decide(none, deviceSecure = true))
        assertEquals(GateDecision.OPEN_NO_LOCK, VaultGateRules.decide(none, deviceSecure = false))
        assertTrue(VaultGateRules.isCancel(androidx.biometric.BiometricPrompt.ERROR_USER_CANCELED))
        assertFalse(VaultGateRules.meansNoLock(androidx.biometric.BiometricPrompt.ERROR_LOCKOUT))
    }

    @Test fun secureWindowIsCounted() {
        SecureWindowCount.reset()
        assertTrue(SecureWindowCount.enter())
        assertTrue(SecureWindowCount.enter())
        assertTrue(SecureWindowCount.leave())
        assertFalse(SecureWindowCount.leave())
        assertFalse(SecureWindowCount.leave())   // never below zero
        assertTrue(SecureWindowCount.enter())
        SecureWindowCount.reset()
    }
}
