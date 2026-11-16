package chat.kelo.app.my

import org.junit.Assert.assertEquals
import org.junit.Test

class DocumentMediaTest {
    @Test
    fun kindsFollowTheServer() {
        assertEquals(listOf("image", "video", "audio", "document"), listOf("a.HEIC", "b.MOV", "c.m4a", "d.pdf").map(MediaKind::of))
    }

    @Test
    fun aPickWithoutSuffixGetsOneFromItsType() {
        assertEquals("IMG_0042.mp4", MediaKind.withSuffix("IMG_0042", "video/mp4"))
        assertEquals("song.mp3", MediaKind.withSuffix("song.mp3", "audio/mpeg"))
        assertEquals("odd", MediaKind.withSuffix("odd", "application/x-unknown"))
    }

    @Test
    fun clipLengthsReadLikeAPlayer() {
        assertEquals("0:00", clipLength(0.4))
        assertEquals("1:05", clipLength(65.4))
        assertEquals("1:02:03", clipLength(3723.0))
    }
}
