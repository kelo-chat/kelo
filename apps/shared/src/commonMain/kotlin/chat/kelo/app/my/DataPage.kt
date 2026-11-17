package chat.kelo.app.my

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.GroupCard
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.theme.Kelo

/** 마이 → Your data: download a copy, bring memories from another assistant, start over. */
@Composable
fun DataPage(onBack: () -> Unit) {
    val services = LocalAppServices.current
    val vm = rememberViewModel("my-data") { DataViewModel(services.sessions, services.filePicker, { services.notifications?.cancelAll() }) }
    val state by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.load() }
    val toaster = chat.kelo.app.platform.LocalToaster.current
    LaunchedEffect(vm) { vm.messages.collect { toaster.show(loadString(it)) } }
    SubPage(string(Res.string.data_title), onBack) {
        MutedText(string(Res.string.data_intro), small = true)
        if (state.overview == null) {
            if (state.failed) ErrorText(string(Res.string.connect_failed), small = true)
        } else {
            ExportSection(state, vm)
            ImportSection(state, vm)
            ResetSection(state, vm)
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold, color = Kelo.colors.ink, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun ExportSection(state: DataUiState, vm: DataViewModel) {
    val e = state.export
    SectionTitle(string(Res.string.data_export_title))
    GroupCard {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            MutedText(string(Res.string.data_export_body), small = true)
            when {
                DataRules.running(e.state) -> {
                    LinearProgressIndicator(Modifier.fillMaxWidth(), color = Kelo.colors.accent)
                    MutedText(string(Res.string.data_export_making), small = true)
                }
                DataRules.exportReady(e) -> {
                    Text(string(Res.string.data_export_ready, fileSize(e.bytes)), color = Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium)
                    PrimaryButton(string(Res.string.data_export_save), { vm.saveExport() }, Modifier.fillMaxWidth(), enabled = !state.saving)
                    SecondaryButton(string(Res.string.data_export_again), { vm.startExport() }, Modifier.fillMaxWidth(), enabled = !state.starting)
                }
                else -> {
                    if (e.state == "failed") ErrorText(string(Res.string.data_export_failed), small = true)
                    PrimaryButton(string(Res.string.data_export_make), { vm.startExport() }, Modifier.fillMaxWidth(), enabled = !state.starting)
                }
            }
        }
    }
}

@Composable
private fun ImportSection(state: DataUiState, vm: DataViewModel) {
    val i = state.import
    val running = DataRules.running(i.state)
    val clipboard = LocalClipboardManager.current
    val toaster = chat.kelo.app.platform.LocalToaster.current
    val copied = string(Res.string.data_import_copied)
    SectionTitle(string(Res.string.data_import_title))
    GroupCard {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            MutedText(string(Res.string.data_import_private), small = true)
            Text(string(Res.string.data_import_step1), color = Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium)
            SecondaryButton(string(Res.string.data_import_copy), {
                clipboard.setText(AnnotatedString(state.prompt)); toaster.show(copied)
            }, Modifier.fillMaxWidth(), enabled = state.prompt.isNotEmpty())
            Text(string(Res.string.data_import_step2), color = Kelo.colors.ink, style = MaterialTheme.typography.bodyMedium)
            KeloTextField(state.pasted, vm::paste, string(Res.string.data_import_paste_hint), singleLine = false)
            PrimaryButton(string(Res.string.data_import_add), { vm.addPasted() }, Modifier.fillMaxWidth(), enabled = state.pasted.isNotBlank() && !running)
            MutedText(string(Res.string.data_import_zip_body), small = true)
            SecondaryButton(string(Res.string.data_import_zip), { vm.pickZip() }, Modifier.fillMaxWidth(), enabled = !running)
            ImportProgress(i, vm)
        }
    }
}

@Composable
private fun ImportProgress(i: DataImport, vm: DataViewModel) {
    val c = Kelo.colors
    when {
        DataRules.running(i.state) -> {
            if (i.total > 0) LinearProgressIndicator(progress = { DataRules.percent(i) / 100f }, modifier = Modifier.fillMaxWidth(), color = c.accent)
            else LinearProgressIndicator(Modifier.fillMaxWidth(), color = c.accent)
            MutedText(if (i.total > 0) string(Res.string.data_import_running, i.done, i.total) else string(Res.string.data_import_reading), small = true)
            SecondaryButton(string(Res.string.data_import_cancel), { vm.cancelImport() }, Modifier.fillMaxWidth())
        }
        i.state == "failed" -> ErrorText(string(Res.string.data_import_failed, i.error.ifEmpty { string(Res.string.connect_failed) }), small = true)
        DataRules.importFinished(i) -> {
            if (i.added.isEmpty()) {
                MutedText(string(Res.string.data_import_none), small = true)
            } else {
                Text(string(Res.string.data_import_added, i.added.size), color = c.ink, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                i.added.forEach { f -> Text("• ${f.text}", color = c.ink, style = MaterialTheme.typography.bodySmall) }
            }
        }
    }
}

@Composable
private fun ResetSection(state: DataUiState, vm: DataViewModel) {
    val c = Kelo.colors
    var confirm by remember { mutableStateOf(false) }
    SectionTitle(string(Res.string.data_reset_title))
    GroupCard {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            MutedText(string(Res.string.data_reset_body), small = true)
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(state.signOut, { vm.signOut(it) })
                Text(string(Res.string.data_reset_signout), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            }
            Text(string(Res.string.data_reset_type, state.word), color = c.ink, style = MaterialTheme.typography.bodyMedium)
            KeloTextField(state.typed, vm::typed, state.word)
            if (state.wrongWord) ErrorText(string(Res.string.data_reset_wrong), small = true)
            PrimaryButton(string(Res.string.data_reset_button), { confirm = true }, Modifier.fillMaxWidth(),
                enabled = DataRules.typedWord(state.typed, state.word) && !state.resetting)
            state.resetDone?.let { MutedText(string(Res.string.data_reset_done, it.backup.name), small = true) }
        }
    }
    if (confirm) {
        ConfirmDialog(string(Res.string.data_reset_confirm), string(Res.string.data_reset_button), danger = true,
            onDismiss = { confirm = false }, onYes = { confirm = false; vm.reset() })
    }
}
