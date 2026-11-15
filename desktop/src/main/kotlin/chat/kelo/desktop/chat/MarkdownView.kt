package chat.kelo.desktop.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.chat.Markdown
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.KeloColors

/**
 * A reply's Markdown, drawn from the shared parser (chat.kelo.app.chat.Markdown: paragraphs, headings, lists, quotes, code,
 * tables, rules; bold, italic, code and links inline). Links open in the system browser; only http(s) ones are links at all.
 */
@Composable
fun MarkdownView(text: String, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val blocks = remember(text) { Markdown.blocks(text) }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        blocks.forEach { b -> Block(b, c) }
    }
}

@Composable
private fun Block(b: Markdown.Block, c: KeloColors) {
    val body = MaterialTheme.typography.bodyLarge
    when (b) {
        is Markdown.Block.Para -> Text(inline(b.text, c), style = body, color = c.ink)
        is Markdown.Block.Heading -> Text(
            inline(b.text, c), color = c.ink,
            style = when (b.level) { 1 -> MaterialTheme.typography.titleLarge; 2 -> MaterialTheme.typography.titleMedium; else -> MaterialTheme.typography.titleSmall },
        )
        is Markdown.Block.Code -> Box(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bg).border(1.dp, c.line, RoundedCornerShape(10.dp))
                .horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Text(b.text, fontFamily = FontFamily.Monospace, fontSize = 13.sp, lineHeight = 19.sp, color = c.ink)
        }
        is Markdown.Block.Items -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            b.entries.forEach { e ->
                Row(Modifier.padding(start = (e.depth * 18).dp)) {
                    Text(if (e.ordered) "${e.number}." else if (e.depth == 0) "•" else "◦", style = body, color = c.muted,
                        modifier = Modifier.widthIn(min = 18.dp).padding(end = 6.dp))
                    Text(inline(e.text, c), style = body, color = c.ink, modifier = Modifier.weight(1f))
                }
            }
        }
        is Markdown.Block.Quote -> Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(3.dp).fillMaxHeight().background(c.line))
            Column(Modifier.padding(start = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { b.blocks.forEach { Block(it, c) } }
        }
        is Markdown.Block.Table -> Box(Modifier.horizontalScroll(rememberScrollState())) {
            Column(Modifier.border(1.dp, c.line, RoundedCornerShape(8.dp)).clip(RoundedCornerShape(8.dp))) {
                TableRow(b.header, b.align, header = true, c = c)
                b.rows.forEach { TableRow(it, b.align, header = false, c = c) }
            }
        }
        Markdown.Block.Rule -> Box(Modifier.fillMaxWidth().height(1.dp).background(c.line))
    }
}

@Composable
private fun TableRow(cells: List<String>, align: List<Markdown.Align>, header: Boolean, c: KeloColors) {
    Row(Modifier.background(if (header) c.bg else c.card)) {
        cells.forEachIndexed { i, cell ->
            Text(
                inline(cell, c), color = c.ink, style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (header) FontWeight.SemiBold else null,
                textAlign = when (align.getOrNull(i)) { Markdown.Align.CENTER -> TextAlign.Center; Markdown.Align.END -> TextAlign.End; else -> TextAlign.Start },
                modifier = Modifier.widthIn(min = 80.dp, max = 280.dp).padding(horizontal = 10.dp, vertical = 6.dp),
            )
        }
    }
}

/** The inline marks of one line of text. */
fun inline(text: String, c: KeloColors): AnnotatedString = buildAnnotatedString {
    for (s in Markdown.inline(text)) {
        when (s) {
            is Markdown.Span.Plain -> append(s.text)
            is Markdown.Span.Bold -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(s.text) }
            is Markdown.Span.Italic -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { append(s.text) }
            is Markdown.Span.Code -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = c.bg, fontSize = 14.sp)) { append(s.text) }
            is Markdown.Span.Link ->
                if (Markdown.isWebUrl(s.url)) {
                    withLink(LinkAnnotation.Url(s.url, TextLinkStyles(SpanStyle(color = c.accent, textDecoration = TextDecoration.Underline)))) { append(s.text) }
                } else append(s.text)
        }
    }
}
