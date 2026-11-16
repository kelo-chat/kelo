package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/** Parsing a growing reply block by block must give exactly what parsing the whole text gives. */
class IncrementalBlocksTest {
    private val reply = """
        |안녕하세요! **오늘** 할 일을 정리했어요.
        |
        |- 운동 30분
        |- `assistant` 업데이트
        |* 책 읽기
        |
        |1. 첫째
        |2) 둘째
        |
        |```kotlin
        |val x = 1
        |
        |println(x)
        |```
        |
        |자세한 건 [여기](https://example.com/a)를 보세요.
        |
        |
        |# 제목
        |## 소제목
        |본문 https://example.com/b 입니다.
        |
        |> 인용 한 줄
        |> - 인용 속 목록
        |
        |- 바깥
        |  - 안쪽
        |    1. 더 안쪽
        |  이어지는 줄
        |
        || 이름 | 수량 |
        ||:--|--:|
        || 사과 | 3 |
        || `a|b` | 4 |
        |
        |---
        |
        |~~~python
        |print("x")
        |
        |```
        |~~~
        |***
        |끝.
    """.trimMargin()

    @Test fun charByCharMatchesTheWholeParse() {
        val inc = IncrementalBlocks()
        for (n in 0..reply.length) {
            val t = reply.substring(0, n)
            assertEquals("at $n", Markdown.blocks(t), inc.blocks(t))
        }
    }

    @Test fun chunkByChunkMatchesTheWholeParse() {
        val inc = IncrementalBlocks()
        var n = 0
        val rnd = java.util.Random(3)
        while (n < reply.length) {
            n = minOf(reply.length, n + 1 + rnd.nextInt(30))
            val t = reply.substring(0, n)
            assertEquals("at $n", Markdown.blocks(t), inc.blocks(t))
        }
    }

    @Test fun aTextThatIsNotAContinuationStartsOver() {
        val inc = IncrementalBlocks()
        inc.blocks("first para\n\nsecond")
        assertEquals(Markdown.blocks("other\n\nthing"), inc.blocks("other\n\nthing"))
    }

    @Test fun aTildeFenceHidesBacktickFencesAndBlankLines() {
        val t = "~~~\n```\n\nx\n"
        assertEquals(0, Markdown.stableCut(t))
        val closed = t + "~~~\n\n"
        assertEquals(closed.length, Markdown.stableCut(closed))
    }

    @Test fun theCutIsNeverInsideACodeFence() {
        val t = "a\n\n```\nx\n\ny\n"
        assertEquals(3, Markdown.stableCut(t))   // the blank line inside the open fence does not count
        val closed = t + "```\n\n"
        assertEquals(closed.length, Markdown.stableCut(closed))   // closed, then a blank line: all of it is final
    }
}
