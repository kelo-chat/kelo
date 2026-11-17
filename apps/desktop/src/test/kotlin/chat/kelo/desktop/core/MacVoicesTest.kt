package chat.kelo.desktop.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MacVoicesTest {
    private val listing = """
        Alex                en_US    # Most people recognize me by my voice.
        Daniel              en_GB    # Hello, my name is Daniel.
        Eddy (한국어(대한민국)) ko_KR    # 안녕하세요, 제 이름은 Eddy입니다.
        Yuna                ko_KR    # 안녕하세요, 제 이름은 유나입니다.
        Amelie              fr_CA    # Bonjour, je m’appelle Amélie.
        Thomas              fr_FR    # Bonjour, je m’appelle Thomas.
        Luciana             pt_BR    # Olá, meu nome é Luciana.
        Kyoko               ja_JP    # こんにちは、私の名前はKyokoです。
    """.trimIndent()

    @Test fun `the first voice of the language`() {
        assertEquals("Eddy (한국어(대한민국))", MacVoices.pick(listing, "ko"))
        assertEquals("Kyoko", MacVoices.pick(listing, "ja"))
    }

    @Test fun `the same region wins when there is one`() {
        assertEquals("Daniel", MacVoices.pick(listing, "en-GB"))
        assertEquals("Thomas", MacVoices.pick(listing, "fr-FR"))
        assertEquals("Amelie", MacVoices.pick(listing, "fr"))
    }

    @Test fun `a region the voices lack falls back to the language`() = assertEquals("Alex", MacVoices.pick(listing, "en-AU"))

    @Test fun `no voice for the language gives none`() {
        assertNull(MacVoices.pick(listing, "th"))
        assertNull(MacVoices.pick("", "en"))
    }
}
