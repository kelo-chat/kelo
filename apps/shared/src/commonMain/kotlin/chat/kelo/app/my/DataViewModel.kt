package chat.kelo.app.my

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.platform.FilePicker
import chat.kelo.app.res.Res
import chat.kelo.app.res.connect_failed
import chat.kelo.app.res.data_export_saved
import chat.kelo.app.res.data_zip_too_big
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.jetbrains.compose.resources.StringResource

/** [overview] null until the first load; [failed]: it could not be read (a loaded page stays shown). */
data class DataUiState(
    val overview: DataOverview? = null,
    val failed: Boolean = false,
    val prompt: String = "",
    val pasted: String = "",
    val starting: Boolean = false,
    val saving: Boolean = false,
    val typed: String = "",
    val signOut: Boolean = false,
    val resetting: Boolean = false,
    val wrongWord: Boolean = false,
    val resetDone: ResetResult? = null,
) {
    val export: DataExport get() = overview?.export ?: DataExport()
    val import: DataImport get() = overview?.importStatus ?: DataImport()
    val word: String get() = overview?.confirmWord ?: "reset"
}

/** 마이 → Your data: the export, the import of another assistant's memories, and start over. */
class DataViewModel(
    private val sessions: Sessions, private val files: FilePicker, private val cancelNotifications: () -> Unit = {}, private val poll: Long = 1500L,
) : ViewModel() {
    private val _state = MutableStateFlow(DataUiState())
    val state: StateFlow<DataUiState> = _state.asStateFlow()
    private val _messages = MutableSharedFlow<StringResource>(extraBufferCapacity = 4)
    val messages: SharedFlow<StringResource> = _messages.asSharedFlow()
    private var watching: Job? = null

    fun load() {
        viewModelScope.launch {
            try {
                val api = sessions.requireApi()
                val o = DataCalls.overview(api)
                val prompt = _state.value.prompt.ifEmpty { runCatching { DataCalls.prompt(api) }.getOrDefault("") }
                _state.update { it.copy(overview = o, failed = false, prompt = prompt) }
                watch()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(failed = true) }
            }
        }
    }

    /** While the export or the import runs, look again every few seconds; stops by itself when both are over. */
    private fun watch() {
        if (watching?.isActive == true) return
        if (!DataRules.running(_state.value.export.state) && !DataRules.running(_state.value.import.state)) return
        watching = viewModelScope.launch(Dispatchers.Default) {   // real time: a test dispatcher would skip the waits
            while (DataRules.running(_state.value.export.state) || DataRules.running(_state.value.import.state)) {
                delay(poll)
                try {
                    val api = sessions.requireApi()
                    val exp = DataCalls.exportStatus(api)
                    val imp = DataCalls.importStatus(api)
                    _state.update { s -> s.copy(overview = (s.overview ?: DataOverview()).copy(export = exp, importStatus = imp)) }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    return@launch   // a page that cannot reach the server stops asking; leaving and coming back loads again
                }
            }
        }
    }

    private fun setExport(e: DataExport) = _state.update { s -> s.copy(overview = (s.overview ?: DataOverview()).copy(export = e)) }
    private fun setImport(i: DataImport) = _state.update { s -> s.copy(overview = (s.overview ?: DataOverview()).copy(importStatus = i)) }

    fun startExport() {
        if (_state.value.starting || DataRules.running(_state.value.export.state)) return
        _state.update { it.copy(starting = true) }
        viewModelScope.launch {
            try {
                setExport(DataCalls.startExport(sessions.requireApi()))
                watch()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
            } finally {
                _state.update { it.copy(starting = false) }
            }
        }
    }

    /** The owner picks where the ZIP goes; it is streamed with the token straight into that file. */
    fun saveExport() {
        val e = _state.value.export
        if (!DataRules.exportReady(e) || _state.value.saving) return
        viewModelScope.launch {
            val target = files.saveFile(DataRules.saveName(e), DataRules.ZIP_TYPE) ?: return@launch
            _state.update { it.copy(saving = true) }
            try {
                withContext(Dispatchers.IO) { target.open().use { out -> sessions.requireApi().downloadTo(e.download, out) } }
                _messages.tryEmit(Res.string.data_export_saved)
            } catch (x: CancellationException) {
                throw x
            } catch (x: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
            } finally {
                _state.update { it.copy(saving = false) }
            }
        }
    }

    fun paste(text: String) = _state.update { it.copy(pasted = text) }

    /** The old assistant's answer goes to the server to be read in the background. */
    fun addPasted() {
        val text = _state.value.pasted.trim()
        if (text.isEmpty() || DataRules.running(_state.value.import.state)) return
        viewModelScope.launch {
            try {
                setImport(DataCalls.importText(sessions.requireApi(), text))
                _state.update { it.copy(pasted = "") }
                watch()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                setImport(_state.value.import.copy(state = "failed", error = e.detail))
            } catch (e: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
            }
        }
    }

    fun pickZip() {
        if (DataRules.running(_state.value.import.state)) return
        viewModelScope.launch {
            val file = files.pickFile(listOf(DataRules.ZIP_TYPE, "application/x-zip-compressed", "application/octet-stream")) ?: return@launch
            if (!DataRules.zipFits(file)) { _messages.tryEmit(Res.string.data_zip_too_big); return@launch }
            try {
                setImport(DataCalls.importFile(sessions.requireApi(), file))
                watch()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                setImport(_state.value.import.copy(state = "failed", error = e.detail))
            } catch (e: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
            }
        }
    }

    fun cancelImport() {
        viewModelScope.launch {
            try {
                setImport(DataCalls.cancelImport(sessions.requireApi()))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
            }
        }
    }

    fun typed(text: String) = _state.update { it.copy(typed = text, wrongWord = false) }
    fun signOut(on: Boolean) = _state.update { it.copy(signOut = on) }

    /** Start over: the server saves a copy first, then clears; a wrong word (400) keeps the page as it is. */
    fun reset() {
        val s = _state.value
        if (s.resetting || !DataRules.typedWord(s.typed, s.word)) return
        _state.update { it.copy(resetting = true, wrongWord = false) }
        viewModelScope.launch {
            try {
                val done = DataCalls.reset(sessions.requireApi(), s.typed.trim(), s.signOut)
                ClearHistory.forgetLocally(sessions, cancelNotifications)   // the chat on this device goes with the server's
                _state.update { it.copy(resetDone = done, typed = "", signOut = false) }
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (DataCalls.wrongWord(e)) _state.update { it.copy(wrongWord = true) } else _messages.tryEmit(Res.string.connect_failed)
            } catch (e: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
            } finally {
                _state.update { it.copy(resetting = false) }
            }
        }
    }
}
