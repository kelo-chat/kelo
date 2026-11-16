package chat.kelo.app.shell

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull

/*
 * The screen a Nest surface sends (docs/nest-ui.md Part 1, "GET .../data?surface=..."), read into plain values the renderer lays out.
 * Everything here is lenient: a field the app does not know is ignored, a part of an unknown type becomes one "update the app" row,
 * and the limits of Part 2 are applied again (the server enforces them, the app never trusts that). Texts are plain text; nothing
 * here reads HTML, Markdown or a link.
 */

/** The limits the app keeps whatever the server sends. */
object NestLimits {
    const val MAX_COMPONENTS = 60     // in one screen, nested ones counted
    const val MAX_CARD_DEPTH = 3
    const val MAX_CHILDREN = 20
    const val MAX_ROWS = 100
    const val MAX_POINTS = 60
    const val MAX_FIELDS = 12
    const val MAX_OPTIONS = 30
    const val MAX_TEXT = 600
    const val MAX_IMAGE_DP = 400
    const val DEFAULT_IMAGE_DP = 160
}

/** A button's, form's, card's or row's action (`action` in the screen): what to POST to run it. */
data class NestAction(
    val id: String,
    val type: String = "",
    val confirm: Boolean = false,
    val label: String = "",
    /** The row's key and data id (a list row only): they say which row, the app never sends the row's values. */
    val item: String = "",
    val data: String = "",
    /** Only for `open_url`. */
    val url: String = "",
) {
    /** One request at a time per button: the key a running request is remembered by. */
    val key: String get() = "$id/$item"

    /** A row action that removes something: shown as a trailing menu item and asked about first. */
    val removes: Boolean get() = type == "store_delete"
}

data class NestRow(
    val key: String, val title: String, val subtitle: String = "", val trailing: String = "", val icon: String = "",
    val image: String = "", val action: NestAction? = null,
)

data class NestOption(val value: String, val label: String)

data class NestField(
    val id: String, val type: String, val label: String, val required: Boolean = false, val placeholder: String = "",
    val options: List<NestOption> = emptyList(), val min: Double? = null, val max: Double? = null, val maxLength: Int? = null,
    /** The default as the field holds it: a string, or "true" / "false" for a toggle. */
    val default: String = "",
)

data class NestPoint(val x: String, val y: Double)

sealed interface NestComponent {
    data class Header(val text: String, val subtitle: String = "") : NestComponent
    data class Text(val text: String, val style: String = "body") : NestComponent
    data class Stat(val label: String, val value: String, val unit: String = "", val caption: String = "", val icon: String = "") : NestComponent
    data class ListView(val rows: List<NestRow>, val emptyText: String? = null, val emptyIcon: String = "") : NestComponent
    data class Card(val title: String, val subtitle: String, val children: List<NestComponent>, val action: NestAction?) : NestComponent
    data class Chart(val kind: String, val label: String, val points: List<NestPoint>) : NestComponent
    data class Image(val src: String, val alt: String, val height: Int) : NestComponent
    data class Button(val label: String, val style: String, val action: NestAction) : NestComponent
    data class Form(val fields: List<NestField>, val submitLabel: String, val submit: NestAction) : NestComponent
    data class Empty(val text: String, val icon: String = "", val action: NestAction? = null) : NestComponent

    /** A part of a type this app does not know (or one the server marked `unsupported`): one row. [text] blank = the app's own words. */
    data class Unsupported(val text: String = "") : NestComponent
}

/** The screen: a [title] (the surface's), the [components] top to bottom, and the data [errors] to show once as a quiet notice. */
data class NestScreen(val title: String = "", val components: List<NestComponent> = emptyList(), val errors: List<String> = emptyList())

object NestScreenParse {
    fun screen(root: JsonElement): NestScreen {
        val o = root as? JsonObject ?: return NestScreen()
        val budget = Budget(NestLimits.MAX_COMPONENTS)
        val comps = parseList(o["components"], 0, budget)
        val errors = (o["errors"] as? JsonArray).orEmpty().mapNotNull { e ->
            (e as? JsonObject)?.str("error")?.takeIf { it.isNotBlank() }
        }.distinct()
        return NestScreen(o.str("title"), comps, errors)
    }

    /** How many components may still be drawn on this screen (nested ones count). */
    private class Budget(var left: Int) {
        fun take(): Boolean = if (left > 0) { left--; true } else false
    }

    private fun parseList(el: JsonElement?, depth: Int, budget: Budget): List<NestComponent> {
        val out = ArrayList<NestComponent>()
        for (e in (el as? JsonArray).orEmpty().take(if (depth == 0) Int.MAX_VALUE else NestLimits.MAX_CHILDREN)) {
            val c = e as? JsonObject ?: continue
            if (!budget.take()) break
            out += one(c, depth, budget)
        }
        return out
    }

