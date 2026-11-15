package chat.kelo.desktop.i18n

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import chat.kelo.app.RNames
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * The app's text in one language, read from src/main/resources/i18n/<code>.json (made from the Android app's strings by
 * tools/sync_strings.py). A line a language lacks falls back to English, as Android falls back to values/.
 *
 * Formatting is Android's (and Java's): "%1$s", "%2$d". Plurals pick their form by the CLDR rules of the language ([PluralRules]).
 */
class Strings private constructor(val code: String, private val own: Table, private val fallback: Table?) {

    /** R.string.x */
    fun get(id: Int, vararg args: Any?): String {
        val name = RNames.string[id]
        val text = own.s[name] ?: fallback?.s?.get(name) ?: name
        return format(text, args)
    }

    /** R.plurals.x for [count]; the args are formatted into the chosen form (usually the count itself again). */
    fun plural(id: Int, count: Int, vararg args: Any?): String {
        val name = RNames.plurals[id]
        val forms = own.p[name] ?: fallback?.p?.get(name) ?: return name
        val form = PluralRules.category(code, count)
        val text = forms[form] ?: forms["other"] ?: forms.values.firstOrNull() ?: name
        return format(text, args)
    }

    /** R.array.x */
    fun array(id: Int): List<String> {
        val name = RNames.array[id]
        return own.a[name] ?: fallback?.a?.get(name) ?: emptyList()
    }

    private fun format(text: String, args: Array<out Any?>): String =
        if (args.isEmpty()) text else try { String.format(locale, text, *args) } catch (e: java.util.IllegalFormatException) { text }

    /** The Java locale for number and date formatting. */
    val locale: Locale = Locale.forLanguageTag(code)

    internal class Table(val s: Map<String, String>, val p: Map<String, Map<String, String>>, val a: Map<String, List<String>>)

    companion object {
        private val tables = ConcurrentHashMap<String, Table>()

        /** The strings for one of chat.kelo.app.core.Languages.ALL (an unknown code gets English). */
        fun of(code: String): Strings {
            val en = table("en") ?: Table(emptyMap(), emptyMap(), emptyMap())
            if (code == "en") return Strings("en", en, null)
            val own = table(code) ?: return Strings("en", en, null)
            return Strings(code, own, en)
        }

        private fun table(code: String): Table? = tables[code] ?: load(code)?.also { tables[code] = it }

        private fun load(code: String): Table? {
            val text = Strings::class.java.getResourceAsStream("/i18n/$code.json")?.use { it.readBytes().toString(Charsets.UTF_8) } ?: return null
            val o = Json.parseToJsonElement(text) as JsonObject
            fun obj(k: String) = o[k] as? JsonObject ?: JsonObject(emptyMap())
            val s = obj("s").mapValues { (it.value as JsonPrimitive).content }
            val p = obj("p").mapValues { e -> (e.value as JsonObject).mapValues { (it.value as JsonPrimitive).content } }
            val a = obj("a").mapValues { e -> (e.value as JsonArray).map { (it as JsonPrimitive).content } }
            return Table(s, p, a)
        }
    }
}

/** The strings of the language the app is shown in (set by the app root). */
val LocalStrings = staticCompositionLocalOf { Strings.of("en") }

/** Android's stringResource(R.string.x, args…), for the desktop. */
@Composable
@ReadOnlyComposable
fun stringResource(id: Int, vararg args: Any?): String = LocalStrings.current.get(id, *args)

/** Android's pluralStringResource(R.plurals.x, count, args…), for the desktop. */
@Composable
@ReadOnlyComposable
fun pluralStringResource(id: Int, count: Int, vararg args: Any?): String = LocalStrings.current.plural(id, count, *args)

@Composable
@ReadOnlyComposable
fun stringArrayResource(id: Int): List<String> = LocalStrings.current.array(id)

/**
 * CLDR cardinal plural categories for whole numbers, for the app's 20 languages (the forms android/tools/strings_res.py PLURALS
 * lists for each). Only integers are counted in the app, so the decimal parts of the rules are left out.
 */
object PluralRules {
    fun category(code: String, n: Int): String {
        val lang = code.substringBefore('-').lowercase(Locale.ROOT)
        val i = kotlin.math.abs(n)
        val mod10 = i % 10
        val mod100 = i % 100
        return when (lang) {
            "ko", "ja", "zh", "id", "vi", "th" -> "other"
            "en", "de", "nl", "tr" -> if (i == 1) "one" else "other"
            "hi" -> if (i == 0 || i == 1) "one" else "other"
            "fr" -> when {
                i == 0 || i == 1 -> "one"
                i != 0 && i % 1_000_000 == 0 -> "many"
                else -> "other"
            }
            "es", "it", "pt" -> when {
                i == 1 && lang != "pt" -> "one"
                lang == "pt" && (i == 0 || i == 1) -> "one"
                i != 0 && i % 1_000_000 == 0 -> "many"
                else -> "other"
            }
            "ru", "uk" -> when {
                mod10 == 1 && mod100 != 11 -> "one"
                mod10 in 2..4 && mod100 !in 12..14 -> "few"
                else -> "many"
            }
            "pl" -> when {
                i == 1 -> "one"
                mod10 in 2..4 && mod100 !in 12..14 -> "few"
                else -> "many"
            }
            "ar" -> when {
                i == 0 -> "zero"
                i == 1 -> "one"
                i == 2 -> "two"
                mod100 in 3..10 -> "few"
                mod100 in 11..99 -> "many"
                else -> "other"
            }
            else -> if (i == 1) "one" else "other"
        }
    }
}
