package chat.kelo.desktop.pages.my.ops

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.wallet.CardBrand

// From android/kelo .../wallet/CardFace.kt and WalletPage.kt (brandShort / brandName). Keep in step with them.

private val CardInk = Color(0xFFFFFFFF)

/** The two colours (ARGB) of a brand's face; both stops keep white text at 4.5:1 or more. */
internal fun cardPalette(brand: CardBrand): Pair<Long, Long> = when (brand) {
    CardBrand.VISA -> 0xFF1C2E8C to 0xFF3D5AD8
    CardBrand.MASTERCARD -> 0xFF3A1F1A to 0xFFB34A16
    CardBrand.AMEX -> 0xFF0E5E7A to 0xFF1C7A8C
    CardBrand.JCB -> 0xFF0F5A3A to 0xFF237A4C
    CardBrand.DISCOVER -> 0xFF6B3A0A to 0xFFA85C0C
    CardBrand.UNIONPAY -> 0xFF6E0F1E to 0xFFC62F3F
    CardBrand.DINERS -> 0xFF2B3440 to 0xFF5E6B7C
    CardBrand.OTHER -> 0xFF3B3B46 to 0xFF6A6A7A
}

/** Brand names are product names, the same in every language. */
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

/** A small card face for a list row. */
@Composable
internal fun CardFaceThumb(brand: CardBrand, last4: String, width: Dp = 56.dp, dimmed: Boolean = false) {
    val (a, b) = cardPalette(brand)
    Box(
        Modifier.size(width = width, height = width * 0.63f).clip(RoundedCornerShape(6.dp))
            .background(Brush.linearGradient(listOf(Color(a), Color(b))))
            .then(if (dimmed) Modifier.background(CardInk.copy(alpha = 0.45f)) else Modifier)
            .padding(horizontal = 5.dp, vertical = 4.dp),
    ) {
        Text(brandShort(brand), color = CardInk, fontSize = 8.sp, fontWeight = FontWeight.Bold, maxLines = 1, modifier = Modifier.align(Alignment.TopStart))
        Text(last4.takeLast(4), color = CardInk, fontSize = 10.sp, fontFamily = FontFamily.Monospace, maxLines = 1, modifier = Modifier.align(Alignment.BottomEnd))
    }
}

/** The large face at the top of a card's page: brand, number, holder and expiry. */
@Composable
internal fun CardFaceLarge(brand: CardBrand, number: String, holder: String, expiry: String, modifier: Modifier = Modifier) {
    val (a, b) = cardPalette(brand)
    Column(
        modifier.fillMaxWidth().aspectRatio(1.586f).clip(RoundedCornerShape(16.dp))
            .background(Brush.linearGradient(listOf(Color(a), Color(b)))).padding(18.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(width = 34.dp, height = 24.dp).clip(RoundedCornerShape(5.dp)).background(CardInk.copy(alpha = 0.35f)))
            Spacer(Modifier.weight(1f))
            Text(brandName(brand) ?: "", color = CardInk, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.weight(1f))
        Text(number, color = CardInk, fontFamily = FontFamily.Monospace, fontSize = 20.sp, letterSpacing = 1.sp, maxLines = 1)
        Spacer(Modifier.size(10.dp))
        Row(verticalAlignment = Alignment.Bottom) {
            Text(holder.ifBlank { " " }.uppercase(), color = CardInk.copy(alpha = 0.9f), style = MaterialTheme.typography.bodySmall,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(expiry, color = CardInk.copy(alpha = 0.9f), style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
        }
    }
}
