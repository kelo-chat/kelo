package chat.kelo.app.wallet

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.LinearProgressIndicator
import chat.kelo.app.ui.components.PrimaryButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.MyCard
import chat.kelo.app.my.SubPage
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import java.time.YearMonth

/** Shows raw card digits grouped ("4242 4242 4242 4242") while the field keeps only the digits. */
internal object CardNumberTransformation : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val raw = text.text
        val grouped = CardNumber.group(raw)
        return TransformedText(AnnotatedString(grouped), object : OffsetMapping {
            override fun originalToTransformed(offset: Int): Int = CardNumber.groupedOffset(raw, offset).coerceIn(0, grouped.length)
            override fun transformedToOriginal(offset: Int): Int = CardNumber.rawOffset(raw, offset).coerceIn(0, raw.length)
        })
    }
}

/** Shows "0428" as "04/28". */
internal object ExpiryTransformation : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val raw = text.text
        val shown = Expiry.format(raw)
        return TransformedText(AnnotatedString(shown), object : OffsetMapping {
            override fun originalToTransformed(offset: Int): Int = if (offset > 2 && raw.length > 2) offset + 1 else offset
            override fun transformedToOriginal(offset: Int): Int = if (offset > 2 && raw.length > 2) offset - 1 else offset.coerceAtMost(raw.length)
        })
    }
}

/** A labelled input of the card forms. [error] shows under it in red. */
@Composable
internal fun WalletField(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    placeholder: String = "",
    keyboard: KeyboardOptions = KeyboardOptions.Default,
    transformation: VisualTransformation = VisualTransformation.None,
    error: String? = null,
    trailing: (@Composable () -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        // a fixed height: Korean and Latin labels side by side (유효기간 / CVC) keep their fields level
        Text(label, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, modifier = Modifier.padding(start = 2.dp).height(20.dp))
        OutlinedTextField(
            value = value, onValueChange = onChange, singleLine = true, modifier = Modifier.fillMaxWidth(),
            placeholder = { chat.kelo.app.ui.components.FieldPlaceholder(placeholder) },
            visualTransformation = transformation, keyboardOptions = keyboard, isError = error != null,
            trailingIcon = trailing,
            shape = RoundedCornerShape(12.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = c.card, unfocusedContainerColor = c.card, errorContainerColor = c.card,
                focusedBorderColor = c.accent, unfocusedBorderColor = c.line, errorBorderColor = c.danger, cursorColor = c.accent,
                focusedTextColor = c.ink, unfocusedTextColor = c.ink, errorTextColor = c.ink,
            ),
        )
        if (error != null) ErrorText(error, small = true, modifier = Modifier.padding(start = 2.dp))
    }
}

private val DIGITS = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Next)

/** The save button (a plain primary button), "저장하는 중…" while it works. */
@Composable
internal fun SaveButton(text: String, enabled: Boolean, busy: Boolean, onClick: () -> Unit) {
    PrimaryButton(if (busy) stringResource(R.string.wallet_saving) else text, onClick = onClick, enabled = enabled && !busy)
}

/** The number field's checks, shown once a full number is typed. */
@Composable
private fun numberError(number: String, mustEnd: String?): String? {
    val n = CardNumber.digits(number)
    val brand = CardNumber.brand(n)
    val complete = n.length >= (if (brand == CardBrand.AMEX) 15 else 16) || n.length == CardNumber.MAX_DIGITS
    return when {
        !complete -> null
        !CardNumber.valid(n) -> stringResource(R.string.wallet_number_invalid)
        mustEnd != null && CardNumber.last4(n) != mustEnd -> stringResource(R.string.wallet_number_other_card, mustEnd)
        else -> null
    }
}

/**
 * 카드 등록, in steps over a live card face: 1 the number (its brand shows as it is typed), 2 expiry and CVC, 3 the holder's name
 * and postal code (both optional), then the notice to tick and Save. The number and CVC are kept only in memory (never in saved
 * state or on disk; [WalletPlaces] holds them through a lock for up to ten minutes), sealed with this phone's key after the owner's
 * check, and cleared once sent. Back steps back a step.
 */
