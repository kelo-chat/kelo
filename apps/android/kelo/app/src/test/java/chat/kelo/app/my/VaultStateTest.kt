package chat.kelo.app.my

import chat.kelo.app.core.KeloApi
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** GET /api/vault: "Use codes from my mail automatically" reads as on unless the server says it is off (older servers send nothing). */
class VaultStateTest {
    @Test fun `codes from mail are on by default and off when the server says so`() {
        assertTrue(KeloApi.json.decodeFromString(VaultState.serializer(), """{"locked": false, "items": []}""").mailCodesAuto)
        assertFalse(KeloApi.json.decodeFromString(VaultState.serializer(), """{"locked": false, "mail_codes_auto": false}""").mailCodesAuto)
    }
}
