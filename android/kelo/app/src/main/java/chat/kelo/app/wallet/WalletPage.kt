package chat.kelo.app.wallet

import android.content.Intent
import android.os.SystemClock
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.blur
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.MyCard
import chat.kelo.app.my.SubPage
import chat.kelo.app.ui.PushHost
import chat.kelo.app.ui.SecureWindow
import chat.kelo.app.ui.components.BackTitleBar
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SkeletonLines
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberVaultGate
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** The card pages' own sub pages. */
internal sealed interface WalletView {
    data object List : WalletView
    data object Add : WalletView
    data class Card(val id: String) : WalletView
    /** Re-enter the number and CVC of a card: to repair its phone copy, or ([autoPay]) to turn auto-pay on */
    data class Reenter(val id: String, val autoPay: Boolean) : WalletView
    data object Limits : WalletView
    data object Payments : WalletView
    /** A shipping address to change, or a new one ([id] null) */
    data class ShippingEdit(val id: String?) : WalletView

    fun depth(): Int = when (this) {
        List -> 0
        Add, Limits, Payments, is Card, is ShippingEdit -> 1
        is Reenter -> 2
    }
}

/**
 * 마이 → 결제 카드. Opening it always takes the owner's check (fingerprint, face or the screen lock); until then only the locked
 * page shows. It locks again when the app goes to the background, after two minutes without a touch, and when it is left. When the
 * page is back in front locked, the phone's check comes up by itself once; backed out of, the page keeps an unlock button (and a way
 * back). The page that was open and what was typed on it come back after the check ([WalletPlaces], memory only, ten minutes). The
 * window stays out of screenshots and the recent-apps preview the whole time.
 */
@Composable
fun WalletPage(onBack: () -> Unit, onSeen: () -> Unit = {}) {
    SecureWindow()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val lock = remember { WalletLock() }
    val flow = remember { UnlockFlow(UnlockFlow.credentialLeavesAppHere) }
    // the flow is plain state; this counter makes Compose read it again after each change
    var tick by remember { mutableIntStateOf(0) }
    fun changed() { tick++ }
    val leave: () -> Unit = { WalletPlaces.clear(); onBack() }
    val gate = rememberVaultGate(
        onNoLock = { flow.noLock(); changed() },
        title = stringResource(R.string.wallet_gate_title),
        onNotOpened = { flow.notOpened(); changed() },
    ) { _ ->
        lock.unlock(SystemClock.elapsedRealtime())
        flow.opened(); changed()
    }
    val resumed = { lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) }
    fun askIfDue() { if (resumed() && flow.resumed()) { changed(); gate() } }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { askIfDue() }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) {
        // Android 9 and older: a card check's screen-lock page is what sent the app to the background, not the owner leaving
        if (CardPrompts.active() && UnlockFlow.credentialLeavesAppHere && flow.isOpen) return@LifecycleEventEffect
        // Google Pay's card scan screen is up over the add form (CardScanAway); the two-minute limit still applies
        if (CardScanAway.active() && flow.isOpen) return@LifecycleEventEffect
        lock.background(); flow.backgrounded(); changed()
    }
    // the place is forgotten when the card pages are left while the app is on screen, not when the activity is only re-created
    DisposableEffect(lifecycle) {
        onDispose { if (lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) WalletPlaces.clear() }
    }
    val open = tick.let { flow.isOpen }
    LaunchedEffect(open) {
        while (open) {
            delay(1000)
            if (!lock.isOpen(SystemClock.elapsedRealtime())) { flow.timedOut(); changed(); askIfDue() }
        }
    }
    Box(
        Modifier.fillMaxSize().pointerInput(Unit) {
            // any touch on an open page starts the two minutes (and the ten minutes of a kept place) again
            awaitPointerEventScope {
                while (true) {
                    awaitPointerEvent(PointerEventPass.Initial)
                    if (lock.isOpen(SystemClock.elapsedRealtime())) {
                        lock.touch(SystemClock.elapsedRealtime())
                        WalletPlaces.touch()
                    }
                }
            }
        },
    ) {
        if (open) {
            WalletOpen(leave, onSeen)
        } else {
            val phase = tick.let { flow.phase }
            LockedPage(
                onBack = leave,
                noLock = phase == LockPhase.NO_LOCK,
                kept = remember(tick) { WalletPlaces.hasTyping() },
                onUnlock = { if (flow.tap()) { changed(); gate() } },
            )
        }
    }
}

/**
 * Behind the lock: the list's outline, blurred, and always a way on and a way back. While the phone's check is up it covers the
 * page; once it was backed out of, the unlock button brings it back. Without a screen lock the page says so and opens the phone's
 * screen-lock settings instead. [kept]: something typed is waiting behind the lock.
 */
