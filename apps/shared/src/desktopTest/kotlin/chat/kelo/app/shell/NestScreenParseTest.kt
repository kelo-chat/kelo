package chat.kelo.app.shell

import chat.kelo.app.core.KeloApi
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** The screen the server sends, read into components: every type of docs/nest-ui.md Part 1, the limits and the leniency rules. */
class NestScreenParseTest {
    private fun parse(json: String) = NestScreenParse.screen(KeloApi.readJson(json))

    @Test fun theHabitTrackerScreenIsReadComponentByComponent() {
        val s = parse(NestFixtures.HABITS)
        assertEquals("Habits", s.title)
        assertEquals(listOf("Connect Example Parcels first."), s.errors)
        val c = s.components
        assertEquals(4, c.size)
        assertEquals(NestComponent.Header("Keep the streak going", "2 check-ins so far"), c[0])
        val form = assertIs<NestComponent.Form>(c[1])
        assertEquals(listOf("habit", "note"), form.fields.map { it.id })
        assertEquals(listOf("select", "text"), form.fields.map { it.type })
        assertTrue(form.fields[0].required)
        assertEquals(3, form.fields[0].options.size)
        assertEquals(NestOption("Read", "Read 20 pages"), form.fields[0].options[1])
        assertEquals(200, form.fields[1].maxLength)
        assertEquals("Check in", form.submitLabel)
        assertEquals(NestAction("check_in", "store_add", false, "Check in"), form.submit)
        val list = assertIs<NestComponent.ListView>(c[2])
        assertEquals(listOf("Walk", "Read"), list.rows.map { it.title })
        assertEquals("park", list.rows[0].subtitle)
        assertEquals("2026-11-09", list.rows[0].trailing)
        val remove = list.rows[0].action!!
        assertTrue(remove.removes)
        assertEquals("a1", remove.item)
        assertEquals("checkins", remove.data)
        assertNull(list.emptyText)
        val button = assertIs<NestComponent.Button>(c[3])
        assertEquals("secondary", button.style)
        assertEquals("agent", button.action.type)
    }

    @Test fun theStatCardIsReadWithItsIconAndCaption() {
        val stat = assertIs<NestComponent.Stat>(parse(NestFixtures.CARD).components.single())
        assertEquals("Check-ins", stat.label)
        assertEquals("2", stat.value)
        assertEquals("trophy", stat.icon)
        assertEquals("Last: Walk on 2026-11-09", stat.caption)
    }

    @Test fun everyOtherComponentIsRead() {
        val c = parse(NestFixtures.ALL).components
        assertEquals(listOf("title", "body", "caption", "label", "body"), c.take(5).map { (it as NestComponent.Text).style })
        assertEquals(NestComponent.Stat("Distance", "12.5", "km", "this week", "fitness"), c[5])
        val empty = assertIs<NestComponent.ListView>(c[6])
        assertTrue(empty.rows.isEmpty())
        assertEquals("Nothing here yet", empty.emptyText)
        assertEquals("sparkle", empty.emptyIcon)
        val bars = assertIs<NestComponent.Chart>(c[7])
        assertEquals("bar", bars.kind)
        assertEquals(listOf(3.0, 5.0, 2.0), bars.points.map { it.y })
        assertEquals(listOf("Mon", "Tue", "Wed"), bars.points.map { it.x })
        assertTrue(assertIs<NestComponent.Chart>(c[8]).points.isEmpty())
        val img = assertIs<NestComponent.Image>(c[9])
        assertEquals("/api/nest/extensions/demo/image?src=x", img.src)
        assertEquals(120, img.height)
        assertEquals("A chart picture", img.alt)
        val card = assertIs<NestComponent.Card>(c[10])
        assertEquals("Parcel", card.title)
        assertEquals("open_url", card.action!!.type)
        assertEquals("https://example.com/track", card.action!!.url)
        assertEquals(2, card.children.size)
        val danger = assertIs<NestComponent.Button>(card.children[1])
        assertEquals("danger", danger.style)
        assertTrue(danger.action.confirm)
        val emptyPart = assertIs<NestComponent.Empty>(c[11])
        assertEquals("link", emptyPart.icon)
        assertEquals("connect", emptyPart.action!!.id)
    }

    @Test fun aComponentOfAnUnknownTypeBecomesOneUpdateRowAndTheRestStaysWhole() {
        val c = parse(NestFixtures.ALL).components
        assertEquals(NestComponent.Unsupported(), c[12])      // the app's own words
        assertEquals(NestComponent.Unsupported("Update the app to see this part"), c[13])   // the server's `unsupported`
        assertEquals(14, c.size)
    }

    @Test fun aFieldTheAppDoesNotKnowIsIgnoredAndMissingOnesAreEmpty() {
        val s = parse("""{"extra":1,"components":[{"type":"header","text":"Hi","future_field":[1,2]},{"type":"text"}]}""")
        assertEquals(NestComponent.Header("Hi", ""), s.components[0])
        assertEquals(NestComponent.Text("", "body"), s.components[1])
        assertEquals(NestScreen(), NestScreenParse.screen(JsonPrimitive("nope")))
        assertEquals(NestScreen(), NestScreenParse.screen(JsonObject(emptyMap())))
    }

