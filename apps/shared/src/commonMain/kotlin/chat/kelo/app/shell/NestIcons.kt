package chat.kelo.app.shell

import androidx.compose.ui.graphics.vector.ImageVector
import chat.kelo.app.ui.icons.KeloIcons

/**
 * The icon names a Nest screen may use (docs/nest-ui.md, "Icons"), each drawn with the app's own line icons. Anything else is a
 * `star`. A few outlines follow open-source icon sets (see NOTICE: Feather, MIT; Lucide, ISC).
 */
object NestIcons {
    /** Every name of the schema, in the document's order. */
    val NAMES: List<String> = (
        "home star heart check check_circle list calendar clock alarm bell box truck cart bag wallet card chart bar_chart book note pen camera " +
            "image music video map pin globe sun cloud moon fire drop leaf bolt flag tag gift trophy target person people chat mail phone link " +
            "search settings info warning plus refresh trash edit play lock key sparkle food coffee fitness pill plane car school work money weather " +
            "news game"
        ).split(' ')

    fun of(name: String): ImageVector = when (name) {
        "home" -> KeloIcons.Home
        "check" -> KeloIcons.Check
        "calendar" -> KeloIcons.Calendar
        "clock" -> KeloIcons.Clock
        "bell" -> KeloIcons.Bell
        "box" -> KeloIcons.Package
        "card" -> KeloIcons.Card
        "chart" -> KeloIcons.Pulse
        "book" -> KeloIcons.Book
        "note" -> KeloIcons.File
        "pen", "edit" -> KeloIcons.Edit
        "camera" -> KeloIcons.Camera
        "image" -> KeloIcons.Image
        "music" -> KeloIcons.Music
        "video" -> KeloIcons.Video
        "pin" -> KeloIcons.MapPin
        "globe" -> KeloIcons.Globe
        "cloud" -> KeloIcons.Cloud
        "flag" -> KeloIcons.Flag
        "tag" -> KeloIcons.Tag
        "person" -> KeloIcons.User
        "chat" -> KeloIcons.Chat
        "phone" -> KeloIcons.Phone
        "info" -> KeloIcons.Info
        "warning" -> KeloIcons.Alert
        "plus" -> KeloIcons.Plus
        "refresh" -> KeloIcons.Retry
        "trash" -> KeloIcons.Trash
        "play" -> KeloIcons.Play
        "lock" -> KeloIcons.Lock
        "key" -> KeloIcons.Key
        "sparkle" -> KeloIcons.Spark
        "work" -> KeloIcons.Work
        "star" -> Star
        "heart" -> Heart
        "check_circle" -> CheckCircle
        "list" -> ListIcon
        "alarm" -> Alarm
        "truck" -> Truck
        "cart" -> Cart
        "bag" -> Bag
        "wallet" -> Wallet
        "bar_chart" -> BarChart
        "map" -> MapIcon
        "sun" -> Sun
        "moon" -> Moon
        "fire" -> Fire
        "drop" -> Drop
        "leaf" -> Leaf
        "bolt" -> Bolt
        "gift" -> Gift
        "trophy" -> Trophy
        "target" -> Target
        "people" -> People
        "mail" -> Mail
        "link" -> Link
        "search" -> Search
        "settings" -> Sliders
        "food" -> Food
        "coffee" -> Coffee
        "fitness" -> Dumbbell
        "pill" -> Pill
        "plane" -> Plane
        "car" -> Car
        "school" -> School
        "money" -> Money
        "weather" -> Weather
        "news" -> News
        "game" -> Game
        else -> Star   // a name the schema does not have
    }

    /** True for a name of the schema. */
    fun known(name: String): Boolean = name in NAMES

    private fun c(cx: Float, cy: Float, r: Float) = KeloIcons.circle(cx, cy, r)
    private fun r(x: Float, y: Float, w: Float, h: Float, rad: Float) = KeloIcons.rect(x, y, w, h, rad)
    private fun s(name: String, vararg paths: String) = KeloIcons.stroke(name, *paths)