@Composable
private fun LockedPage(onBack: () -> Unit, noLock: Boolean, kept: Boolean, onUnlock: () -> Unit) {
    val c = Kelo.colors
    val context = LocalContext.current
    if (!chat.kelo.app.ui.LocalInPushHost.current) androidx.activity.compose.BackHandler(onBack = onBack)
    Column(Modifier.fillMaxSize().background(c.bg).safeDrawingPadding()) {
        BackTitleBar(stringResource(R.string.wallet_title), onBack)
        Box(Modifier.fillMaxWidth().weight(1f)) {
            Column(
                Modifier.fillMaxWidth().padding(16.dp).blur(10.dp).graphicsLayer { alpha = 0.55f },
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                listOf(CardBrand.VISA, CardBrand.MASTERCARD, CardBrand.OTHER).forEach { b ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        CardFaceThumb(b, "0000")
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Box(Modifier.size(width = 140.dp, height = 12.dp).clip(RoundedCornerShape(6.dp)).background(c.line))
                            Box(Modifier.size(width = 90.dp, height = 10.dp).clip(RoundedCornerShape(5.dp)).background(c.line))
                        }
                    }
                }
            }
            Column(
                Modifier.align(Alignment.Center).fillMaxWidth().padding(horizontal = 24.dp)
                    .clip(RoundedCornerShape(20.dp)).background(c.card).padding(horizontal = 20.dp, vertical = 22.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Icon(KeloIcons.Lock, contentDescription = null, tint = c.accent, modifier = Modifier.size(28.dp))
                Text(
                    stringResource(if (noLock) R.string.wallet_lock_no_lock_title else R.string.wallet_lock_title),
                    style = MaterialTheme.typography.titleMedium, color = c.ink, textAlign = TextAlign.Center,
                )
                Text(
                    stringResource(if (noLock) R.string.wallet_lock_no_lock_body else R.string.wallet_lock_body),
                    style = MaterialTheme.typography.bodyMedium, color = c.muted, textAlign = TextAlign.Center,
                )
                if (kept && !noLock) {
                    Text(
                        stringResource(R.string.wallet_lock_kept),
                        style = MaterialTheme.typography.bodySmall, color = c.ink, textAlign = TextAlign.Center,
                        modifier = Modifier.clip(RoundedCornerShape(12.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
                    )
                }
                Spacer(Modifier.height(4.dp))
                if (noLock) {
                    PrimaryButton(stringResource(R.string.wallet_lock_open_settings), onClick = {
                        runCatching { context.startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) }
                            .onFailure { runCatching { context.startActivity(Intent(Settings.ACTION_SETTINGS)) } }
                    }, modifier = Modifier.widthIn(max = 280.dp))
                } else {
                    PrimaryButton(stringResource(R.string.wallet_lock_unlock), onClick = onUnlock, modifier = Modifier.widthIn(max = 280.dp))
                }
                SecondaryButton(stringResource(R.string.wallet_lock_back), onClick = onBack, modifier = Modifier.fillMaxWidth().widthIn(max = 280.dp))
            }
        }
    }
}

/** Everything behind the lock. The data lives only as long as the pages are open. */
@Composable
private fun WalletOpen(onBack: () -> Unit, onSeen: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val key = remember { mutableStateOf<String?>(null) }
    val auth = rememberCardAuthenticator()
    var info by remember { mutableStateOf<WalletInfo?>(null) }
    var notices by remember { mutableStateOf<List<WalletPayment>>(emptyList()) }
    var failed by remember { mutableStateOf("") }
    // the page that was open before the lock (or the list)
    var view by remember { mutableStateOf(WalletPlaces.view()) }
    fun go(v: WalletView) { view = v; WalletPlaces.go(v) }

    fun calls() = WalletCalls(sessions.requireApi())
    suspend fun loadNow() {
        try {
            // an older server's wallet has no addresses in it: they are asked for apart (none at all from a server without them)
            info = calls().info().let { w ->
                if (w.shipping != null) w else w.copy(shipping = runCatching { ShippingCalls(sessions.requireApi()).list() }.getOrNull())
            }
            key.value = auth.key.keyId()
            failed = ""
        } catch (e: ApiError) {
            failed = e.detail
        }
    }
    val load: () -> Unit = { scope.launch { loadNow() } }
    LaunchedEffect(Unit) {
        loadNow()
        // automatic payments the owner has not looked at: shown at the top of the list once, then marked as seen
        runCatching { calls().notifications() }.onSuccess { n ->
            notices = n.items.filter { !it.seen }
            val top = n.items.maxOfOrNull { it.id }
            if (n.unseen > 0 && top != null) runCatching { calls().markSeen(top) }.onSuccess { onSeen() }
        }
    }

    val v = info
    if (v == null) {
        SubPage(stringResource(R.string.wallet_title), onBack) {
            if (failed.isNotEmpty()) chat.kelo.app.ui.components.ErrorText(failed, small = true)
            else SkeletonRows(3)
        }
        return
    }
    val toList = { go(WalletView.List) }
    fun back() {
        go(
            when (val now = view) {
                is WalletView.Reenter -> WalletView.Card(now.id)
                else -> WalletView.List
            },
        )
    }
    PushHost(
        state = view,
        depth = { it.depth() },
        back = when (val now = view) {
            WalletView.List -> null
            is WalletView.Reenter -> WalletView.Card(now.id)
            else -> WalletView.List
        },
        onBack = ::back,
        modifier = Modifier.fillMaxSize(),
    ) { shown ->
        when (shown) {
            WalletView.List -> WalletList(v, key.value, notices, onBack, go = ::go, onChanged = load)
            WalletView.Add -> AddCardPage(onBack = toList, onSaved = { load(); toList() })
            is WalletView.Card -> v.cards.find { it.id == shown.id }?.let { card ->
                CardPage(card, key.value, onBack = toList, go = ::go, onChanged = load, onDeleted = { load(); toList() }, cards = v.cards)
            } ?: WalletList(v, key.value, notices, onBack, go = ::go)
            is WalletView.Reenter -> v.cards.find { it.id == shown.id }?.let { card ->
                ReenterPage(card, shown.autoPay, onBack = { go(WalletView.Card(card.id)) }, onSaved = { load(); go(WalletView.Card(card.id)) })
            } ?: WalletList(v, key.value, notices, onBack, go = ::go)
            WalletView.Limits -> LimitsPage(v.paused, onBack = toList, onPaused = load)
            WalletView.Payments -> PaymentsPage(onBack = toList)
            is WalletView.ShippingEdit -> {
                val list = v.shipping.orEmpty()
                val p = shown.id?.let { id -> list.find { it.id == id } }
                if (shown.id != null && p == null) WalletList(v, key.value, notices, onBack, go = ::go, onChanged = load)
                else ShippingEditPage(p, firstOne = list.isEmpty(), onBack = toList, onSaved = { load(); toList() }, onDeleted = { load(); toList() })
            }
        }
    }
}

