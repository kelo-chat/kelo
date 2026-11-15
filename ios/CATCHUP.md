# iOS catch-up with Android (started 2026-11-09)

The iPhone app was frozen on 2026-11-09 (about 11:15) while Android (`android/kelo`, the reference) and the server moved on.
This list is what iOS lacks, found from: CHANGELOG.md "Unreleased", the Android string keys used in Kotlin but not in Swift
(406 keys; device-only ones such as SMS / call log / Health / the APK self-updater and the Android notification channels are
left out on purpose), the API paths Android calls that iOS does not, and a file-by-file look at the chat, wallet and My pages.

Paths: Android `A/` = `android/kelo/app/src/main/java/chat/kelo/app/`; iOS `KeloUI/Sources/…`, `KeloCore/Sources/KeloCore/…`.
Order = priority (freezes and chat-critical flows first). `[x]` done and checked with `swift build` + tests; `(shot)` = iOS
screenshot compared with Android.

## 0. Freezes and chat-critical bugs

- [x] Chat list freezes (main thread busy, ChatPage body recomputed again and again) when scrolled away from the bottom.
      iOS `KeloChat/ChatScreen.swift`. Reference: `A/chat/ChatMessageList.kt`, `A/chat/ChatScroll.kt`. The old evidence was gone, so
      the cause was looked for in code and with `sample` on a 300-message history (KELO_MOCK_LONG_HISTORY): the rows the list draws
      were rebuilt on every read (`rows` was computed; now stored, made once per change of the items and with the folded tool chips),
      and "at the end of the list" came from a 1-pt sentinel row whose appear / disappear wrote state on every flap (and the sentinel
      still counted as seen while under the composer). Now the end is measured from the scroll view's own geometry (iOS 18+,
      `onScrollGeometryChange`, Bool only, written only when it changes); the sentinel stays only for iOS 17. Reproduced on the
      simulator: after scrolling up, "↓" (an animated `ScrollViewReader.scrollTo` to the newest row of the lazy list) kept the main
      thread 100% busy in SwiftUI's lazy layout (`LazySubviewPlacements`, `GraphHost.flushTransactions`) for over a minute, and the
      page-wide `.animation(value: atBottom)` re-laid out the whole list. A first fix (jump through `ScrollPosition`'s end edge)
      still froze in a chat with a waiting card: the list bound to its end edge measured every row again on each frame, and the
      status line's three dots were a per-frame `TimelineView` inside the lazy list. Now "↓" and tapping the open tab jump to the
      newest row without animation (once more on the next run-loop turn, so it lands once the rows on the way have their sizes),
      the dots are a repeating fade, and the live end of the list (status line, payment steps, PINs, questions) is its own small
      lazy stack after the history's: even a drawn-only animation inside the history's stack made it place every row again on
      each frame (sampled again in the `grant` scene after the keyboard went away). A streaming reply draws frames only while its
      reveal is behind the text that came. Only the "↓" fades, and the jump to a notified message is not animated. Checked with
      `sample` and the XCUITest's idle wait (no "idle notification not received") in every chat scene.
- [x] The work page (header → 작업) froze for minutes once scrolled to its end (main thread in SwiftUI's lazy placement, sampled
      in the `grants` scene). Its sections above "지난 일" are short and now sit in a plain stack; only the past, which pages in,
      is lazy. The grant rows have one id (an `.id` on a ForEach row gave the lazy stack two), and the loading pulse animates the
      fade alone (a repeating `withAnimation` on appear also caught the frames of rows appearing with it). iOS
      `KeloWork/WorkEntry.swift`, `KeloShared/Design/Components.swift` (SkeletonRows).
- [x] Three blocks never loaded: a `Group { if … }` with nothing to show yet has no view for its `.task` to run on, so "읽는
      계정", a task's grants on the watcher / job pages and the vault's waiting secure entries stayed empty. Each now keeps a
      zero-height view while empty. iOS `BriefingSourcesBlock`, `TaskGrantsBlock` (`KeloShared/Design/ScheduleGrantRows.swift`),
      `KeloMy/Pages/VaultPages.swift`.
- [x] The tab bar shows above the keyboard and covers the floating composer / an in-chat form's send button.
      iOS `KeloShell/MainShell.swift`, `KeloShared/App/Router.swift` (hideChrome). Reference: `A/shell/MainShell.kt` (bar hidden while the IME is up).
- [x] The list stops with the last card under the composer, and no ↓ button in some themes. iOS `ChatScreen.swift`
      (bottom inset / JumpPill colours). Reference: `A/chat/ChatMessageList.kt`, `A/chat/ChatJumpPill.kt`, `A/chat/ComposerScrim.kt`. Cause: the end-of-list
      sentinel counted as seen while hidden under the composer and its scrim, so the list thought it was at the end (no ↓, no
      following). Now "at the end" = the visible part above the bottom inset reaches the content's end (within 64 pt: the list's bottom padding and the scrim's fade); "↓" also takes
      hold of the newest row again. The colours were the same as Android's in both themes.
- [x] Plain "no model" message only for the server's `code: model_required` 400 (any other 400 shows its own words).
      Reference: `A/chat/ChatTurns.kt` (model_required). iOS `KeloChat/ChatModel*.swift`.

- [x] "Connect with access key" also takes a typed sign-in / review code (16-128 characters once single spaces are taken out,
      so "ABCD EFGH JKMN PQRS" works too): a 401 on the key is tried again through POST /api/pair with the spaces stripped, like
      a pairing link. Reference: `A/start/ConnectViewModel.kt` (`codeOf`, `looksLikeCode`), `PairingLinksTest.kt`. iOS
      `KeloShell/StartScreens.swift`, KeloCore `PairingLinks.codeOf` / `looksLikeCode` (+ tests).
- [x] In the chat, nothing floats over a message or a card: the list is masked (drawn only, no layout) to nothing under the
      floating header with an all but hard 4-pt edge just below the name tag, and the "↓" is part of the composer: it straddles
      the card's top border at the trailing side, centred over 보내기, its lower half on the card's top strip (the text field keeps
      10 pt more room above its first line, so the button never covers text), or sits on top of the attachment tray while one
      shows. No band is cut out above the composer any more. Reference: `A/chat/ChatOverlayClear.kt` (`ChatOverlayRules`). iOS
      `KeloChat/Logic/ChatOverlay.swift` (+ `ChatOverlayTests`), `ClearOfOverlays` and the composer's `.overlay` in
      `KeloChat/ChatScreen.swift`. Checked on kelo-ios-check (iPhone 17) only, as no other simulator may be made.
- [x] Generated models: country codes read in upper case (gen_models type `C`; the 3 AddressFormParseTests that failed after a
      regeneration pass again).

## 1. Chat cards and approvals

- [x] Tool steps of one group in a row fold into one chip that updates in place ("메일 읽는 중 · 3/6" → "✓ 메일 6개 읽음"), tap to
      list each call; `tool_started`/`tool_result` `group`, `done`, argument detail. Reference: `A/chat/ChatItems.kt`
      (`foldTools`, `foldedState`, `foldedText`, `detailOf`), `A/chat/ChatStatusRows.kt` (ToolChip, `chat_tool_progress`,
      `chat_tool_ok_failed`), `A/chat/TurnEvents.kt`.
- [x] One live browser card per reply and tab (follows the agent from step to step and site to site, "사이트 N곳"); browser
      steps show no chips in a reply with a card. Reference: `A/chat/ChatItems.kt` (Browser), `A/chat/PurchaseUi.kt`
      (`chat_browser_step_sites`).
- [x] Bulk task permission card: the real count and "목록 보기 (N)" / hide (`approval_request.items`). Reference:
      `A/chat/ChatCards.kt` (`chat_approval_list_show/hide`), `A/agent/ApprovalParts.kt`.
- [x] `step_limit` with `next` from a newer server: no error bubble (the server writes the line + Continue). Reference:
      `A/chat/ChatItems.kt`, `A/chat/TurnEvents.kt`.
- [x] Approval lines with `full` (shown whole). Reference: `A/chat/TurnEvents.kt` ApprovalLine.
- [x] Payment step card ("approve in another app", one-time code, passkey, PIN keypad): code large + copy, QR, countdown bar,
      instruction, [승인했어요]/[취소], expired → new code, OTP typed into the card, keypad picture with taps, dots, clear,
      "결제 마무리 중…", live view; `GET/POST /api/payment-steps`; kept across reloads. Reference: `A/chat/PaymentStepUi.kt`,
      `A/chat/PaymentStepCalls.kt`, `A/chat/TurnEvents.kt` (PaymentStep, PaymentStepRules).
- [x] Payment PIN: `pin_needed` card (release with Face ID), Allow on a payment approval with `pin` also releases the saved PIN;
      in-chat PIN fill-in card (`request_payment_pin`, `pin_secret_*`); Keychain "결제 비밀번호" section (add / delete, other phone);
      PIN sealed with the wallet's end-to-end key (`/api/payment-pins`, deliveries). Reference: `A/chat/PaymentStepUi.kt`
      (PinNeedCard), `A/wallet/PinVault.kt`, `A/wallet/PinForms.kt`, `A/my/PaymentPinSection.kt`, `A/chat/vault/SecretEntryCard.kt`.
- [x] Order approval card wording = Android's (`order_*`, `shipping_ask_done`): items with options and quantity, ship to, card,
      coupon, total, site. iOS `KeloShared/Design/OrderParts.swift` uses iPhone-only `ios_order_*`. Reference: `A/chat/PurchaseUi.kt`.
- [x] Payment-auto row shows the order (items, where it goes). Reference: `A/chat/ChatItems.kt` PaymentAuto.order, `A/chat/PurchaseUi.kt`.
- [x] In-chat shipping form = Android's (states OPEN/DONE/CANCELLED/EXPIRED/CLOSED, `shipping_ask_*` words, "보내는 중…", the
      form closed when its tool result arrives). iOS `KeloChat/Views/ShippingViews.swift`. Reference: `A/chat/ShippingFormCard.kt`.