@Composable
internal fun AddCardPage(onBack: () -> Unit, onSaved: () -> Unit) {
    var back by remember { mutableStateOf<(() -> Boolean)?>(null) }
    SubPage(stringResource(R.string.wallet_add_title), onBack = { if (back?.invoke() != true) onBack() }) {
        AddCardForm(onSaved = { onSaved() }, stepBack = { back = it })
    }
}

/**
 * The card form itself, used by 카드 등록 and, [inline], by a card entry in the chat (request_card) and the Keychain's waiting list.
 * [stepBack] gets a function that goes back one step (true) or says there is none (false). Inline, nothing is kept through the
 * wallet's lock or taken from a held-back message: the draft lives only as long as the card on screen. [onSaved] gets the new card
 * (its nickname and last four digits; never the number).
 */
@Composable
internal fun ColumnScope.AddCardForm(
    onSaved: (WalletCard) -> Unit, stepBack: ((() -> Boolean)?) -> Unit = {}, inline: Boolean = false,
) {
    val sessions = LocalAppContainer.current.sessions
    val auth = rememberCardAuthenticator()
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val c = Kelo.colors
    // what was typed, picked up again after the lock (WalletPlaces: memory only, ten minutes); every change goes through set()
    // a card number from a message the chat held back (SecretGuard): already filled in, taken out of the hand-off at once
    val handoff = LocalAppContainer.current.secretHandoff
    var draft by remember {
        mutableStateOf(
            if (inline) AddCardDraft()
            else WalletPlaces.add() ?: handoff.peek()?.let { v -> handoff.clear(); AddCardDraft(number = CardNumber.digits(v).take(19)) } ?: AddCardDraft(),
        )
    }
    fun set(d: AddCardDraft) { draft = d; if (!inline) WalletPlaces.saveAdd(d) }
    val step = draft.step
    val holder = draft.holder
    val number = draft.number
    val expiry = draft.expiry
    val cvc = draft.cvc
    val postal = draft.postal
    // the notice is acknowledged each time a card is added (never remembered as "seen"; kept only through a lock mid-flow)
    val agreed = draft.agreed
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    val context = androidx.compose.ui.platform.LocalContext.current
    // the page's own copy goes with the page; the kept place is WalletPlaces' to forget
    DisposableEffect(Unit) { onDispose { draft = AddCardDraft() } }
    val prev = AddCardSteps.prev(step)
    if (!inline) androidx.activity.compose.BackHandler(enabled = prev != null && !busy) { set(draft.copy(step = prev ?: step)) }
    val goBack by androidx.compose.runtime.rememberUpdatedState { if (prev != null && !busy) { set(draft.copy(step = prev)); true } else false }
    DisposableEffect(Unit) { stepBack { goBack() }; onDispose { stepBack(null) } }

    val now = YearMonth.now()
    val brand = CardNumber.brand(number)
    val exp = Expiry.check(expiry, now)
    val expError = when (exp) {
        ExpiryCheck.BadMonth -> stringResource(R.string.wallet_expiry_bad_month)
        ExpiryCheck.Expired -> stringResource(R.string.wallet_expiry_expired)
        ExpiryCheck.TooFar -> stringResource(R.string.wallet_expiry_too_far)
        else -> null
    }
    val title = stringResource(R.string.wallet_auth_save_title)
    val subtitle = stringResource(R.string.wallet_auth_save_subtitle)
    val failedSend = stringResource(R.string.wallet_save_server)
    val words = rememberSaveWords()

    fun save() {
        AddCardSteps.firstBlocking(number, expiry, cvc, now)?.let { set(draft.copy(step = it)); return }
        val ok = exp as? ExpiryCheck.Ok ?: return
        val secret = CardSecret.of(number, cvc) ?: return
        busy = true; problem = ""
        scope.launch {
            try {
                val a = auth.awaitSaving(title, subtitle)
                if (a !is CardAuth.Ok) {
                    problem = words.of(a)
                    return@launch
                }
                val blob = CardBlob.seal(secret, a.cipher, a.keyId)
                val saved = WalletCalls(sessions.requireApi()).add(
                    NewCard(holder, postal, ok.month, ok.year, CardNumber.brand(number), label = draft.nickname.trim()), blob, secret,
                )
                WalletConsent(context).cardSaved(saved.id)
                if (!inline) {
                    WalletPlaces.clear()
                    handoff.kept()   // the chat puts "(saved to Keychain: …)" in place of the number, if it came from there
                }
                draft = AddCardDraft()
                haptics.confirm()
                onSaved(saved)
            } catch (e: ApiError) {
                problem = if (e.isNetwork) failedSend else e.detail
                haptics.reject()
            } finally {
                secret.wipe()
                busy = false
            }
        }
    }
    run {
        CardFaceLarge(
            brand, CardFaceRules.preview(number), holder,
            if (expiry.isEmpty()) stringResource(R.string.wallet_field_expiry_hint) else Expiry.format(expiry),
            modifier = Modifier.padding(horizontal = if (inline) 40.dp else 24.dp),
        )
        val (at, of) = AddCardSteps.position(step)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(stringResource(stepTitle(step)), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
            Text(stringResource(R.string.wallet_step_of, at, of), style = MaterialTheme.typography.labelLarge, color = c.muted)
        }
        LinearProgressIndicator(
            progress = { at / of.toFloat() }, modifier = Modifier.fillMaxWidth().height(4.dp),
            color = c.accent, trackColor = c.line, drawStopIndicator = {},
        )
        when (step) {
            AddStep.NUMBER -> {
                WalletField(
                    stringResource(R.string.wallet_field_number), number,
                    { set(draft.copy(number = CardNumber.digits(it).take(CardNumber.maxLength(CardNumber.brand(it))))) },
                    placeholder = stringResource(R.string.wallet_field_number_hint), keyboard = DIGITS, transformation = CardNumberTransformation,
                    error = numberError(number, null),
                    trailing = brandName(brand)?.let { name -> { Text(name, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(end = 12.dp)) } },
                )
                // 카드 스캔: fills the number and, when read, the expiry (never the CVC); the owner checks them on the next steps
                CardScanButton(onResult = { scan -> set(CardScanFill.apply(draft, scan)) })
            }
            AddStep.DATES -> Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                WalletField(
                    stringResource(R.string.wallet_field_expiry), expiry, { set(draft.copy(expiry = Expiry.clean(it))) },
                    placeholder = stringResource(R.string.wallet_field_expiry_hint), keyboard = DIGITS, transformation = ExpiryTransformation,
                    error = expError, modifier = Modifier.weight(1f),
                )
                WalletField(
                    stringResource(R.string.wallet_field_cvc), cvc, { set(draft.copy(cvc = Cvc.clean(it, brand))) },
                    placeholder = stringResource(if (brand == CardBrand.AMEX) R.string.wallet_field_cvc_hint4 else R.string.wallet_field_cvc_hint3),
                    keyboard = DIGITS, transformation = PasswordVisualTransformation(), modifier = Modifier.weight(1f),
                )
            }
            AddStep.OWNER -> {
                // the owner's name for the card: its title in the wallet, the approval cards and what Kelo calls it
                WalletField(
                    stringResource(R.string.wallet_nickname_field), draft.nickname, { set(draft.copy(nickname = it.take(60))) },
                    placeholder = stringResource(R.string.wallet_rename_hint),
                    keyboard = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Next),
                    error = if (Regex("(?:\\d[ -]?){12,}").containsMatchIn(draft.nickname)) stringResource(R.string.wallet_rename_no_digits) else null,
                )
                WalletField(
                    stringResource(R.string.wallet_field_holder), holder, { set(draft.copy(holder = it.take(80))) },
                    placeholder = stringResource(R.string.wallet_field_holder_hint),
                    keyboard = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false, imeAction = ImeAction.Next),
                )
                WalletField(
                    stringResource(R.string.wallet_field_postal), postal, { set(draft.copy(postal = it.take(16))) },
                    placeholder = stringResource(R.string.wallet_field_postal_hint),
                    keyboard = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Done),
                )
            }
            AddStep.CONFIRM -> {
                Text(stringResource(R.string.wallet_card_notice), style = MaterialTheme.typography.bodySmall, color = c.ink)
                AgreeRow(agreed, onAgree = { set(draft.copy(agreed = it)) })
            }
        }
        if (problem.isNotEmpty()) ErrorText(problem, small = true, modifier = Modifier.padding(horizontal = 4.dp))
        val canLeave = AddCardSteps.canLeave(step, number, expiry, cvc, agreed, now)
        if (step == AddStep.CONFIRM) {
            SaveButton(stringResource(R.string.wallet_save), enabled = canLeave, busy = busy, onClick = ::save)
        } else {
            PrimaryButton(stringResource(R.string.wallet_step_next), enabled = canLeave, onClick = { set(draft.copy(step = AddCardSteps.next(step))) })
        }
        if (inline && prev != null) chat.kelo.app.ui.components.LinkButton(stringResource(R.string.back), onClick = { goBack() })
    }
}

