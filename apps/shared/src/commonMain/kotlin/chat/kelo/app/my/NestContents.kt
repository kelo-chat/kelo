package chat.kelo.app.my

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * What a package installs, as the server lists it (server/src/assistant/nest_routes.py, module docstring): the preview card's
 * "contents" and an update's "changed". Older servers send neither; every field has a default, so a missing one reads as empty.
 */

@Serializable data class NestFile(val path: String = "", val bytes: Long = 0, val sha256: String = "")

@Serializable
data class NestContents(
    val files: List<NestFile> = emptyList(),
    @SerialName("total_bytes") val totalBytes: Long = 0,
    /** The full text of every .md file and the manifest, by path. */
    val texts: Map<String, String> = emptyMap(),
    /** The paths in [texts] that were cut at 64 KB (their text ends with an ellipsis). */
    val cut: List<String> = emptyList(),
    /** Every host and URL the package can reach, sorted. */
    val endpoints: List<String> = emptyList(),
    /** Notes from loading the package. */
    val warnings: List<String> = emptyList(),
)

/** An update's view of what differs from the installed files. */
@Serializable
data class NestChanged(
    val added: List<String> = emptyList(),
    val removed: List<String> = emptyList(),
    val modified: List<String> = emptyList(),
)

sealed class NestSize {
    data class Bytes(val count: Long) : NestSize()
    data class Kilobytes(val count: Long) : NestSize()
}

object NestContentsRules {
    /** The number on an update row: added + removed + modified. */
    fun changedCount(c: NestChanged): Int = c.added.size + c.removed.size + c.modified.size

    /** Bytes under 1 KB, else whole kilobytes rounded up (a file is never shown as 0 KB). */
    fun size(bytes: Long): NestSize =
        if (bytes < 1024) NestSize.Bytes(bytes) else NestSize.Kilobytes((bytes + 1023) / 1024)

    fun isCut(c: NestContents, path: String): Boolean = path in c.cut

    /** The "see everything" row is shown only when there is something to show (an older server sends no contents). */
    fun hasContents(c: NestContents): Boolean = c.files.isNotEmpty() || c.endpoints.isNotEmpty() || c.warnings.isNotEmpty()
}
