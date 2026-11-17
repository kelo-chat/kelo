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
    val filter: DidFilter = DidFilter.ALL,
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
        load(period, filter = _state.value.filter)
    }

    fun setFilter(filter: DidFilter) {
        if (_state.value.filter == filter) return
        val today = todayProvider()
        val zone = zoneProvider()
        val filtered = _state.value.items.filter { filter.matches(it) }
        val groups = ActionRules.groupByDay(filtered, today, zone)
        _state.update { it.copy(filter = filter, groups = groups) }
    }

    fun refresh() {
        load(_state.value.period, filter = _state.value.filter, force = true)
    }

    fun load(
        period: ActionPeriod = _state.value.period,
        filter: DidFilter = _state.value.filter,
        force: Boolean = false,
    ) {
        val api = sessions.api.value ?: return
        _state.update { it.copy(loading = true, failed = false, error = null) }
        viewModelScope.launch {
            try {
                // Try /api/did first; fall back to /api/actions on an older server (404)
                val result = try {
                    api.get<ActionList>("/api/did?days=${period.days}")
                } catch (e: ApiError) {
                    if (e.status == 404) {
                        api.get<ActionList>("/api/actions?days=${period.days}")
                    } else {
                        throw e
                    }
                }
                val today = todayProvider()
                val zone = zoneProvider()
                val filtered = result.items.filter { filter.matches(it) }
                val groups = ActionRules.groupByDay(filtered, today, zone)
                _state.update {
                    it.copy(
                        period = period,
                        filter = filter,
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
