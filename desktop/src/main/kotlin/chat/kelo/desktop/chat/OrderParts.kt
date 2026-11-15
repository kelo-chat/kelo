package chat.kelo.desktop.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.chat.ApprovalCardInfo
import chat.kelo.app.chat.PaymentInfo
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.AddressRules
import chat.kelo.app.wallet.CardBrand
import chat.kelo.app.wallet.OrderItem
import chat.kelo.app.wallet.OrderShipping
import chat.kelo.desktop.i18n.stringResource

/*
 * The one final approval of an order (confirm_purchase), everything at once and in this order: the site; the items (name,
 * options, × quantity, price) or the summary; where it goes (label · name · phone, then the address lines in its country's
 * layout); what pays (the card's nickname ••last 4, or the site); the coupon; and the total, large. The same block on the chat's
 * card, the chat's waiting questions and the 작업 page (android/kelo .../chat/PurchaseUi.kt draws the phone's). Every line wraps.
 */

/** Whether [p] carries the order's parts (an older server sends only the site, the summary and the amount). */
internal fun PaymentInfo.isOrder(): Boolean = items.isNotEmpty() || shipping != null || coupon.isNotEmpty()

/** "생활비 카드 ••4242" (the nickname alone when no last 4 is known; the brand when there is no nickname). */
internal fun cardLine(info: ApprovalCardInfo): String {
    val c = info.card
    val name = c.label.ifBlank { chat.kelo.desktop.pages.my.ops.brandName(CardBrand.of(c.brand)) ?: chat.kelo.desktop.pages.my.ops.brandShort(CardBrand.of(c.brand)) }
    val last4 = c.last4.filter { it.isDigit() }.takeLast(4)
    return if (last4.isEmpty() || name.endsWith(last4)) name else "$name ••$last4"
}

/** The order block of a payment approval (see the file's note). [card]: the wallet card named for it, if any. */
@Composable
fun OrderDetails(p: PaymentInfo, card: ApprovalCardInfo?) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.bubble).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (p.site.isNotEmpty()) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(KeloIcons.Globe, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
            Text(p.site, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(1f))
        }
        if (p.items.isNotEmpty()) {
            Part(stringResource(R.string.order_items)) { p.items.forEach { ItemRow(it) } }
        } else if (p.summary.isNotEmpty() || p.orderWhat.isNotEmpty()) {
            Text(p.summary.ifEmpty { p.orderWhat }, style = MaterialTheme.typography.bodyMedium, color = c.ink)
        }
        p.shipping?.let { s -> Divider(); ShipTo(s) }
        val pay = card?.let(::cardLine) ?: if (p.isOrder()) stringResource(R.string.order_pay_site) else null
        if (pay != null) {
            Divider()
            Part(stringResource(R.string.order_pay_with)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(KeloIcons.Card, contentDescription = null, tint = c.ink, modifier = Modifier.size(18.dp))
                    Text(pay, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink, modifier = Modifier.weight(1f))
                }
            }
        }
        if (p.coupon.isNotEmpty()) {
            Divider()
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.order_coupon), style = MaterialTheme.typography.labelMedium, color = c.muted)
                Text(p.coupon, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink, textAlign = TextAlign.End, modifier = Modifier.weight(1f))
            }
        }
        Divider()
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(R.string.order_total), style = MaterialTheme.typography.titleSmall, color = c.ink, modifier = Modifier.semantics { heading() })
            Text(
                p.amountText.ifEmpty { stringResource(R.string.wallet_money, p.amount.toString(), p.currency) },
                fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.Bold, color = c.ink, textAlign = TextAlign.End, modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun Divider() = HorizontalDivider(color = Kelo.colors.line)

/** A part of the order with its small heading. */
@Composable
private fun Part(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.labelMedium, color = Kelo.colors.muted, modifier = Modifier.semantics { heading() })
        content()
    }
}

/** "블루 머그 · 색상: 네이비" on the left, "× 2" and the price on the right; long names wrap. */
@Composable
private fun ItemRow(item: OrderItem) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
            Text(item.name, style = MaterialTheme.typography.bodyMedium, color = c.ink)
            if (item.options.isNotEmpty()) Text(item.options, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(1.dp)) {
            Text(stringResource(R.string.order_quantity, item.quantity), style = MaterialTheme.typography.bodySmall, color = c.muted)
            if (item.priceText.isNotEmpty()) Text(item.priceText, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink)
        }
    }
}

/** Where it goes: "집 · 홍길동 · +821012345678", then the address in its country's layout (no name in it). */
@Composable
private fun ShipTo(s: OrderShipping) {
    val c = Kelo.colors
    Part(stringResource(R.string.order_ship_to)) {
        val who = AddressRules.who(s.label, s.name, s.phone)
        if (who.isNotEmpty()) Text(who, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink)
        val lines = AddressRules.lines(s.formatted)
        lines.forEach { Text(it, style = MaterialTheme.typography.bodyMedium, color = c.ink) }
        when {
            s.fromSite -> Text(stringResource(R.string.order_ship_site), style = MaterialTheme.typography.bodySmall, color = c.muted)
            s.source == OrderShipping.SOURCE_ONCE -> Text(stringResource(R.string.order_ship_once), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
    }
}