    private fun one(c: JsonObject, depth: Int, budget: Budget): NestComponent = when (c.str("type")) {
        "header" -> NestComponent.Header(c.text("text"), c.text("subtitle"))
        "text" -> NestComponent.Text(c.text("text"), c.str("style").takeIf { it in TEXT_STYLES } ?: "body")
        "stat" -> NestComponent.Stat(c.text("label"), c.text("value"), c.text("unit"), c.text("caption"), c.str("icon"))
        "list" -> {
            val rows = (c["rows"] as? JsonArray).orEmpty().take(NestLimits.MAX_ROWS).mapNotNull { r ->
                val ro = r as? JsonObject ?: return@mapNotNull null
                NestRow(ro.str("key"), ro.text("title"), ro.text("subtitle"), ro.text("trailing"), ro.str("icon"), ro.str("image"), action(ro["action"]))
            }
            val empty = c["empty"] as? JsonObject
            NestComponent.ListView(rows, empty?.text("text")?.takeIf { it.isNotBlank() }, empty?.str("icon").orEmpty())
        }
        "card" ->
            // cards nest at most 3 deep: a card in the fourth level is shown as an unsupported part
            if (depth >= NestLimits.MAX_CARD_DEPTH) NestComponent.Unsupported()
            else NestComponent.Card(c.text("title"), c.text("subtitle"), parseList(c["children"], depth + 1, budget), action(c["action"]))
        "chart" -> {
            val points = (c["points"] as? JsonArray).orEmpty().take(NestLimits.MAX_POINTS).mapNotNull { p ->
                val po = p as? JsonObject ?: return@mapNotNull null
                val y = (po["y"] as? JsonPrimitive)?.doubleOrNull?.takeIf { it.isFinite() } ?: return@mapNotNull null
                NestPoint(po.str("x"), y)
            }
            NestComponent.Chart(if (c.str("kind") == "bar") "bar" else "line", c.text("label"), points)
        }
        "image" -> NestComponent.Image(
            c.str("src"), c.text("alt"),
            (c["height"] as? JsonPrimitive)?.intOrNull?.coerceIn(40, NestLimits.MAX_IMAGE_DP) ?: NestLimits.DEFAULT_IMAGE_DP,
        )
        "button" -> action(c["action"])?.let { NestComponent.Button(c.text("label"), c.str("style").takeIf { it in BUTTON_STYLES } ?: "primary", it) }
            ?: NestComponent.Unsupported()
        "form" -> form(c)
        "empty" -> NestComponent.Empty(c.text("text"), c.str("icon"), action(c["action"]))
        "unsupported" -> NestComponent.Unsupported(c.text("text"))
        else -> NestComponent.Unsupported()
    }

    private fun form(c: JsonObject): NestComponent {
        val submit = c["submit"] as? JsonObject
        val act = action(submit?.get("action")) ?: return NestComponent.Unsupported()
        val fields = (c["fields"] as? JsonArray).orEmpty().take(NestLimits.MAX_FIELDS).mapNotNull { f ->
            val fo = f as? JsonObject ?: return@mapNotNull null
            val id = fo.str("id").ifEmpty { return@mapNotNull null }
            val type = fo.str("type").takeIf { it in FIELD_TYPES } ?: return@mapNotNull null
            val options = (fo["options"] as? JsonArray).orEmpty().take(NestLimits.MAX_OPTIONS).mapNotNull { op ->
                val oo = op as? JsonObject ?: return@mapNotNull null
                NestOption(oo.str("value"), oo.str("label").ifEmpty { oo.str("value") })
            }
            NestField(
                id, type, fo.text("label"), (fo["required"] as? JsonPrimitive)?.booleanOrNull == true, fo.text("placeholder"), options,
                (fo["min"] as? JsonPrimitive)?.doubleOrNull, (fo["max"] as? JsonPrimitive)?.doubleOrNull,
                (fo["max_length"] as? JsonPrimitive)?.intOrNull?.takeIf { it > 0 }, fo.str("default"),
            )
        }
        return if (fields.isEmpty()) NestComponent.Unsupported() else NestComponent.Form(fields, submit?.text("label").orEmpty(), act)
    }

    fun action(el: JsonElement?): NestAction? {
        val a = el as? JsonObject ?: return null
        val id = a.str("id").ifEmpty { return null }
        return NestAction(id, a.str("type"), (a["confirm"] as? JsonPrimitive)?.booleanOrNull == true, a.text("label"), a.str("item"), a.str("data"), a.str("url"))
    }

