package chat.kelo.app.my

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** [info]: null until the server answered, and after a failed read (the row is hidden then); [checking]: a connection check is running. */
data class AndroidDeviceUiState(val info: AndroidDeviceInfo? = null, val checking: Boolean = false)

/**
 * 마이 → 휴대폰 기기: the device's state. One read when My opens (it decides whether the row shows), and the page's "연결 확인" asks the
 * server for a fresh reading (POST /api/android/check).
 */
class AndroidDeviceViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(AndroidDeviceUiState())
    val state: StateFlow<AndroidDeviceUiState> = _state.asStateFlow()

    fun load() {
        val api = sessions.api.value ?: return
        viewModelScope.launch {
            try {
                val info = api.get<AndroidDeviceInfo>("/api/android")
                _state.update { it.copy(info = info) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(info = null) }   // an older server, or none reachable: no row
            }
        }
    }

    fun check() {
        val api = sessions.api.value ?: return
        if (_state.value.checking) return
        _state.update { it.copy(checking = true) }
        viewModelScope.launch {
            try {
                val info = api.post<AndroidDeviceInfo>("/api/android/check", JsonObject(emptyMap()))
                _state.update { it.copy(info = info, checking = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // the server did not answer: the device cannot be said to be connected
                _state.update { it.copy(info = it.info?.copy(connected = false), checking = false) }
            }
        }
    }
}
