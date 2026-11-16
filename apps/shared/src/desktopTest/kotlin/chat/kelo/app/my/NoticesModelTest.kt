package chat.kelo.app.my

import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test

/** 마이 → 공지: parsing the answer, which links open, the language asked for, and the model's load and read calls. */
@OptIn(ExperimentalCoroutinesApi::class)
class NoticesModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun tearDown() { server?.shutdown(); vms.clear(); Dispatchers.resetMain() }

    private val two = """{"notices":[
        {"id":"n-2","date":"2026-11-15","level":"important","title":"보안 업데이트","body":"서버를 올려 주세요.","link":"https://kelo.chat/download"},
        {"id":"n-1","date":"2026-11-10","level":"info","title":"점검 안내","body":"새벽에 잠깐 멈춰요.\nKelo가 다시 알려 드릴게요."}
    ]}"""

    @Test fun parsesTheNoticesNewestFirstAsTheServerSendsThem() {
        val list = NoticesRules.parse(two)
        assertEquals(listOf("n-2", "n-1"), list.map { it.id })
        assertEquals("important", list[0].level)
        assertEquals("https://kelo.chat/download", list[0].link)
        assertNull(list[1].link)
    }

    @Test fun aMissingLevelIsInfoAndUnknownFieldsAreIgnored() {
        val list = NoticesRules.parse("""{"notices":[{"id":"a1","title":"t","body":"b","future":{"x":1}}],"extra":true}""")
        assertEquals(1, list.size)
        assertEquals(NoticesRules.INFO, list[0].level)
        assertFalse(NoticesRules.isImportant(list[0]))
    }

    @Test fun aBrokenOrEmptyAnswerGivesNoNotices() {
        assertTrue(NoticesRules.parse("not json").isEmpty())
        assertTrue(NoticesRules.parse("""{"detail":"Not Found"}""").isEmpty())
        assertTrue(NoticesRules.parse("""{"notices":[]}""").isEmpty())
    }

    @Test fun aNoticeWithoutAUsableIdIsLeftOut() {
        val list = NoticesRules.parse("""{"notices":[{"id":"","title":"x"},{"id":"../etc","title":"y"},{"id":"ok-1","title":"z"}]}""")
        assertEquals(listOf("ok-1"), list.map { it.id })
    }

    @Test fun onlyKeloSiteHttpsLinksAreOffered() {
        assertEquals("https://kelo.chat/download", NoticesRules.safeLink("https://kelo.chat/download"))
        assertEquals("https://kelo.chat/x", NoticesRules.safeLink("  https://kelo.chat/x  "))
        assertNull(NoticesRules.safeLink("http://kelo.chat/download"))
        assertNull(NoticesRules.safeLink("https://kelo.chat.evil.example/"))
        assertNull(NoticesRules.safeLink("https://evil.example/https://kelo.chat/"))
        assertNull(NoticesRules.safeLink("https://kelo.chat/"))
        assertNull(NoticesRules.safeLink("https://kelo.chat/a b"))
        assertNull(NoticesRules.safeLink(""))
        assertNull(NoticesRules.safeLink(null))
    }

    @Test fun theLanguageIsThePickOrThePhonesWhenFollowingIt() {
        assertEquals("ja", NoticesRules.language("ja", "ko"))
        assertEquals("ko", NoticesRules.language("system", "ko"))
        assertEquals("en", NoticesRules.language(null, "en"))
    }

    @Test fun theUnreadFilterDropsWhatWasReadHere() {
        val list = NoticesRules.parse(two)
        assertEquals(listOf("n-1"), NoticesRules.unread(list, setOf("n-2")).map { it.id })
    }

    @Test fun loadAsksForTheLanguageAndShowsWhatTheServerSends() {
        val s = MyFakeServer { m, p, _ -> if (m == "GET" && p.startsWith("/api/notices")) ok(two) else null }.also { server = it }
        val vm = vms.make { NoticesModel(s.sessions()) { "ko" } }
        vm.load()
        until("the notices") { vm.state.value.notices.isNotEmpty() }
        assertTrue(s.saw("GET /api/notices?lang=ko"))
        assertEquals(listOf("n-2", "n-1"), vm.state.value.notices.map { it.id })
    }

    @Test fun anOlderServerShowsNothing() {
        val s = MyFakeServer { _, _, _ -> null }.also { server = it }   // 404 for everything
        val vm = vms.make { NoticesModel(s.sessions()) { "en" } }
        vm.load()
        until("asked") { s.saw("GET /api/notices") }
        Thread.sleep(100)
        assertTrue(vm.state.value.notices.isEmpty())
        assertNull(vm.state.value.opened)
    }

    @Test fun openingReadsTheNoticeAndItsCardGoes() {
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p.startsWith("/api/notices") -> ok(two)
                m == "POST" && p.endsWith("/read") -> ok("")   // 204: an empty answer
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { NoticesModel(s.sessions()) { "ko" } }
        vm.load()
        until("the notices") { vm.state.value.notices.size == 2 }
        val first = vm.state.value.notices.first { it.id == "n-2" }
        vm.open(first)
        assertEquals("n-2", vm.state.value.opened?.id)
        assertEquals(listOf("n-1"), vm.state.value.notices.map { it.id })
        until("the read call") { s.saw("POST /api/notices/n-2/read") }
    }

    @Test fun aNoticeReadHereDoesNotComeBackOnTheNextLoad() {
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p.startsWith("/api/notices") -> ok(two)   // the server still lists it (the read call failed)
                m == "POST" && p.endsWith("/read") -> ok("")
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { NoticesModel(s.sessions()) { "ko" } }
        vm.load()
        until("the notices") { vm.state.value.notices.size == 2 }
        vm.open(vm.state.value.notices.first { it.id == "n-1" })
        vm.close()
        vm.load()
        until("asked again") { s.requests.count { it.startsWith("GET /api/notices") } == 2 }
        Thread.sleep(100)
        assertEquals(listOf("n-2"), vm.state.value.notices.map { it.id })
        assertNull(vm.state.value.opened)
    }
}
