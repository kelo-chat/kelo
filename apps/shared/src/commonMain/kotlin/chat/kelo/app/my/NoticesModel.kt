package chat.kelo.app.my

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.Languages
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/** One operator notice from GET /api/notices (unread only, already in the requested language). */
@Serializable
data class Notice(
    val id: String,
    val date: String = "",
    val level: String = NoticesRules.INFO,
    val title: String = "",
    val body: String = "",
    val link: String? = null,
)

@Serializable
internal data class NoticesAnswer(val notices: List<Notice> = emptyList())

/** The rules of the notices area: parsing the answer, which links open, and the language asked for. */
object NoticesRules {
    const val INFO = "info"
    const val IMPORTANT = "important"

    /** The only links the app opens (in the external browser): https pages on Kelo's own site. */
    const val LINK_PREFIX = "https://kelo.chat/"

    private val ID = Regex("[A-Za-z0-9._-]{1,128}")
    private val json = Json { ignoreUnknownKeys = true }

    /** The notices in an answer; a broken answer gives none, and a notice without a usable id is left out. */
    fun parse(text: String): List<Notice> = try {
        json.decodeFromString(NoticesAnswer.serializer(), text).notices.filter { ID.matches(it.id) }
    } catch (e: Exception) {
        emptyList()
    }

    fun isImportant(n: Notice): Boolean = n.level == IMPORTANT

    /** The link button's address, or null: only an https link on kelo.chat with no spaces is offered. */
    fun safeLink(link: String?): String? {
        val t = link?.trim() ?: return null
        return t.takeIf { it.startsWith(LINK_PREFIX) && t.length > LINK_PREFIX.length && t.none { c -> c.isWhitespace() } }
    }

    /** The language asked for: the saved pick, or the phone's language when the app follows the phone. */
    fun language(pick: String?, systemShown: String): String =
        if (pick == null || pick == Languages.SYSTEM) systemShown else pick

    /** The notices not read in this session (a server that still lists one after its read does not bring it back). */
    fun unread(list: List<Notice>, read: Set<String>): List<Notice> = list.filter { it.id !in read }
}

/** The notices area's state: the cards shown, and the notice open in the sheet (null when none). */
data class NoticesUiState(val notices: List<Notice> = emptyList(), val opened: Notice? = null)

/**
 * 마이 → 공지: the operator's notices. Loaded when My opens; a notice stays while unread and goes when it is opened (the card goes at
 * once and POST /api/notices/{id}/read tells the server). An older server (404) or a failed read shows no notices.
 */
class NoticesModel(private val sessions: Sessions, private val language: () -> String) : ViewModel() {
    private val _state = MutableStateFlow(NoticesUiState())
    val state: StateFlow<NoticesUiState> = _state.asStateFlow()

    /** Read in this session: never shown again here, even while the server's list still has them. */
    private val read = HashSet<String>()

    fun load() {
        val api = sessions.api.value ?: return
        val lang = language()
        viewModelScope.launch {
            val found = try {
                NoticesRules.parse(api.request("GET", "/api/notices?lang=$lang").toString())
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                emptyList()   // an older server (404), no answer: nothing shows
            }
            _state.update { it.copy(notices = NoticesRules.unread(found, read)) }
        }
    }

    /** The notice was opened: it is read, its card goes now and the server is told. */
    fun open(notice: Notice) {
        read += notice.id
        _state.update { s -> s.copy(notices = s.notices.filter { it.id != notice.id }, opened = notice) }
        val api = sessions.api.value ?: return
        viewModelScope.launch {
            try {
                api.post<JsonElement>("/api/notices/${notice.id}/read")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // the server keeps it unread; it stays hidden in this session and may come back next time
            }
        }
    }

    fun close() = _state.update { it.copy(opened = null) }
}