    private val TEXT_STYLES = setOf("body", "caption", "title", "label")
    private val BUTTON_STYLES = setOf("primary", "secondary", "danger")
    private val FIELD_TYPES = setOf("text", "multiline", "number", "date", "time", "select", "toggle")

    /** A string value; numbers and booleans as they are written, nothing for null, objects and lists. */
    private fun JsonObject.str(k: String): String {
        val p = this[k] as? JsonPrimitive ?: return ""
        return if (p is JsonNull) "" else p.content
    }

    /** A text shown to the reader: plain, at most 600 characters. */
    private fun JsonObject.text(k: String): String = str(k).take(NestLimits.MAX_TEXT)
}

/** The server's answer to POST .../actions/{id} (HTTP 200); an error is an [chat.kelo.app.core.ApiError]. */
sealed interface NestActionResult {
    data class Done(val message: String, val refresh: Boolean) : NestActionResult
    /** [sends]: what goes out, as plain "key: value" pairs. */
    data class Confirm(val token: String, val title: String, val detail: String, val sends: List<Pair<String, String>>, val expiresIn: Int) : NestActionResult
    data class Open(val url: String) : NestActionResult
    data class Started(val message: String, val turnId: String) : NestActionResult
    data object Unknown : NestActionResult

    companion object {
        fun parse(root: JsonElement): NestActionResult {
            val o = root as? JsonObject ?: return Unknown
            fun s(k: String) = (o[k] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content.orEmpty()
            return when (s("status")) {
                "done" -> Done(s("message"), (o["refresh"] as? JsonPrimitive)?.booleanOrNull == true)
                "confirm" -> Confirm(
                    s("token"), s("title"), s("detail"),
                    (o["sends"] as? JsonObject).orEmpty().entries.map { (k, v) -> k to flat(v) },
                    (o["expires_in"] as? JsonPrimitive)?.intOrNull ?: 0,
                )
                "open" -> Open(s("url"))
                "started" -> Started(s("message"), s("turn_id"))
                else -> Unknown
            }
        }

        /** A value of `sends` as a plain line: text as it is, a number or true/false as written, a list or object as short JSON. */
        private fun flat(v: JsonElement): String = when (v) {
            is JsonNull -> ""
            is JsonPrimitive -> v.content
            else -> v.toString()
        }.take(NestLimits.MAX_TEXT)
    }
}

/** Reading and checking what a form holds, before it is sent. */
object NestFormRules {
    /** The ids of required fields with no value (a toggle always has one). */
    fun missing(fields: List<NestField>, values: Map<String, String>): List<String> =
        fields.filter { f -> f.required && f.type != "toggle" && values[f.id].orEmpty().isBlank() }.map { it.id } +
            fields.filter { f -> f.type == "number" && values[f.id].orEmpty().isNotBlank() && !numberOk(f, values[f.id].orEmpty()) }.map { it.id }

    private fun numberOk(f: NestField, text: String): Boolean {
        val n = text.trim().replace(',', '.').toDoubleOrNull() ?: return false
        return (f.min == null || n >= f.min) && (f.max == null || n <= f.max)
    }

    /** The values to send: number as a number, toggle as true / false, everything else as the text; empty optional fields are left out. */
    fun payload(fields: List<NestField>, values: Map<String, String>): JsonObject {
        val m = LinkedHashMap<String, JsonElement>()
        for (f in fields) {
            val raw = values[f.id] ?: f.default
            when (f.type) {
                "toggle" -> m[f.id] = JsonPrimitive(raw == "true")
                "number" -> raw.trim().replace(',', '.').toDoubleOrNull()?.let { n ->
                    m[f.id] = if (n == Math.floor(n) && Math.abs(n) < 1e15) JsonPrimitive(n.toLong()) else JsonPrimitive(n)
                }
                else -> if (raw.isNotEmpty()) m[f.id] = JsonPrimitive(raw.take(f.maxLength ?: NestLimits.MAX_TEXT))
            }
        }
        return JsonObject(m)
    }

    /** What a field holds before the reader touches it. */
    fun initial(fields: List<NestField>): Map<String, String> = fields.associate { f ->
        f.id to when {
            f.type == "toggle" -> if (f.default == "true") "true" else "false"
            f.type == "select" && f.default.isBlank() && f.required -> f.options.firstOrNull()?.value.orEmpty()
            else -> f.default
        }
    }
}

/** A system browser may open an https (or http) address only; nothing else leaves the app from a screen. */
fun nestOpenable(url: String): Boolean {
    val u = url.trim().lowercase()
    return (u.startsWith("https://") || u.startsWith("http://")) && !u.contains(' ')
}
