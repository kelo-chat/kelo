package chat.kelo.desktop.pages

import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.Dp
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.pages.work.WorkScreen

/**
 * 작업: one timeline of everything the agent does, as on the phone (android/kelo .../work/WorkScreen.kt): 지금 하는 일, 답이 필요해요,
 * 지켜보는 중, 다가오는 일 and 지난 일, with the task, job, watcher, schedule and open-browser pages over it. See pages/work.
 */
@Composable
fun WorkPane(app: AppContainer, topInset: Dp) = WorkScreen(app, topInset)