private fun stepTitle(s: AddStep): Int = when (s) {
    AddStep.NUMBER -> R.string.wallet_step_number
    AddStep.DATES -> R.string.wallet_step_dates
    AddStep.OWNER -> R.string.wallet_step_owner
    AddStep.CONFIRM -> R.string.wallet_step_confirm
}

/** "확인했어요" with a checkbox; the whole row toggles it. */
@Composable
internal fun AgreeRow(agreed: Boolean, onAgree: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth()
            .agreeToggle(agreed, onAgree),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        androidx.compose.material3.Checkbox(
            checked = agreed, onCheckedChange = null,
            colors = androidx.compose.material3.CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent, uncheckedColor = c.muted),
        )
        Text(stringResource(R.string.wallet_notice_agree), color = c.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 8.dp))
    }
}

private fun Modifier.agreeToggle(value: Boolean, onChange: (Boolean) -> Unit): Modifier =
    this.then(Modifier.toggleable(value = value, role = androidx.compose.ui.semantics.Role.Checkbox, onValueChange = onChange)).padding(vertical = 4.dp)

/** Why a card was not saved, as text resolved in composition (the save runs in a coroutine). */
internal class SaveWords(private val cancelled: String, private val noLock: String, private val keyLost: String, private val failed: String) {
    fun of(a: CardAuth): String = when (a) {
        is CardAuth.Ok -> ""
        CardAuth.Cancelled -> cancelled
        CardAuth.NoLock -> noLock
        CardAuth.KeyLost -> keyLost
        is CardAuth.Failed -> failed.replace("%1\$s", a.message)
    }
}

