package chat.kelo.desktop.vnc

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import java.io.ByteArrayOutputStream

/** A scripted RFB server for the session tests: the bytes it sends and what the client sent back. */
internal class Recorder : RfbEvents {
    var updates = 0
    val failures = ArrayList<String>()
    override fun updated() { updates++ }
    override fun failed(why: String) { failures += why }
}

internal class Run(val sent: ByteArray, val failures: List<String>, val updates: Int, val frame: FrameBuffer)

/** Feeds the server's bytes in [chunks], then ends the socket; returns what the client sent. */
internal fun run(chunks: List<ByteArray>, frame: FrameBuffer = FrameBuffer(1, 1)): Run = runBlocking {
    val reader = RfbByteReader()
    val out = ByteArrayOutputStream()
    val rec = Recorder()
    val session = RfbSession(reader, { out.write(it) }, frame, rec)
    val job = launch(Dispatchers.Default) { session.run() }
    chunks.forEach { reader.feed(it) }
    reader.end()
    job.join()
    Run(out.toByteArray(), rec.failures, rec.updates, frame)
}

internal fun cat(vararg parts: ByteArray): ByteArray {
    val out = ByteArrayOutputStream()
    parts.forEach { out.write(it) }
    return out.toByteArray()
}

internal fun ints(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }
internal fun ascii(s: String) = s.toByteArray(Charsets.US_ASCII)

/** RFB 3.8 greeting, one security type (None), SecurityResult OK, and a ServerInit of [w] x [h] with a one-letter name. */
internal fun serverStart(w: Int = 64, h: Int = 48): ByteArray = cat(
    ascii("RFB 003.008\n"),
    ints(1, 1),                       // one security type: None
    ints(0, 0, 0, 0),                 // SecurityResult OK
    ints(w shr 8, w and 0xff, h shr 8, h and 0xff),
    ints(32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0),   // the server's own pixel format (not used)
    ints(0, 0, 0, 1), ascii("k"),
)

/** What the client sends after the handshake for a [w] x [h] screen. */
internal fun clientStart(w: Int = 64, h: Int = 48): ByteArray = cat(
    ascii("RFB 003.008\n"),
    ints(1),                          // the security type chosen: None
    ints(1),                          // ClientInit: shared
    RfbClientMessage.setPixelFormat(),
    RfbClientMessage.setEncodings(RfbSession.ENCODINGS),
    RfbClientMessage.updateRequest(false, 0, 0, w, h),
)