    // ---- limits

    private fun components(n: Int, extra: String = "") = buildString {
        append("""{"components":[""")
        append((1..n).joinToString(",") { """{"type":"text","text":"t$it"$extra}""" })
        append("]}")
    }

    @Test fun atMostSixtyComponentsInAScreenNestedOnesCounted() {
        assertEquals(60, parse(components(80)).components.size)
        val card = """{"type":"card","title":"c","children":[${(1..30).joinToString(",") { """{"type":"text","text":"x"}""" }}]}"""
        val s = parse("""{"components":[$card,$card,$card]}""")
        // 3 cards, but only 60 components in all: the third card is cut and its children with it
        val drawn = s.components.sumOf { 1 + ((it as? NestComponent.Card)?.children?.size ?: 0) }
        assertTrue(drawn <= 60, "drawn $drawn")
    }

    @Test fun cardsNestAtMostThreeDeep() {
        fun card(d: Int): String = if (d == 0) """{"type":"text","text":"leaf"}""" else """{"type":"card","title":"c$d","children":[${card(d - 1)}]}"""
        val s = parse("""{"components":[${card(5)}]}""")
        var c: NestComponent = s.components.single()
        var depth = 0
        while (c is NestComponent.Card) { depth++; c = c.children.single() }
        assertEquals(3, depth)
        assertIs<NestComponent.Unsupported>(c)   // the fourth level is not drawn
    }

    @Test fun aCardHoldsAtMostTwentyChildren() {
        val kids = (1..30).joinToString(",") { """{"type":"text","text":"x"}""" }
        val card = assertIs<NestComponent.Card>(parse("""{"components":[{"type":"card","children":[$kids]}]}""").components.single())
        assertEquals(20, card.children.size)
    }

    @Test fun listRowsChartPointsFormFieldsAndOptionsAreCapped() {
        val rows = (1..150).joinToString(",") { """{"key":"k$it","title":"r$it"}""" }
        assertEquals(100, assertIs<NestComponent.ListView>(parse("""{"components":[{"type":"list","rows":[$rows]}]}""").components.single()).rows.size)
        val points = (1..90).joinToString(",") { """{"x":"p$it","y":$it}""" }
        assertEquals(60, assertIs<NestComponent.Chart>(parse("""{"components":[{"type":"chart","kind":"line","points":[$points]}]}""").components.single()).points.size)
        val fields = (1..20).joinToString(",") { """{"id":"f$it","type":"text","label":"F"}""" }
        val form = assertIs<NestComponent.Form>(parse("""{"components":[{"type":"form","fields":[$fields],"submit":{"label":"Go","action":{"id":"a","type":"store_add"}}}]}""").components.single())
        assertEquals(12, form.fields.size)
        val opts = (1..50).joinToString(",") { """{"value":"v$it","label":"L$it"}""" }
        val sel = assertIs<NestComponent.Form>(parse("""{"components":[{"type":"form","fields":[{"id":"s","type":"select","label":"S","options":[$opts]}],"submit":{"label":"Go","action":{"id":"a","type":"store_add"}}}]}""").components.single())
        assertEquals(30, sel.fields.single().options.size)
    }

    @Test fun aTextIsCutAt600CharactersAndAnImageHeightStaysBetween40And400() {
        val t = assertIs<NestComponent.Text>(parse("""{"components":[{"type":"text","text":"${"a".repeat(900)}"}]}""").components.single())
        assertEquals(600, t.text.length)
        fun h(v: Int) = assertIs<NestComponent.Image>(parse("""{"components":[{"type":"image","src":"/api/nest/x","alt":"a","height":$v}]}""").components.single()).height
        assertEquals(400, h(5000))
        assertEquals(40, h(1))
        assertEquals(160, assertIs<NestComponent.Image>(parse("""{"components":[{"type":"image","src":"/api/nest/x","alt":"a"}]}""").components.single()).height)
    }

    @Test fun aButtonWithoutAnActionAndAFormWithoutFieldsAreUpdateRows() {
        val c = parse("""{"components":[{"type":"button","label":"x","style":"primary"},{"type":"form","fields":[],"submit":{"label":"Go","action":{"id":"a","type":"x"}}}]}""").components
        assertEquals(listOf(NestComponent.Unsupported(), NestComponent.Unsupported()), c)
    }

    @Test fun errorsAreShownOnceEach() {
        val s = parse("""{"components":[],"errors":[{"data":"a","error":"Same"},{"data":"b","error":"Same"},{"data":"c","error":""}]}""")
        assertEquals(listOf("Same"), s.errors)
    }

    // ---- action answers