@Composable
internal fun rememberSaveWords(): SaveWords {
    val a = stringResource(R.string.wallet_save_cancelled)
    val b = stringResource(R.string.wallet_save_no_lock)
    val c = stringResource(R.string.wallet_save_key_lost)
    val d = stringResource(R.string.wallet_save_failed)
    return remember(a, b, c, d) { SaveWords(a, b, c, d) }
}

/**
 * The number and CVC of a saved card typed again: to make a new phone copy (a card this phone cannot open), or to turn auto-pay on
 * (the server keeps its own sealed copy then). The number must be the same card (same last 4 digits).
 */
@Composable
internal fun ReenterPage(card: WalletCard, autoPay: Boolean, onBack: () -> Unit, onSaved: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val auth = rememberCardAuthenticator()
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    // kept through the lock like 카드 등록's fields (WalletPlaces: memory only, ten minutes)
    var draft by remember { mutableStateOf(WalletPlaces.reenter(card.id, autoPay) ?: ReenterDraft(card.id, autoPay)) }
    fun set(d: ReenterDraft) { draft = d; WalletPlaces.saveReenter(d) }
    val number = draft.number
    val cvc = draft.cvc
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    val context = androidx.compose.ui.platform.LocalContext.current
    DisposableEffect(Unit) { onDispose { draft = ReenterDraft(card.id, autoPay) } }
    val brand = CardNumber.brand(number).takeIf { number.isNotEmpty() } ?: CardBrand.of(card.brand)
    val ready = CardNumber.valid(number) && CardNumber.last4(number) == card.last4 && Cvc.valid(cvc, brand)
    val title = stringResource(R.string.wallet_auth_save_title)
    val subtitle = stringResource(R.string.wallet_auth_save_subtitle)
    val failedSend = stringResource(R.string.wallet_save_server)
    val words = rememberSaveWords()
    var needKeyguard by remember { mutableStateOf(false) }
    fun save() {
        val secret = CardSecret.of(number, cvc) ?: return
        busy = true; problem = ""
        scope.launch {
            try {
                val a = auth.awaitSaving(title, subtitle)
                if (a !is CardAuth.Ok) {
                    problem = words.of(a)
                    return@launch
                }
                WalletCalls(sessions.requireApi()).replace(card.id, CardBlob.seal(secret, a.cipher, a.keyId), secret, autoPay)
                if (autoPay) WalletConsent(context).autoPayAccepted(card.id)
                draft = ReenterDraft(card.id, autoPay)
                haptics.confirm()
                onSaved()
            } catch (e: ApiError) {
                if (e.code == AutoPayGate.CODE_KEYGUARD_REQUIRED) needKeyguard = true   // auto-pay needs Keyguard: the dialog, then again
                else problem = if (e.isNetwork) failedSend else e.detail
                haptics.reject()
            } finally {
                secret.wipe()
                busy = false
            }
        }
    }
    if (needKeyguard) chat.kelo.app.keyguard.KeyguardNeededDialog(onEnabled = { needKeyguard = false; save() }, onDismiss = { needKeyguard = false })

    SubPage(stringResource(if (autoPay) R.string.wallet_reenter_auto_title else R.string.wallet_reenter_title), onBack) {
        Note(stringResource(if (autoPay) R.string.wallet_reenter_auto_body else R.string.wallet_reenter_body, card.label))
        MyCard {
            WalletField(
                stringResource(R.string.wallet_field_number), number,
                { set(draft.copy(number = CardNumber.digits(it).take(CardNumber.maxLength(CardNumber.brand(it))))) },
                placeholder = stringResource(R.string.wallet_field_number_ending, card.last4), keyboard = DIGITS,
                transformation = CardNumberTransformation, error = numberError(number, card.last4),
            )
            CardScanButton(onResult = { scan -> set(draft.copy(number = CardNumber.digits(scan.number).take(CardNumber.maxLength(CardNumber.brand(scan.number))))) })
            WalletField(
                stringResource(R.string.wallet_field_cvc), cvc, { set(draft.copy(cvc = Cvc.clean(it, brand))) },
                placeholder = stringResource(if (brand == CardBrand.AMEX) R.string.wallet_field_cvc_hint4 else R.string.wallet_field_cvc_hint3),
                keyboard = DIGITS.copy(imeAction = ImeAction.Done), transformation = PasswordVisualTransformation(),
            )
        }
        if (problem.isNotEmpty()) ErrorText(problem, small = true, modifier = Modifier.padding(horizontal = 4.dp))
        SaveButton(stringResource(if (autoPay) R.string.wallet_auto_turn_on else R.string.wallet_save), enabled = ready, busy = busy, onClick = ::save)
    }
}
