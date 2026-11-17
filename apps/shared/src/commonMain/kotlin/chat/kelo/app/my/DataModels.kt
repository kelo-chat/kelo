package chat.kelo.app.my

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.platform.PickedFile
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** GET /api/data/export: the background export. [state]: idle, running, done, failed. [download] is the library path of the ZIP when done. */
@Serializable
data class DataExport(
    val state: String = "idle",
    val bytes: Long = 0,
    val error: String = "",
    val file: String = "",
    val download: String = "",
)

/** One thing the import added to memory. */
@Serializable
data class ImportedFact(val id: String = "", val text: String = "", val kind: String = "")

/** GET /api/data/import: the background import. [state]: idle, running, done, failed, cancelled. [total] counts pieces of text read. */
@Serializable
data class DataImport(
    val state: String = "idle",
    val assistant: String = "",
    val total: Int = 0,
    val done: Int = 0,
    val failed: Int = 0,
    val skipped: Int = 0,
    val added: List<ImportedFact> = emptyList(),
    val error: String = "",
)

/** GET /api/data: both statuses and the word the owner types before a reset. */
@Serializable
data class DataOverview(
    @SerialName("confirm_word") val confirmWord: String = "reset",
    val export: DataExport = DataExport(),
    @SerialName("import") val importStatus: DataImport = DataImport(),
)

@Serializable
data class DataPrompt(val prompt: String = "")

@Serializable
data class ResetBackup(val name: String = "", val path: String = "", val bytes: Long = 0)

/** POST /api/data/reset: where the copy was saved first. */
@Serializable
data class ResetResult(val ok: Boolean = false, val backup: ResetBackup = ResetBackup(), @SerialName("signed_out") val signedOut: Int = 0)

/** What the data page decides, away from the screen so it can be tested. */
object DataRules {
    const val ZIP_TYPE = "application/zip"
    /** The biggest ZIP the server reads (100 MB); a bigger one is not sent. */
    const val MAX_ZIP: Long = 100L * 1024 * 1024

    fun running(state: String): Boolean = state == "running"

    /** The export file can be saved: done and the server named the download. */
    fun exportReady(e: DataExport): Boolean = e.state == "done" && e.download.isNotEmpty()

    /** The word was typed (any case, spaces around ignored), as the server compares it. */
    fun typedWord(typed: String, word: String): Boolean = typed.trim().equals(word.trim(), ignoreCase = true) && word.isNotBlank()

    /** 0..100 for the import's progress bar; 0 until the first piece is counted. */
    fun percent(i: DataImport): Int = if (i.total <= 0) 0 else (i.done * 100 / i.total).coerceIn(0, 100)

    /** The import finished with something to show (done, even when nothing new was found). */
    fun importFinished(i: DataImport): Boolean = i.state == "done" || i.state == "cancelled"

    /** A name for the saved file, from the library name. */
    fun saveName(e: DataExport): String = e.file.ifEmpty { "kelo-data.zip" }

    /** The picked file is small enough for the server (it checks again). */
    fun zipFits(file: PickedFile): Boolean = file.size <= MAX_ZIP
}

/** The data routes (data_controls.py, data_import.py). */
object DataCalls {
    suspend fun overview(api: KeloApi): DataOverview = api.get("/api/data")

    suspend fun startExport(api: KeloApi): DataExport = api.post("/api/data/export")
    suspend fun exportStatus(api: KeloApi): DataExport = api.get("/api/data/export")

    suspend fun prompt(api: KeloApi): String = api.get<DataPrompt>("/api/data/import/prompt").prompt

    suspend fun importText(api: KeloApi, text: String, assistant: String = ""): DataImport =
        api.post("/api/data/import/text", buildJsonObject { put("text", text); put("assistant", assistant) })

    /** The ZIP goes up as one request, read as it is sent. */
    suspend fun importFile(api: KeloApi, file: PickedFile): DataImport {
        val answer: JsonElement = api.putStream("/api/data/import/file", file.size, DataRules.ZIP_TYPE, file.open)
        return KeloApi.decode(answer)
    }

    suspend fun importStatus(api: KeloApi): DataImport = api.get("/api/data/import")
    suspend fun cancelImport(api: KeloApi): DataImport = api.delete("/api/data/import")

    suspend fun reset(api: KeloApi, word: String, signOut: Boolean): ResetResult =
        api.post("/api/data/reset", buildJsonObject { put("confirm", word); put("sign_out_connectors", signOut) })

    /** A wrong word is a 400: the page keeps the field and says so. */
    fun wrongWord(e: ApiError): Boolean = e.status == 400
}
