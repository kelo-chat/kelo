package chat.kelo.app.ui

import android.view.HapticFeedbackConstants
import chat.kelo.app.shell.ChromeRules
import chat.kelo.app.shell.ShellChrome
import chat.kelo.app.ui.platform.Haptic
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

    @Test fun hapticsFallBackBeforeApi30() {
        assertEquals(HapticFeedbackConstants.CONFIRM, HapticMap.constant(Haptic.CONFIRM, 30))
        assertEquals(HapticFeedbackConstants.VIRTUAL_KEY, HapticMap.constant(Haptic.CONFIRM, 29))
        assertEquals(HapticFeedbackConstants.REJECT, HapticMap.constant(Haptic.REJECT, 34))
        assertEquals(HapticFeedbackConstants.LONG_PRESS, HapticMap.constant(Haptic.REJECT, 26))
        assertEquals(HapticFeedbackConstants.CLOCK_TICK, HapticMap.constant(Haptic.TICK, 26))
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
