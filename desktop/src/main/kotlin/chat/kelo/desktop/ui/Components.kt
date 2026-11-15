package chat.kelo.desktop.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.pressShrink

// The desktop copies of android/kelo .../ui/components/Components.kt (same shapes and colours). Every text here wraps: no fixed
// widths around words, so a long German label or a spaceless Thai one breaks onto the next line instead of being cut.

private val Rounded12 = RoundedCornerShape(12.dp)
private val ButtonShape = RoundedCornerShape(50)

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    Button(
        onClick = onClick, enabled = enabled, modifier = modifier.pressShrink(source), shape = ButtonShape, interactionSource = source,
        colors = ButtonDefaults.buttonColors(
            containerColor = c.accent, contentColor = c.onAccent, disabledContainerColor = c.disabledFill, disabledContentColor = c.disabledInk,
        ),
        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 10.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    OutlinedButton(
        onClick = onClick, enabled = enabled, modifier = modifier.pressShrink(source), shape = ButtonShape, border = BorderStroke(1.dp, c.line),
        interactionSource = source,
        colors = ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink, disabledContainerColor = c.card, disabledContentColor = c.disabledInk),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 10.dp),
    ) { Text(text, style = MaterialTheme.typography.bodyMedium) }
}

@Composable
fun LinkButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, danger: Boolean = false) {
    val c = Kelo.colors
    TextButton(onClick = onClick, modifier = modifier, contentPadding = PaddingValues(horizontal = 4.dp, vertical = 4.dp)) {
        Text(text, color = if (danger) c.danger else c.accent, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun ChoiceChip(text: String, selected: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    OutlinedButton(
        onClick = onClick, shape = RoundedCornerShape(8.dp), border = BorderStroke(1.dp, if (selected) c.mine else c.line),
        colors = ButtonDefaults.outlinedButtonColors(containerColor = if (selected) c.mine else c.card, contentColor = c.ink),
        contentPadding = PaddingValues(start = if (selected) 8.dp else 12.dp, end = 12.dp, top = 4.dp, bottom = 4.dp),
    ) {
        if (selected) Icon(KeloIcons.Check, contentDescription = null, modifier = Modifier.size(16.dp).padding(end = 2.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

/**
 * The hint inside an empty field. A field that holds one value (an address, a key, a code, a name, a number) is one line: its value
 * scrolls sideways and its hint never wraps (it is cut with … instead). Only message-like fields (`singleLine = false`) wrap.
 * SingleLineFieldsTest checks every field of the app keeps to this (as on Android).
 */
@Composable
fun FieldPlaceholder(text: String, singleLine: Boolean = true) {
    if (singleLine) {
        Text(text, color = Kelo.colors.muted, maxLines = 1, softWrap = false, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
    } else {
        Text(text, color = Kelo.colors.muted)
    }
}

/**
 * A one-value field with its button (저장, 추가, 보내기). Where there is room the button sits beside the field; in a narrow column
 * ([stackBelow]) it goes under the field, so the field keeps the whole width instead of being squeezed (as on Android).
 */
@Composable
fun FieldWithAction(
    modifier: Modifier = Modifier,
    stackBelow: androidx.compose.ui.unit.Dp = FIELD_ACTION_STACK_BELOW,
    field: @Composable (Modifier) -> Unit,
    action: @Composable () -> Unit,
) {
    androidx.compose.foundation.layout.BoxWithConstraints(modifier.fillMaxWidth()) {
        if (maxWidth < stackBelow) {
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                field(Modifier.fillMaxWidth())
                androidx.compose.foundation.layout.Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) { action() }
            }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                field(Modifier.weight(1f))
                action()
            }
        }
    }
}

/** Below this width a field's button goes under it (FieldWithAction). */
val FIELD_ACTION_STACK_BELOW = 440.dp

@Composable
fun KeloTextField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    password: Boolean = false,
    singleLine: Boolean = true,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    maxLength: Int = Int.MAX_VALUE,
    label: String? = null,
    trailingIcon: (@Composable () -> Unit)? = null,
) {
    val c = Kelo.colors
    OutlinedTextField(
        value = value,
        onValueChange = { onValueChange(it.take(maxLength)) },
        modifier = modifier.fillMaxWidth(),
        // a placeholder that only repeats the floating label would show the same words twice while the field is focused and empty
        placeholder = if (placeholder.isBlank() || placeholder == label) null else { { FieldPlaceholder(placeholder, singleLine) } },
        label = label?.let { { Text(it, maxLines = 1, softWrap = false, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) } },
        trailingIcon = trailingIcon,
        singleLine = singleLine,
        maxLines = if (singleLine) 1 else Int.MAX_VALUE,
        shape = Rounded12,
        visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = keyboardOptions,
        keyboardActions = keyboardActions,
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = c.card, unfocusedContainerColor = c.card,
            focusedBorderColor = c.accent, unfocusedBorderColor = c.line, cursorColor = c.accent,
            focusedTextColor = c.ink, unfocusedTextColor = c.ink, focusedLabelColor = c.accent, unfocusedLabelColor = c.muted,
        ),
    )
}

/** A rounded card with a hairline border. */
@Composable
fun Card(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val c = Kelo.colors
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(18.dp))
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content,
    )
}

@Composable
fun MutedText(text: String, modifier: Modifier = Modifier, small: Boolean = false) {
    Text(text, modifier = modifier, color = Kelo.colors.muted, style = if (small) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium)
}

@Composable
fun ErrorText(text: String, modifier: Modifier = Modifier, small: Boolean = false) {
    Text(text, modifier = modifier, color = Kelo.colors.danger, style = if (small) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium)
}

/** A page's big title. */
@Composable
fun PageTitle(title: String, modifier: Modifier = Modifier) {
    Text(title, style = MaterialTheme.typography.displaySmall, color = Kelo.colors.ink, modifier = modifier.semantics { heading() })
}

/** A section heading above a group of rows. */
@Composable
fun SectionTitle(title: String, modifier: Modifier = Modifier) {
    Text(title, style = MaterialTheme.typography.titleSmall, color = Kelo.colors.accent, modifier = modifier.padding(top = 8.dp).semantics { heading() })
}

/**
 * A page's scrolling column, at most [maxWidth] wide and centred in the window (a desktop window is often much wider than a
 * comfortable line).
 */
@Composable
fun PageColumn(modifier: Modifier = Modifier, maxWidth: Int = 760, content: @Composable ColumnScope.() -> Unit) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = maxWidth.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
    }
}