/** A short muted line under a field or a card. */
@Composable
internal fun Note(text: String) = MutedText(text, small = true, modifier = Modifier.padding(horizontal = 4.dp))

/** A rounded tag ("자동결제", "만료됨"). */
@Composable
internal fun CardTag(text: String, tone: TagTone = TagTone.PLAIN) {
    val c = Kelo.colors
    val (bg, fg) = when (tone) {
        TagTone.PLAIN -> c.bubble to c.muted
        TagTone.ACCENT -> c.mine to c.ink
        TagTone.DANGER -> c.dangerSoft to c.dangerInk
    }
    Text(
        text, color = fg, style = MaterialTheme.typography.labelSmall, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(bg).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

internal enum class TagTone { PLAIN, ACCENT, DANGER }

/** Text of an amount in a currency ("30,000원", "25.00 USD"), digits and separators in the app's language. */
@Composable
internal fun moneyText(amount: Double, currency: String): String {
    val n = Money.number(amount, currency, chat.kelo.app.my.appLocale())
    return if (currency.equals("KRW", ignoreCase = true)) stringResource(R.string.wallet_money_krw, n) else stringResource(R.string.wallet_money, n, currency.uppercase())
}

/** Brand names are product names, the same in every language (not translated). */
internal fun brandShort(b: CardBrand): String = when (b) {
    CardBrand.VISA -> "VISA"
    CardBrand.MASTERCARD -> "MC"
    CardBrand.AMEX -> "AMEX"
    CardBrand.JCB -> "JCB"
    CardBrand.DISCOVER -> "DISC"
    CardBrand.UNIONPAY -> "UP"
    CardBrand.DINERS -> "DC"
    CardBrand.OTHER -> "•••"
}

internal fun brandName(b: CardBrand): String? = when (b) {
    CardBrand.VISA -> "Visa"
    CardBrand.MASTERCARD -> "Mastercard"
    CardBrand.AMEX -> "American Express"
    CardBrand.JCB -> "JCB"
    CardBrand.DISCOVER -> "Discover"
    CardBrand.UNIONPAY -> "UnionPay"
    CardBrand.DINERS -> "Diners Club"
    CardBrand.OTHER -> null
}

/** Whether a card can pay from this phone, and how. */
internal enum class CardStatus { USABLE, AUTO_ONLY, NOT_ON_PHONE, EXPIRED, UNFINISHED }

internal object CardStatusRules {
    /**
     * EXPIRED first; a card whose phone copy this phone cannot open is NOT_ON_PHONE, unless the server's auto-pay copy still pays
     * with it (AUTO_ONLY: it works until auto-pay is turned off); a card with neither copy is UNFINISHED.
     */
    fun of(card: WalletCard, keyId: String?): CardStatus = when {
        card.expired -> CardStatus.EXPIRED
        CardBlob.ours(card, keyId) -> CardStatus.USABLE
        card.serverCopy -> CardStatus.AUTO_ONLY
        card.e2e -> CardStatus.NOT_ON_PHONE
        else -> CardStatus.UNFINISHED
    }
}
