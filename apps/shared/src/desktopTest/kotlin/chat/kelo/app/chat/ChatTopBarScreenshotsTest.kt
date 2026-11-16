package chat.kelo.app.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.toAwtImage
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.ui.character.KeloCharacterAt
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.KeloTheme
import chat.kelo.app.ui.theme.LocalReduceMotion
import java.io.File
import javax.imageio.ImageIO
import kotlin.test.Test
import kotlin.test.assertTrue

@OptIn(ExperimentalTestApi::class)
class ChatTopBarScreenshotsTest {

    @Test
    fun captureChatScreenshots() = runComposeUiTest {
        // inside the build folder, never a machine-specific path (KELO_SHOTS_DIR to keep them elsewhere)
        val outDirs = listOf(File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/top-bar").apply { mkdirs() })

        val widths = listOf(320, 412)
        val themes = listOf(false to "light", true to "dark")
        val scrollStates = listOf(false to "at-rest", true to "scrolled")

        for (width in widths) {
            for ((dark, themeName) in themes) {
                for ((scrolled, scrollName) in scrollStates) {
                    setContent {
                        CompositionLocalProvider(
                            LocalFormFactor provides FormFactor.Phone,
                            LocalReduceMotion provides true,
                        ) {
                            KeloTheme(dark = dark) {
                                AndroidChatMockScreen(
                                    widthDp = width,
                                    scrolled = scrolled,
                                )
                            }
                        }
                    }
                    waitForIdle()

                    val filename = "chat-$scrollName-$themeName-${width}dp.png"
                    val image = onNodeWithTag("android_phone_screen").captureToImage().toAwtImage()
                    for (dir in outDirs) {
                        val file = File(dir, filename)
                        ImageIO.write(image, "png", file)
                        assertTrue(file.exists() && file.length() > 0, "Saved $filename to $dir")
                    }
                }
            }
        }
    }

    @Composable
    private fun AndroidChatMockScreen(widthDp: Int, scrolled: Boolean) {
        val c = Kelo.colors
        val statusTop = 28.dp
        val headerContentHeight = 84.dp
        val totalHeaderHeight = statusTop + headerContentHeight

        // Material 3 behavior:
        // at rest: header background uses surface colour (c.bg)
        // scrolled: header background uses tonal surfaceContainer colour (c.card)
        val headerBg = if (scrolled) c.card else c.bg

        Box(
            Modifier
                .testTag("android_phone_screen")
                .size(widthDp.dp, 680.dp)
                .background(c.bg)
        ) {
            // Chat messages content
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(horizontal = 14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // Top spacing: at rest, content starts below the header;
                // when scrolled, content has scrolled up under the header
                val topSpacing = if (scrolled) 40.dp else (totalHeaderHeight + 12.dp)
                Spacer(Modifier.height(topSpacing))

                // User message
                ChatBubble(
                    text = "오늘 일정 확인해줘",
                    isUser = true,
                    maxWidth = (widthDp * 0.75f).dp,
                )

                // Assistant reply
                ChatBubble(
                    text = "오전 10시에 디자인 리뷰 미팅이 있고, 오후 3시에 스프린트 회의가 예정되어 있습니다.",
                    isUser = false,
                    maxWidth = (widthDp * 0.82f).dp,
                )

                // User message 2
                ChatBubble(
                    text = "오후 회의 자료 준비해둘래?",
                    isUser = true,
                    maxWidth = (widthDp * 0.75f).dp,
                )

                // Assistant reply 2
                ChatBubble(
                    text = "네, 지난주 결정된 안건을 정리하여 요약 자료를 만들어 두었습니다.",
                    isUser = false,
                    maxWidth = (widthDp * 0.82f).dp,
                )
            }

            // Top Header Area: covers status bar + Kelo's header
            // Solid headerBg masks all scrolled content passing under it
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(totalHeaderHeight)
                    .background(headerBg)
                    .align(Alignment.TopCenter)
            ) {
                // Simulated Android status bar at top (clock, icons)
                Row(
                    Modifier
                        .fillMaxWidth()
                        .height(statusTop)
                        .padding(horizontal = 16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        "09:41",
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = c.ink,
                    )
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        // Wi-Fi indicator
                        Box(
                            Modifier
                                .size(width = 14.dp, height = 10.dp)
                                .clip(RoundedCornerShape(2.dp))
                                .background(c.ink)
                        )
                        // Battery indicator
                        Box(
                            Modifier
                                .size(width = 18.dp, height = 10.dp)
                                .clip(RoundedCornerShape(3.dp))
                                .border(1.dp, c.ink, RoundedCornerShape(3.dp))
                                .padding(1.5.dp)
                        ) {
                            Box(
                                Modifier
                                    .fillMaxSize()
                                    .background(c.ink, RoundedCornerShape(1.dp))
                            )
                        }
                    }
                }

                // Floating Header (Agent Avatar disc & Name tag)
                Column(
                    Modifier
                        .fillMaxWidth()
                        .padding(top = statusTop + 4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Box(contentAlignment = Alignment.TopCenter) {
                        // Name tag
                        Box(
                            Modifier
                                .padding(top = 46.dp)
                                .shadow(2.dp, RoundedCornerShape(12.dp))
                                .clip(RoundedCornerShape(12.dp))
                                .background(c.card)
                                .border(1.dp, c.line, RoundedCornerShape(12.dp))
                                .padding(horizontal = 14.dp, vertical = 2.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                "Kelo",
                                style = MaterialTheme.typography.titleSmall,
                                color = c.ink,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                        // Avatar disc
                        Box(
                            Modifier
                                .size(56.dp)
                                .shadow(4.dp, CircleShape)
                                .clip(CircleShape)
                                .background(c.card)
                                .border(2.dp, c.line, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            KeloCharacterAt(KeloMood.Idle, 0L, Modifier.size(50.dp).offset(y = 2.dp))
                        }
                    }
                }
            }

            // Bottom Area: Composer + Tab Bar
            Column(
                Modifier
                    .fillMaxWidth()
                    .align(Alignment.BottomCenter)
                    .background(c.bg)
            ) {
                // Composer field
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 8.dp)
                        .clip(RoundedCornerShape(24.dp))
                        .background(c.card)
                        .border(1.dp, c.line, RoundedCornerShape(24.dp))
                        .padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(KeloIcons.Plus, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp))
                    Text(
                        "메시지를 입력하세요...",
                        style = MaterialTheme.typography.bodyMedium,
                        color = c.muted,
                        modifier = Modifier.weight(1f),
                    )
                    Icon(KeloIcons.Mic, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp))
                    Box(
                        Modifier
                            .size(28.dp)
                            .clip(CircleShape)
                            .background(c.accent),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(KeloIcons.Send, contentDescription = null, tint = c.card, modifier = Modifier.size(16.dp))
                    }
                }

                // Tab Bar
                Column(Modifier.fillMaxWidth().background(c.bg)) {
                    HorizontalDivider(color = c.line)
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceAround,
                    ) {
                        TabItem("채팅", KeloIcons.Chat, selected = true)
                        TabItem("다짐", KeloIcons.Goal, selected = false)
                        TabItem("커넥터", KeloIcons.Plug, selected = false)
                        TabItem("마이", KeloIcons.User, selected = false)
                    }
                }
            }
        }
    }

    @Composable
    private fun ChatBubble(text: String, isUser: Boolean, maxWidth: androidx.compose.ui.unit.Dp) {
        val c = Kelo.colors
        val bubbleBg = if (isUser) c.mine else c.bubble
        val align = if (isUser) Alignment.End else Alignment.Start
        val shape = if (isUser) {
            RoundedCornerShape(topStart = 16.dp, topEnd = 4.dp, bottomStart = 16.dp, bottomEnd = 16.dp)
        } else {
            RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp)
        }

        Box(Modifier.fillMaxWidth(), contentAlignment = if (isUser) Alignment.CenterEnd else Alignment.CenterStart) {
            Box(
                Modifier
                    .widthIn(max = maxWidth)
                    .clip(shape)
                    .background(bubbleBg)
                    .padding(horizontal = 14.dp, vertical = 10.dp)
            ) {
                Text(
                    text,
                    style = MaterialTheme.typography.bodyMedium,
                    color = c.ink,
                    lineHeight = 20.sp,
                )
            }
        }
    }

    @Composable
    private fun TabItem(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector, selected: Boolean) {
        val c = Kelo.colors
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
            val pill = if (selected) c.mine else c.mine.copy(alpha = 0f)
            Box(
                Modifier
                    .width(48.dp)
                    .height(26.dp)
                    .clip(RoundedCornerShape(13.dp))
                    .background(pill),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, contentDescription = null, tint = if (selected) c.ink else c.muted, modifier = Modifier.size(20.dp))
            }
            Text(
                label,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp),
                color = if (selected) c.ink else c.muted,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            )
        }
    }
}