/** A row in a list: an icon, a title with an optional second line, and something at the end; clickable when [onClick] is set. */
@Composable
fun ListRow(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    icon: ImageVector? = null,
    onClick: (() -> Unit)? = null,
    trailing: @Composable RowScope.() -> Unit = {},
) {
    val c = Kelo.colors
    Row(
        modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .heightIn(min = 48.dp).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (icon != null) Icon(icon, contentDescription = null, tint = c.muted, modifier = Modifier.size(22.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            if (!subtitle.isNullOrBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
        trailing()
    }
}

/** A row with a switch; the whole row toggles it. */
@Composable
fun SwitchRow(title: String, checked: Boolean, onChange: (Boolean) -> Unit, subtitle: String? = null, enabled: Boolean = true) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
            .clickable(enabled = enabled, role = Role.Switch) { onChange(!checked) }
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = if (enabled) c.ink else c.disabledInk)
            if (!subtitle.isNullOrBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = checked, onCheckedChange = onChange, enabled = enabled,
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent, uncheckedTrackColor = c.bubble, uncheckedBorderColor = c.line),
        )
    }
}

/** A small rounded label ("연결됨"). */
@Composable
fun Pill(text: String, soft: androidx.compose.ui.graphics.Color, ink: androidx.compose.ui.graphics.Color, modifier: Modifier = Modifier) {
    Text(
        text, color = ink, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold,
        modifier = modifier.clip(RoundedCornerShape(99.dp)).background(soft).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}