    private val Star by lazy { s("star", "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z") }
    private val Heart by lazy { s("heart", "M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z") }
    private val CheckCircle by lazy { s("check-circle", "M22 11.08V12a10 10 0 1 1-5.93-9.14", "m22 4-10 10.01-3-3.01") }
    private val ListIcon by lazy { s("list", "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01") }
    private val Alarm by lazy { s("alarm", c(12f, 13f, 8f), "M12 9v4l2 2", "M5 3 2 6", "m22 6-3-3") }
    private val Truck by lazy { s("truck", r(1f, 3f, 15f, 13f, 0f), "M16 8h4l3 3v5h-7V8z", c(5.5f, 18.5f, 2.5f), c(18.5f, 18.5f, 2.5f)) }
    private val Cart by lazy { s("cart", c(9f, 21f, 1f), c(20f, 21f, 1f), "M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6") }
    private val Bag by lazy { s("bag", "M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z", "M3 6h18", "M16 10a4 4 0 0 1-8 0") }
    private val Wallet by lazy {
        s("wallet", "M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1",
            "M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4")
    }
    private val BarChart by lazy { s("bar-chart", "M18 20V10M12 20V4M6 20v-6") }
    private val MapIcon by lazy { s("map", "M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4z", "M8 2v16", "M16 6v16") }
    private val Sun by lazy {
        s("sun", c(12f, 12f, 5f), "M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42")
    }
    private val Moon by lazy { s("moon", "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z") }
    private val Fire by lazy {
        s("fire", "M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z")
    }
    private val Drop by lazy { s("drop", "M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z") }
    private val Leaf by lazy { s("leaf", "M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z", "M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12") }
    private val Bolt by lazy { s("bolt", "M13 2 3 14h9l-1 8 10-12h-9l1-8z") }
    private val Gift by lazy {
        s("gift", "M20 12v10H4V12", "M2 7h20v5H2z", "M12 22V7", "M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z", "M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z")
    }
    private val Trophy by lazy {
        s("trophy", "M6 9H4.5a2.5 2.5 0 0 1 0-5H6", "M18 9h1.5a2.5 2.5 0 0 0 0-5H18", "M4 22h16", "M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22",
            "M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22", "M18 2H6v7a6 6 0 0 0 12 0V2Z")
    }
    private val Target by lazy { s("target", c(12f, 12f, 10f), c(12f, 12f, 6f), c(12f, 12f, 2f)) }
    private val People by lazy { s("people", "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", c(9f, 7f, 4f), "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75") }
    private val Mail by lazy { s("mail", r(2f, 4f, 20f, 16f, 2f), "m22 7-10 6L2 7") }
    private val Link by lazy {
        s("link", "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71")
    }
    private val Search by lazy { s("search", c(11f, 11f, 8f), "m21 21-4.35-4.35") }
    private val Sliders by lazy { s("sliders", "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6") }
    private val Food by lazy { s("food", "M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2", "M7 2v20", "M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7") }
    private val Coffee by lazy { s("coffee", "M18 8h1a4 4 0 0 1 0 8h-1", "M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z", "M6 1v3M10 1v3M14 1v3") }
    private val Dumbbell by lazy { s("dumbbell", "M6 7v10M3 9v6M18 7v10M21 9v6M6 12h12") }
    private val Pill by lazy { s("pill", "m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z", "m8.5 8.5 7 7") }
    private val Plane by lazy {
        s("plane", "M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z")
    }
    private val Car by lazy {
        s("car", "M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2",
            c(7f, 17f, 2f), "M9 17h6", c(17f, 17f, 2f))
    }
    private val School by lazy { s("school", "M22 10 12 5 2 10l10 5 10-5Z", "M6 12v5c3 3 9 3 12 0v-5", "M22 10v6") }
    private val Money by lazy {
        s("money", c(12f, 12f, 10f), "M12 6v12", "M15 9.5a3 3 0 0 0-3-1.5c-1.7 0-3 .9-3 2s1.3 1.8 3 2 3 .9 3 2-1.3 2-3 2a3 3 0 0 1-3-1.5")
    }
    private val Weather by lazy {
        s("weather", "M12 2v2M4.93 4.93l1.41 1.41M20 12h2M19.07 4.93l-1.41 1.41", "M15.947 12.65a4 4 0 0 0-5.925-4.128", "M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z")
    }
    private val News by lazy {
        s("news", "M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2", "M18 14h-8", "M15 18h-5", "M10 6h8v4h-8V6Z")
    }
    private val Game by lazy {
        s("game", "M6 11h4M8 9v4", "M15 12h.01M18 10h.01",
            "M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z")
    }
}