- [x] Task setup card (`task_allow`, `approval_request.grant`): trigger, what it does, site / recipients / card + the card's own
      auto-pay limit, limits per time / times / monthly, login, ship to, urgent line; urgent + `missed` on any card.
      Reference: `A/agent/TaskGrantCard.kt`, `A/agent/AgentModels.kt` (TaskGrantAsk).
- [x] Morning briefing card in the chat (weather / today / missed, each item opens its place, mail item asks in the composer,
      pending hours suggestion at the bottom with 바꾸기 / 그대로). Reference: `A/chat/BriefingCard.kt`, `A/chat/ChatModels.kt`.
- [x] Fill-in cards: Keychain "입력 대기" section and card entry finished (the wallet's card form inline, `AppModel.wallet`); "앱에서 채우기" (`vault_fill_from_app`) is Android's password-app button: on iPhone the fields carry AutoFill content types instead (no button, as before). Reference:
      `A/my/VaultPages.kt`, `A/chat/vault/SecureEntry.kt`, `A/chat/vault/SecretEntryCard.kt`.
- [-] (n/a: iOS uses the system context menu, which closes on Copy, and its own chevron on select fields) Message long-press "복사됨" feedback (`msg_action_copied`); select field chip menu (`chat_chip_menu`). Reference:
      `A/chat/MessageUi.kt`, `A/chat/SelectField.kt`.

## 2. Wallet and shipping

- [x] Card scanning (number + expiry, never CVC) under the card number in add card, the in-chat card fill-in card and re-entering
      a number: VisionKit `DataScannerViewController` / Live Text, on-device, only the existing camera permission. Reference:
      `A/wallet/CardScan.kt`, `A/wallet/CardScanRules.kt` (Luhn, MM/YY, same read in several frames).
- [x] Shipping pages use Android's words (`shipping_*` instead of `ios_ship_*`), same field errors and calling code line.
      Reference: `A/wallet/ShippingUi.kt`, `A/wallet/ShippingModels.kt`. iOS `KeloMy/Pages/ShippingPages.swift`,
      `KeloShared/Design/AddressParts.swift`.

## 3. Grants, notifications, hours

- [x] Grants on the watcher and job pages (list + 허용 취소), `/api/watchers|workers/{id}/grants`, `/api/task-grants`; rows
      (`task_grant_row_*`). Reference: `A/agent/ScheduleGrants.kt`, `A/work/WatchPages.kt`, `A/work/JobPages.kt`.
- [x] Urgent notifications (a card of a task marked urgent: time-sensitive interruption level) and the briefing notification
      type; briefing opens the chat. Reference: `A/notify/NotifyRules.kt`, `A/notify/Notifier.kt`. iOS `KeloMy/Logic/NotifyRules.swift`,
      `KeloMy/Background.swift`.
- [x] Weekday / weekend contact hours in My → Notifications ("알림 시간": 평일, 주말·공휴일, 배운 시간 [적용], suggestion cards),
      weekend suggestion on Records. Reference: `A/my/NotifyPage.kt`, `A/my/ContactHours.kt`, `A/my/HoursSuggestionUi.kt`,
      `A/my/RecordsPage.kt`.
- [x] Mascot night per day type (weekend hours, server `day_types`, a late night belongs to the evening before). Reference:
      `A/chat/AgentActivity.kt` (SleepHours, dayType, hoursFor, nightAt). iOS `KeloShared/App/AgentActivityModel.swift`, KeloCore AgentActivity.

## 4. Briefing settings

- [x] My → Notifications → 아침 브리핑: on/off, when (auto / fixed time), offset, early open, home area search, phone's approximate
      city ("while using" location, rounded), sections, quiet days, weather provider + keys (checked before saving), "Get one now".
      `/api/briefing*`. Reference: `A/my/BriefingSettings.kt`, `A/device/PhonePlace.kt`.
- [x] "읽는 계정" (sources): its own section at the bottom of My → 알림, after the heads-ups, shown whether the briefing is on or
      not (one choice for the briefing and the heads-ups); calendars and mail each "all except those turned off" / "only those
      turned on", a switch per account, a quiet problem line, "check accounts now". GET/PUT `/api/briefing/sources`, POST
      `/api/briefing/sources/check`. Reference: `A/my/BriefingSources.kt`, `A/my/NotifyPage.kt`. iOS `BriefingSourcesBlock`
      (`KeloMy/Pages/BriefingSettingsViews.swift`), placed in `KeloMy/Pages/NotifyPage.swift`.
- [x] Briefing card: each event and unread mail names its account (`source`, a small tag) when more than one is read.
      Reference: `A/chat/BriefingCard.kt` (SourceTag), `A/chat/ChatModels.kt`. iOS `KeloChat/Views/BriefingViews.swift`
      (models through gen_models: `BriefingEvent.source`, `BriefingItem.source`).
- [x] Usage kind "briefing". Reference: `A/my/UsageUi.kt`.

## 5. The rest

- [x] My → Model row says "아직 고르지 않음" when the server has no model (`modelReady == false`). Reference: `A/my/MyScreen.kt`.
- [-] (n/a on iPhone: push is allowed or not by the system ask, there is no "push needs this device" state) Notifications row "needs device permission" state. Reference: `A/my/MyScreen.kt`, `A/my/NotifyPage.kt` (`notify_state_needs_device`).
- [x] CalDAV connector panel (address, user, app password, other accounts, disconnect; POST `/api/caldav/accounts`, checked by
      the server before saving). Reference: `A/connectors/CalDavConnect.kt`, `A/connectors/ConnectorRules.kt`
      (`connector_method_caldav`, reconnect reasons). iOS `CalDavConnectPanel` in `KeloConnectors/ConnectPanel.swift`. Android's
      "에이전트에게 알리기" after connecting comes from the chat's connect card on iOS, as for every other connector.
- [x] (already: iOS maps the server's dotted keys to the same strings at run time) Connector groups / reconnect reasons the server sends now (`connector_group_*`, `connector_reconnect_*`). Reference:
      `A/connectors/ConnectorRules.kt`.
- [x] (already: iOS shows the same choices with the date, `sched_opt_monthly/yearly`, as Android's picker rows) Schedule editor: monthly / yearly repeat words. Reference: `A/agent/SchedulePage.kt`.
- [x] (already: iOS uses the named variants with the header / parameter name) Vault place header (`vault_place_header`, `vault_place_query`). Reference: `A/my/VaultPages.kt`.
- [-] (n/a: iPhone saves through the share sheet, `ShareLink`) Documents: saving / saved to Files. Reference: `A/my/DocumentsPage.kt`.
- [-] (n/a: Apple's speech recognizer does not tell "busy" or "network" apart; iOS keeps "couldn't hear" / "failed" / "unavailable") Voice input busy / network words. Reference: `A/voice/MicButton.kt`.
- [-] (n/a: the key is no longer used by Android code) Work badge "waiting". Reference: `A/shell/MainShell.kt`.

## Parity screenshots (iOS `ios/screens/catchup-*` vs Android)

iOS: `KeloCatchupShotsTests` on kelo-ios-check (iPhone 17, iOS 26.5) against `desktop/tools/mock_server.py`, ko light and en dark.

| Scene | iOS | Android |
|---|---|---|
| Tool chips folded, one browser card, permission for 37 | `catchup-cards-card-ko-light.png`, `-list-`, `-done-`, `-chip-open-` | scratchpad `bulk-shots/kd_2card.png`, `kd_3list.png`, `kd_4done.png`, `el_2card.png` |
| Task setup card (urgent) | `catchup-grant-card-*.png`, `catchup-grant-card-bottom-*.png` | scratchpad `grant-shots/ko-light-412-a.png`, `-b.png`, `en-dark-412-a.png` |
| Morning briefing card | `catchup-briefing-top-*.png`, `-bottom-` | `android/kelo/screens/sources/card-*-top.png`, `-bottom.png`; scratchpad `brief/shots/card-*` |
| Message hours + briefing settings, 읽는 계정 | `catchup-notify-top-*.png`, `-scroll1..4-`, `-sources-` | scratchpad `brief/shots/settings-ko-light-412-2..5.png`, `shots-full/hours-*.png`; `android/kelo/screens/sources/settings-*` |
| Payment step (app approval), PIN to release | `catchup-pay-step-*.png`, `catchup-pay-pin-*.png` | none taken on Android (card checked against `chat/PaymentStepUi.kt`) |
| PIN keypad in the card | `catchup-keypad-pad-*.png` | none (checked against `PaymentStepUi.kt` PinPadPart) |
| Keychain payment PINs | `catchup-keychain-pins-*.png` (the add form opens in place; not shot) | none (checked against `my/PaymentPinSection.kt`, `wallet/PinForms.kt`) |
| Watcher grants | `catchup-grants-watcher-*.png` | none (checked against `agent/ScheduleGrants.kt` TaskGrantsBlock) |
| Long history, ↓ | `catchup-long-*.png` | — (behaviour) |
| CalDAV connect form | `catchup-caldav-form-*.png`, `-form-bottom-` | none (checked against `connectors/CalDavConnect.kt`) |
| Card scan | — (VisionKit cannot scan in the simulator, so no button there, as Android hides it without a scanner) | `android/kelo/screens/card-scan/*` |

## App Store review notes found on the way

- [x] Camera purpose text said "scan the QR code, no photos are saved", but the camera also takes chat photos (sent) and now scans
      cards: one accurate text in all 20 languages (`tools/infoplist_strings.json`; it no longer comes from Android's QR-only string).
- [x] New location purpose (`NSLocationWhenInUseUsageDescription`) for the briefing's "this phone's city when traveling", from
      Android's own words (`briefing_phone_place` — `briefing_phone_place_about`), all 20 languages; asked only when that switch is
      turned on, "while using the app", reduced accuracy.

## Verified already on iOS (no work)

- Reactions chip size / position (`Reactions.chipStart`), storage warnings + cleanup screen, resources page loading / error and
  lenient numbers, server update card / progress (owner only), model picker "제공자 선택…", privacy page titles (`privacy_titles`
  array), MCP pre-sign-in notes and "키로 연결하기", Outlook granted scope, sleeping after 5 min idle, time lines, secret-in-message
  sheet. Kelo Nest: server only, no UI (skipped on purpose).

## 6. Tone setting: friendly / polite (added 2026-11-09, Android + desktop done; iOS done 2026-11-09)

How Kelo talks to the user. Two values, stored on the server as the profile field `tone`: `friendly` (친근: casual, like a close
friend; Korean 반말) and `polite` (공손: courteous; Korean 해요체, how Kelo talked before). **Default polite**: an older server sends
no `tone`, which reads as polite. The app's own UI texts never change with the tone; only what the agent writes does.

Server (done): `GET /api/profile` returns `tone`; `PUT /api/profile {"tone": "friendly"|"polite"}` (anything else 422);
`GET /api/agent/status` also carries `"tone"` so a change shows without a reload. In the chat, "반말로 해 줘" / "존댓말 써" makes the
agent call `set_tone` (no approval card), which saves the same field.

- [x] (done: `gen_models.py` table, ApiModels.swift regenerated) Model: add `tone:S="polite"` to `Profile` and `tone:S?` to `AgentStatus` in `ios/tools/gen_models.py`, then regenerate
      `KeloCore/Sources/KeloCore/Models/ApiModels.swift`.
- [x] (done: `KeloCore/Sources/KeloCore/Agent/ToneRules.swift`) Rules (port of Android `my/ToneRules.kt`): `selected(profile)` = friendly only when `tone == "friendly"`, else polite;
      `synced(profile, polledTone)` = the profile with the polled tone when it is one of the two and differs, else nil;
      `reload` = GET /api/profile; `save(tone)` = PUT /api/profile `{"tone"}` and use the profile it returns.
- [x] (done) Placement: **My → Agent** page (마이 → 에이전트, `KeloUI/Sources/KeloMy/Pages/ProfilePages.swift` `AgentPage`), a new section
      `R.string.my_agent_tone` ("말투" / "Tone") right under the Name section and above "Right now". **Not** in setup/onboarding.
- [x] (done: private `ToneRow` in ProfilePages.swift, ids `kelo.tone.friendly` / `kelo.tone.polite`; VoiceOver reads it as a selected / unselected button in a group named 말투, as SwiftUI has no radio role) The selector: two radio rows in one group, each with its label (`my_agent_tone_friendly` 친근 / `my_agent_tone_polite` 공손) and,
      under it, a muted one-line example that wraps (`my_agent_tone_friendly_example`, `my_agent_tone_polite_example`). Tapping a
      row selects it at once (optimistic), PUTs, then shows the saved profile; while saving both rows are disabled; on failure the
      old choice comes back with `my_agent_tone_failed`. The radio role / selected state go to VoiceOver.
- [x] (done: `.task` reload on AgentPage; `AgentActivityModel.onStatus` → `AppModel.statusPolled`, set in AppModel.init, so MainShell is unchanged) Live update: (1) reload the profile (GET /api/profile) every time the Agent page opens; (2) in the status poll
      (`AgentActivityModel`), apply `synced(profile, status.tone)` to the shared profile, as the agent name already is, so after
      "반말로 해 줘" in the chat the page shows 친근 without a manual refresh. Tapping 공손 switches it back; the next reply uses it.
- [x] (already generated; `gen_strings.py --check` clean) Strings: already in Android res (`values*/strings_tone.xml`, all 20 languages); run `ios/tools/gen_strings.py`.
- [x] (done: `KeloCore/Tests/KeloCoreTests/Rules/ToneRulesTests.swift`, `KeloUI/Tests/KeloSharedTests/ToneSyncTests.swift`, UI walk `Kelo/UITests/KeloToneShotsTests.swift` with `TEST_RUNNER_KELO_TONE=1`) Tests: the rules (older server = polite, unknown = polite, poll changes only when different) and the round trip: status says
      friendly → page shows friendly; reload says friendly; save polite → PUT body `{"tone":"polite"}` → page shows polite
      (Android `ToneRulesTest.kt`).
- [x] (done for ko light / ko dark / en; narrow width not shot: the only simulator allowed is kelo-ios-check, iPhone 17, 402 pt, and no new ones may be made) Screenshots: ko light / dark, en, narrow width; compare with `android/kelo/screens/tone/` and `desktop/screens/tone-*`.
      `KeloToneShotsTests` on kelo-ios-check (iOS 26.5) against `desktop/tools/mock_server.py`; the walk also checks the save, the
      status-poll update and that the reopened page shows the server's tone. Pairs (iOS `ios/screens/` ↔ Android `android/kelo/screens/tone/`):

      | Scene | iOS | Android |
      |---|---|---|
      | 공손 chosen, ko light | `tone-agent-ko-light.png` | `agent-tone-ko-light-412.png` |
      | 친근 tapped and saved, ko light | `tone-agent-friendly-ko-light.png` | `agent-tone-ko-light-412-friendly.png` |
      | 공손 chosen, ko dark | `tone-agent-ko-dark.png` | `agent-tone-ko-dark-412.png` |
      | Polite chosen, en light (the polite example wraps to two lines) | `tone-agent-en-light.png` | `agent-tone-en-light-412.png` |
      | Changed on the server, shown by the status poll | `tone-agent-polled-{ko-light,ko-dark,en-light}.png` | — (behaviour) |
      | 친근 in dark / en | `tone-agent-friendly-{ko-dark,en-light}.png` | — |

      Same order and layout as Android: 사진 card, 이름 row, 말투 with the radio in the icon column and the example muted under each
      label, then 지금 상태. One difference that is not part of §6: with no picture, iOS `AgentPicture` shows the name's initial
      ("K") where Android draws the Kelo character, although the text next to it says the character shows.

- [x] Smarter morning briefing card (server briefing.py): the headline replaces the old line (`BriefingRules.headline`: headline, else line, never both), a "Worth noticing" block under it (notes with reference chips that do what a tap on that item does; refs that point at nothing are skipped; no-tap items are plain chips), a "Next steps" block above the hours line (a tap writes the question into the composer, like the mail item: `BriefingTap.ask`). Models: `BriefingLink`, `id` on weather / events / ahead / missed, `headline`, `connections`, `suggestions` (all optional). Strings `briefing_connections` / `briefing_steps` come from Android's `strings_briefing.xml`. Tests: `BriefingCardTests`, `BriefingWidthTests` (the card drawn at 320 / 375 / 414 pt on macOS). Screens: `ios/screens/briefing-smart-*.png`.
