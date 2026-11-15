package chat.kelo.app.core

import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource

/** Text decided in a ViewModel: a string resource, or text the server sent (its "detail"). */
sealed interface UiText {
    data class Res(@StringRes val id: Int) : UiText
    data class Raw(val text: String) : UiText

    @Composable
    fun resolve(): String = when (this) {
        is Res -> stringResource(id)
        is Raw -> text
    }
}
