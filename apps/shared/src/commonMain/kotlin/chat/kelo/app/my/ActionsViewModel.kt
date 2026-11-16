package chat.kelo.app.my

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.agent.DayGroups
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import java.time.LocalDate
import java.time.ZoneId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ActionsUiState(
    val period: ActionPeriod = ActionPeriod.DEFAULT,
    val items: List<ActionItem> = emptyList(),
    val groups: List<DayGroups.Group<ActionItem>> = emptyList(),
    val loading: Boolean = false,
    val failed: Boolean = false,
    val error: String? = null,
)

class ActionsViewModel(
    private val sessions: Sessions,
    private val todayProvider: () -> LocalDate = { LocalDate.now() },
    private val zoneProvider: () -> ZoneId = { ZoneId.systemDefault() },
) : ViewModel() {
    private val _state = MutableStateFlow(ActionsUiState())
    val state: StateFlow<ActionsUiState> = _state.asStateFlow()

    fun setPeriod(period: ActionPeriod) {
        if (_state.value.period == period && _state.value.items.isNotEmpty()) return
        _state.update { it.copy(period = period) }
        load(period)
    }

    fun refresh() {
        load(_state.value.period, force = true)
    }

    fun load(period: ActionPeriod = _state.value.period, force: Boolean = false) {
        val api = sessions.api.value ?: return
        _state.update { it.copy(loading = true, failed = false, error = null) }
        viewModelScope.launch {
            try {
                val result = api.get<ActionList>("/api/actions?days=${period.days}")
                val today = todayProvider()
                val zone = zoneProvider()
                val groups = ActionRules.groupByDay(result.items, today, zone)
                _state.update {
                    it.copy(
                        period = period,
                        items = result.items,
                        groups = groups,
                        loading = false,
                        failed = false,
                        error = null,
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                _state.update { it.copy(loading = false, failed = true, error = e.detail) }
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, failed = true, error = e.message) }
            }
        }
    }
}
