package chat.kelo.app.my

import org.junit.Assert.assertEquals
import org.junit.Test

class KeyRulesTest {
    @Test fun aKeyInTheAppHidesTheField() {
        assertEquals(KeyRules.State.IN_APP, KeyRules.state("app"))
        assertEquals(KeyRules.State.ENV, KeyRules.state("env"))
        assertEquals(KeyRules.State.NONE, KeyRules.state(null))
        assertEquals(KeyRules.State.NONE, KeyRules.state(""))
    }
}