    @Test fun everyActionAnswerIsRead() {
        fun r(j: String) = NestActionResult.parse(KeloApi.readJson(j))
        assertEquals(NestActionResult.Done("Logged.", true), r("""{"status":"done","message":"Logged.","refresh":true}"""))
        assertEquals(NestActionResult.Done("", false), r("""{"status":"done"}"""))
        assertEquals(NestActionResult.Open("https://example.com/x"), r("""{"status":"open","url":"https://example.com/x"}"""))
        assertEquals(NestActionResult.Started("Kelo is on it", "t1"), r("""{"status":"started","message":"Kelo is on it","turn_id":"t1"}"""))
        val c = assertIs<NestActionResult.Confirm>(r("""{"status":"confirm","token":"tok","title":"Cancel the parcel?","detail":"Goes to parcels.example","sends":{"id":"p1","force":true,"n":3,"tags":["a","b"]},"expires_in":300}"""))
        assertEquals("tok", c.token)
        assertEquals("Cancel the parcel?", c.title)
        assertEquals(300, c.expiresIn)
        assertEquals(listOf("id" to "p1", "force" to "true", "n" to "3", "tags" to """["a","b"]"""), c.sends)
        assertEquals(NestActionResult.Unknown, r("""{"status":"something_new"}"""))
    }

    // ---- forms

    @Test fun aFormIsCheckedBeforeItIsSentAndSendsTypedValues() {
        val fields = listOf(
            NestField("habit", "select", "Habit", required = true, options = listOf(NestOption("Walk", "Walk"))),
            NestField("n", "number", "Count", min = 1.0, max = 10.0),
            NestField("note", "text", "Note", maxLength = 5),
            NestField("on", "toggle", "On"),
            NestField("when", "date", "When"),
        )
        assertEquals(listOf("habit"), NestFormRules.missing(fields, mapOf("habit" to "")))
        assertEquals(listOf("n"), NestFormRules.missing(fields, mapOf("habit" to "Walk", "n" to "11")))
        assertEquals(listOf("n"), NestFormRules.missing(fields, mapOf("habit" to "Walk", "n" to "abc")))
        assertTrue(NestFormRules.missing(fields, mapOf("habit" to "Walk", "n" to "3")).isEmpty())
        val p = NestFormRules.payload(fields, mapOf("habit" to "Walk", "n" to "3", "note" to "abcdefgh", "on" to "true", "when" to "2026-11-09"))
        assertEquals("Walk", p["habit"]!!.jsonPrimitive.content)
        assertEquals("3", p["n"]!!.jsonPrimitive.content)   // a number, not "3.0"
        assertFalse(p["n"]!!.jsonPrimitive.isString)
        assertEquals("abcde", p["note"]!!.jsonPrimitive.content)   // max_length
        assertTrue(p["on"]!!.jsonPrimitive.boolean)
        assertEquals("2026-11-09", p["when"]!!.jsonPrimitive.content)
        assertEquals("1.5", NestFormRules.payload(fields, mapOf("n" to "1,5"))["n"]!!.jsonPrimitive.content)
        assertFalse(NestFormRules.payload(fields, emptyMap()).containsKey("note"))   // an empty optional text is left out
        assertEquals(false, NestFormRules.payload(fields, emptyMap())["on"]!!.jsonPrimitive.boolean)
    }

    @Test fun aFormStartsWithItsDefaultsAndTheFirstOptionOfARequiredSelect() {
        val init = NestFormRules.initial(listOf(
            NestField("a", "text", "A", default = "hi"), NestField("b", "toggle", "B", default = "true"), NestField("c", "toggle", "C"),
            NestField("d", "select", "D", required = true, options = listOf(NestOption("x", "X"), NestOption("y", "Y"))),
            NestField("e", "select", "E", options = listOf(NestOption("x", "X"))),
        ))
        assertEquals(mapOf("a" to "hi", "b" to "true", "c" to "false", "d" to "x", "e" to ""), init)
    }

    @Test fun onlyWebAddressesLeaveTheAppAndOnlyThroughTheSystemBrowser() {
        assertTrue(nestOpenable("https://example.com/a?b=1"))
        assertTrue(nestOpenable("http://example.com"))
        assertFalse(nestOpenable("javascript:alert(1)"))
        assertFalse(nestOpenable("file:///etc/passwd"))
        assertFalse(nestOpenable("intent://x#Intent;end"))
        assertFalse(nestOpenable("https://exa mple.com"))
        assertFalse(nestOpenable(""))
    }

    @Test fun everyNameOfTheSchemaHasAnIconAndAnUnknownOneIsAStar() {
        assertEquals(70, NestIcons.NAMES.size)
        NestIcons.NAMES.forEach { assertTrue(NestIcons.known(it)); NestIcons.of(it) }
        assertEquals(NestIcons.of("star"), NestIcons.of("not-an-icon"))
        assertFalse(NestIcons.known("not-an-icon"))
    }

    @Test fun theExtensionsAnswerKeepsTheDraftFlag() {
        val x = KeloApi.decode<NestExtensions>(KeloApi.readJson(NestFixtures.EXTENSIONS))
        assertEquals(2, x.maxTabs)
        val tabs = x.places[NestPlace.TAB].orEmpty()
        assertEquals(listOf(false, true), tabs.map { it.draft })
        assertEquals("Example Author", tabs[0].author)
        assertEquals("book", tabs[1].icon)
    }
}
