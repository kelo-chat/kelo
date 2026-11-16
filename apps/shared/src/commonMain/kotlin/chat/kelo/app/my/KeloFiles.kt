package chat.kelo.app.my

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One row of GET /api/files?folders=1 (the agent's own files; api/files.py FILE MANAGER). */
@Serializable
data class HomeEntry(val path: String, val type: String = "file", val bytes: Long? = null, val writable: Boolean = false) {
    val isDir: Boolean get() = type == "dir"
    val name: String get() = path.trimEnd('/').substringAfterLast('/')
}

/** A file in the trash (GET /api/home-trash); kept until [purgeAt] (unix seconds). */
@Serializable
data class HomeTrashed(
    val id: String, val path: String, val bytes: Long? = null, @SerialName("deleted_at") val deletedAt: Double = 0.0,
    val type: String = "file", @SerialName("purge_at") val purgeAt: Double = 0.0,
)

@Serializable data class HomeFile(val path: String, val content: String = "")

/** One row of GET /api/skills; only the custom ones (source "custom", written on this server) are shown in Kelo's files. [state]
 *  "held" = not approved as it is now (shown as "Waiting"). */
@Serializable
data class CustomSkillRow(
    val id: String, val name: String = "", val description: String = "", val source: String = "", val state: String = "ready",
    @SerialName("created_at") val createdAt: Double? = null,
) {
    val held: Boolean get() = state == "held"
    val title: String get() = name.ifBlank { id }
}

@Serializable data class CustomSkillList(val items: List<CustomSkillRow> = emptyList())

/** GET /api/skills/custom/{id}: the skill's text without its front matter. */
@Serializable
data class CustomSkill(
    val id: String, val name: String = "", val description: String = "", val text: String = "", val state: String = "ready",
    @SerialName("created_at") val createdAt: Double? = null,
) {
    val title: String get() = name.ifBlank { id }
}

/** The custom skills for the list: newest first by created_at when the rows have it, else by name. */
fun customSkillsForList(items: List<CustomSkillRow>): List<CustomSkillRow> {
    val custom = items.filter { it.source == "custom" }
    return if (custom.all { it.createdAt != null }) custom.sortedByDescending { it.createdAt }
    else custom.sortedBy { it.title.lowercase() }
}

object KeloFileRules {
    /** The files Kelo always keeps (deleting one = "back to the start"; the server answers 409 reset). */
    val CORE = listOf("assistant.md", "user.md", "MEMORY.md", "AGENTS.md", "CHECKLIST.md", "NOTIFY_PREFS.md")
    /** Folders where the owner may add folders and delete files. */
    val OPEN_FOLDERS = listOf("memory", "notes")

    fun isCore(path: String) = path in CORE
    /** Delete (to the trash) is offered for files and folders inside memory/ or notes/, not for those two folders themselves. */
    fun canDelete(path: String) = OPEN_FOLDERS.any { path.startsWith("$it/") }
    /** A new folder can be made here ("" = the top: no). */
    fun canMakeFolder(folder: String) = OPEN_FOLDERS.any { folder == it || folder.startsWith("$it/") }

    /** The rows directly inside [folder] ("" = the top): folders first, then core files in [CORE] order, then the rest by name. */
    fun children(all: List<HomeEntry>, folder: String): List<HomeEntry> {
        val prefix = if (folder.isEmpty()) "" else "$folder/"
        val direct = all.filter { e -> e.path.startsWith(prefix) && e.path.length > prefix.length && '/' !in e.path.removePrefix(prefix).trimEnd('/') }
        return direct.sortedWith(compareBy<HomeEntry>({ !it.isDir }, { CORE.indexOf(it.path).let { i -> if (i < 0) Int.MAX_VALUE else i } }, { it.name.lowercase() }))
    }

    /** The API path for a home path: every part through [segment], joined with "/". */
    fun apiPath(path: String, segment: (String) -> String) = path.split('/').filter { it.isNotEmpty() }.joinToString("/") { segment(it) }

    /** A new folder's name: one part, no slash, not hidden, not empty (trimmed). Null when it cannot be used. */
    fun folderName(raw: String): String? = raw.trim().takeIf { it.isNotEmpty() && '/' !in it && '\\' !in it && !it.startsWith(".") && it.length <= 80 }
}
