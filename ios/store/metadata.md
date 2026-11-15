# App Store 첫 제출 초안 — Kelo (chat.kelo.app)

App Store Connect(ASC)의 앱 정보·버전 페이지에 넣을 내용과, 심사에서 물어볼 만한 것을 정리한 **초안**이에요. 스크린샷은 따로 만들고
있어요. 2026-11-10 기준 코드(`ios/`, MARKETING_VERSION 1.0.0, 빌드 26111001)와 ASC 현재 값(읽기만 함)을 보고 썼어요.
**아직 아무것도 ASC에 넣지 않았어요.** 앱이 바뀌면 이 답도 다시 확인해야 해요.

ASC 현재 상태 (`asc.py get`, 읽기만):
- 앱 id `6832422201`, 번들 `chat.kelo.app`, SKU `kelo-ios`, **기본 언어 ko**, `isOrEverWasMadeForKids: false`
- 앱 정보(appInfo `5e07523c-…`): 상태 PREPARE_FOR_SUBMISSION. 현지화는 **ko 하나**: 이름 **`kelo`(소문자)**, 부제·개인정보처리방침 URL 비어 있음
- 카테고리 없음, 연령 등급 설문 전부 비어 있음, 버전 1.0(PREPARE_FOR_SUBMISSION), 저작권 비어 있음

---

## 1. 앱 이름 / App name

| 언어 | 이름 | 비고 |
|---|---|---|
| ko (기본) | **Kelo** | 지금 `kelo`(소문자)로 잡혀 있어요. 이름은 대소문자를 구분하지 않고 이미 이 앱이 가진 이름이라 `Kelo`로 바꿔도 다른 앱과 겹치지 않아요 |
| en-US (추가) | **Kelo** | en-US 현지화를 새로 추가해야 해요(대부분 국가의 기본 표시 언어) |

"Kelo"가 다른 스토어에서 이미 쓰이는지 물었는데: ASC는 같은 이름을 두 앱이 가질 수 없게 하고, `kelo`는 이미 **이 앱 이름으로 예약**돼
있어요. 그래서 App Store에서는 문제없어요. 혹시 거절되면 대안: `Kelo: AI Agent` / `Kelo — 내 AI 비서`(30자 이내).

## 2. 부제 / Subtitle (≤30)

| 언어 | 부제 | 글자 수 |
|---|---|---|
| en-US | `Self-hosted AI agent` | 20 |
| en-US 대안 | `Your server. Your AI model.` | 27 |
| ko | `내 서버에서 돌아가는 AI 비서` | 17 |

## 3. 프로모션 텍스트 / Promotional text (≤170, 심사 없이 바꿀 수 있음)

- en-US (162): `Run your own AI assistant on your own server, with the model you choose. Chat from your iPhone; it works in its own browser and asks before anything hard to undo.`
- ko (93): `내가 고른 AI 모델로, 내 서버에서 돌아가는 AI 비서예요. iPhone에서 말을 걸면 자기 브라우저와 연결한 서비스로 일하고, 되돌리기 어려운 일은 먼저 물어봐요.`

## 4. 설명 / Description (≤4000)

앱이 실제로 하는 것만 썼어요. iPhone 앱에 **없는** 것(건강 데이터, 문자·통화 기록, 앱 안 업데이트)은 넣지 않았고, 다른 플랫폼
(Android, Windows) 이름도 넣지 않았어요(가이드라인 2.3.10).

### en-US (2,458자)

```
Kelo is the iPhone app for your own AI assistant. The assistant runs on a Kelo server that you install yourself (with Docker, on a Mac, a Linux box, a NAS or a cloud server) and uses the AI model you choose. The app connects to that server; it has no assistant of its own and does nothing without one.

You need a Kelo server to use this app. Setting one up takes a few minutes: see kelo.chat for the free, open-source server and the guided setup. Pair your iPhone with a QR code or a link from the server.

YOUR SERVER, YOUR MODEL
- Chats, memory, files and keys are stored on your server, not by us.
- Pick the AI service that answers: Anthropic, OpenAI, Google, OpenRouter, Mistral and many more, with your own API key, or a model running on your own hardware (LM Studio, Ollama or any OpenAI-compatible server).
- Before your first message, Kelo tells you where your chats go and asks for your OK. The model picker always shows which service receives them.

WHAT IT DOES
- Chat by text or voice, with photos and documents, or send things to Kelo from the share sheet.
- Its own browser: Kelo opens sites, reads, compares and fills in forms on your server. You can watch it live, take over and hand it back.
- Connectors: mail, calendars, GitHub, Notion, Todoist and more, each connected on the service's own sign-in page.
- Schedules and watchers: run a task every morning, or get told when a page changes.
- Goals: write down a goal and Kelo keeps the plan, steps and due dates with you.
- On this iPhone (each one off until you turn it on): calendar, contacts and location for the briefing's weather and "near me" questions.

IT ASKS FIRST
Looking things up just happens. Anything that commits something for you (paying, ordering, booking, sending, deleting) waits for your Allow or Deny on the phone. When a site wants to check that you're a person, Kelo hands the browser to you instead of trying to get around it.

KEYCHAIN AND CARDS
Passwords and keys go in through a form, never the chat, and the AI model never sees them. Keychain items and cards open with Face ID. A payment card can be locked with a key only your iPhone holds.

ALSO
- 20 languages; Kelo answers in the language you write in.
- Report any reply you find wrong or harmful from the reply's menu.
- No ads, no tracking, no analytics in the app.

Kelo's server is free and open source (AGPL-3.0). You pay only for what you choose to use, such as your AI provider or a cloud server.
```

### ko (1,289자)

```
Kelo는 내 AI 비서를 위한 iPhone 앱이에요. 비서는 내가 직접 설치한 Kelo 서버(Docker로 Mac, Linux, NAS, 클라우드 서버 어디든)에서 돌아가고, 내가 고른 AI 모델을 써요. 앱은 그 서버에 연결해요. 앱 안에 비서가 따로 있지 않아서, 서버 없이는 쓸 수 없어요.

이 앱을 쓰려면 Kelo 서버가 필요해요. 설치는 몇 분이면 돼요. 무료 오픈소스 서버와 안내형 설치는 kelo.chat에서 볼 수 있어요. 서버가 보여 주는 QR 코드나 연결 링크로 iPhone을 연결해요.

내 서버, 내 모델
- 대화, 기억, 파일, 키는 우리가 아니라 내 서버에 저장돼요.
- 답할 AI 서비스를 골라요: Anthropic, OpenAI, Google, OpenRouter, Mistral 등을 내 API 키로 쓰거나, 내 장비에서 돌리는 모델(LM Studio, Ollama, OpenAI 호환 서버)을 써요.
- 첫 메시지를 보내기 전에 대화가 어디로 가는지 알려 주고 동의를 받아요. 모델 선택 화면에도 대화를 보내는 곳이 늘 보여요.

할 수 있는 일
- 글이나 음성으로, 사진과 문서를 붙여 대화해요. 공유 메뉴에서 Kelo로 보낼 수도 있어요.
- 자기 브라우저: 서버에서 사이트를 열고 읽고 비교하고 양식을 채워요. 실시간으로 보다가 직접 이어받고 다시 넘길 수 있어요.
- 커넥터: 메일, 캘린더, GitHub, Notion, Todoist 등을 각 서비스의 로그인 화면에서 연결해요.
- 예약과 지켜보기: 매일 아침 할 일을 돌리거나, 페이지가 바뀌면 알려 줘요.
- 목표: 목표를 적으면 계획, 단계, 마감을 함께 챙겨요.
- 이 iPhone에서(각각 켜기 전에는 꺼져 있어요): 캘린더, 연락처, 그리고 브리핑 날씨와 "근처" 질문을 위한 위치.

먼저 물어봐요
찾아보는 일은 바로 해요. 결제, 주문, 예약, 보내기, 삭제처럼 나 대신 무언가를 확정하는 일은 휴대폰에서 허용이나 거부를 누를 때까지 기다려요. 사이트가 사람인지 확인하려 하면, 우회하지 않고 브라우저를 나에게 넘겨요.

키체인과 카드
비밀번호와 키는 대화가 아니라 입력 양식으로 넣어서 AI 모델이 볼 수 없어요. 키체인과 카드는 Face ID로 열어요. 결제 카드는 이 iPhone만 가진 키로 잠글 수 있어요.

그 밖에
- 20개 언어. 쓰는 언어로 답해요.
- 이상하거나 해로운 답은 답의 메뉴에서 신고할 수 있어요.
- 앱에 광고, 추적, 분석 도구가 없어요.

Kelo 서버는 무료 오픈소스(AGPL-3.0)예요. 비용은 내가 고른 것(AI 서비스, 클라우드 서버 등)에만 들어요.
```

> 확인할 것: (1) 제출 시점에 **소스가 공개돼 있지 않으면** "open source / 오픈소스"를 빼세요(지금 저장소는 `kelo-src` 비공개). (2) README에
> "한 줄 설치는 Kelo가 공개되면 열려요"라고 돼 있어요. 설명이 "kelo.chat에서 설치"라고 하니 **설치 스크립트가 출시 전에 열려 있어야** 해요.

## 5. 키워드 / Keywords (≤100, 쉼표로 구분, 띄어쓰기 없이)

- en-US (95): `ai assistant,agent,self-hosted,llm,chatbot,local model,automation,browser,privacy,docker,server`
- ko (52): `AI비서,에이전트,셀프호스팅,LLM,챗봇,로컬모델,자동화,개인서버,브라우저,일정,도커,개인정보`

다른 회사 상표(Claude, ChatGPT, Gemini 등)는 넣지 않았어요(가이드라인 2.3.7: 키워드에 다른 앱 이름·상표 금지). 앱 이름 "Kelo"도 이미
검색되니 뺐어요.

## 6. URL

| 칸 | 값 | 비고 |
|---|---|---|
| 지원 URL (필수) | `https://kelo.chat/support` **(새로 만들어야 함)** | 지금 사이트에 지원 페이지가 없어요. 지원 URL은 실제 연락 방법이 보이는 페이지여야 해요. 급하면 `https://kelo.chat/privacy#contact`(연락처 support@kelo.chat이 있는 12장)도 되지만, 짧은 지원 페이지(연락 메일, 서버 설치 링크, 자주 묻는 질문)를 권해요 |
| 마케팅 URL (선택) | `https://kelo.chat` | |
| 개인정보처리방침 URL (필수) | `https://kelo.chat/privacy` | **iPhone 앱 내용으로 고쳐야 해요** (아래 9-5) |

## 7. 카테고리 / Category

- **주 카테고리: 생산성(Productivity)** — 할 일, 일정, 메일, 목표, 예약 작업을 대신하는 비서예요. Play도 생산성으로 냈어요.
- **보조 카테고리: 유틸리티(Utilities)** — 자기 서버를 다루는 도구(연결, 기기 관리, 서버 업데이트)이기도 해요.
- 고려했지만 안 고른 것: 개발자 도구(서버를 직접 깔지만 앱 사용자는 개발자가 아님), 비즈니스(개인용), 라이프스타일.

## 8. 저작권 / Copyright

`2026 Nam Ho Kang`

---

## 9. App Privacy (개인정보 "영양 성분표")

### 9-1. Apple 정의 (developer.apple.com/app-store/app-privacy-details, 2026-11-10 확인)

> **"Collect"** refers to transmitting data off the device in a way that allows **you and/or your third-party partners** to access it for a
> period longer than what is necessary to service the transmitted request in real time.
>
> **"Third-party partners"** refers to analytics tools, advertising networks, third-party SDKs, or other external vendors **whose code
> you've added to your app**.
>
> "if data is sent to your servers then immediately discarded after servicing the request, you do not need to disclose this"
>
> "Data that is processed only on device is not 'collected' and does not need to be disclosed in your answers."
>
> **Linked**: "You'll need to identify whether each data type is linked to the user's identity (via their account, device, or other
> details) by you and/or your third-party partners. … 'Personal Information' and 'Personal Data', as defined under relevant privacy laws,
> are considered linked to the user."
>
> **"Tracking"** refers to linking data collected from your app about a particular end-user or device … with Third-Party Data for targeted
> advertising or advertising measurement purposes, or sharing data … with a data broker.

선택 공개(optional disclosure)는 네 조건을 **모두** 만족할 때만이고, 그중 하나가 "the user's name or account name is prominently displayed
in the submission form"이라 Kelo의 신고 양식(이름 없음)은 해당하지 않아요 → 신고는 공개해야 해요.

### 9-2. 까다로운 부분: 사용자가 운영하는 서버

Apple의 "collect"는 **개발자("you")나 개발자가 앱에 넣은 SDK가** 데이터에 접근할 수 있을 때예요. Kelo에서는:

- 앱이 데이터를 보내는 곳은 거의 다 **사용자 자신이 설치·운영하는 Kelo 서버**예요. 개발자(Nam Ho Kang)는 그 서버에 접근할 방법이 없어요
  (개인정보처리방침 2장 "The operator has no access to your server"). → Apple 정의상 **개발자의 수집이 아니에요.**
- AI 회사로 대화가 가는 것은 **사용자의 서버가, 사용자의 키로, 사용자가 고른 회사로** 보내는 거예요. 그 회사는 개발자가 앱에 넣은 SDK가
  아니라서 "third-party partner"도 아니에요. → 성분표에는 안 들어가요. 대신 **가이드라인 5.1.2(i)의 공개·동의 대상**이고, 그건 앱 안
  동의 화면(10장)과 개인정보처리방침 6장으로 해요.
- iPhone 앱에는 **외부 SDK가 하나도 없어요**(Firebase, ML Kit, 분석·광고 없음. QR·카드 인식은 VisionKit/AVFoundation으로 기기 안에서,
  음성 인식은 iOS Speech로 가능하면 기기 안에서). Android와 달리 **업데이트 확인은 없어요.** 푸시는 **Apple 자체 APNs**로만 받아요(Firebase·FCM SDK 없음, 외부 SDK 없음: `UIApplication.registerForRemoteNotifications`와 `aps-environment` 권한만 써요).
- 단, 개발자 서버(kelo.chat)로 가는 것이 **두 가지** 있어요: ① 사용자가 직접 보내는 **답 신고**(1년 보관), ② 커넥터의 **Google 로그인 베타
  신청**(입력한 Gmail 주소, 처리 후 30일). 이 둘은 개발자의 수집이라 공개해야 해요.
- 기본 주소(`<이름>.kelo.chat`)를 쓰면 앱↔서버 통신이 Cloudflare(운영자 계정)를 지나가지만, https가 Cloudflare에서 끝났다가 바로 사용자
  서버로 가고 저장되지 않아요 → "servicing the request in real time"에 해당, 수집 아님. 로그인 중계(OAuth relay)도 토큰을 메모리에서만
  다루고 저장하지 않아요 → 수집 아님.

**권장 답: "예, 데이터를 수집해요"** + 아래 네 유형만. 나머지는 사용자 서버로만 가므로 체크하지 않아요.

> 보수적인 대안: 심사자가 "사용자 서버로 보내는 것도 수집"이라고 보면, 아래 9-4의 "사용자 서버로만" 항목들을 모두 **수집 / 연결됨 / 추적
> 아님 / 앱 기능**으로 추가하면 돼요(Play Data safety와 같은 방식). Apple 정의의 문구("you and/or your third-party partners")에 맞는 건
> 권장 답이고, 보수적인 대안은 성분표가 길어지는 대신 거절 위험이 가장 낮아요. **고르는 건 소유자 결정이에요.**

- **푸시 기기 토큰(APNs)**: 앱이 받은 토큰은 **사용자의 서버**로만 가요. 알림을 보낼 때 사용자의 서버가 그 토큰을 운영 서버(`kelo.chat`)를 거쳐
  Apple 푸시 서비스에 넘기는데, 운영 서버는 토큰과 알림 데이터를 **메모리에서만 다루고 저장도 로그도 하지 않아요**(알림 종류와 개수만 기록). 그래서 Apple의
  "collect" 정의(실시간 처리를 넘어 접근할 수 있게 전송)에 해당하지 않는다고 봐요 → 권장 답: 체크 안 함. 푸시에는 알림 종류와 번호만 실리고 글은 없어요.
  보수적인 대안: 식별자 → **기기 ID**(Device ID)를 수집 / 연결 안 됨 / 추적 안 함 / 앱 기능으로 추가해요. Apple이 푸시 토큰을 어떻게 보는지는 심사자마다 다를 수 있어서
  **소유자가 고르는 항목**이에요. SDK는 없어서 "third-party partner"는 Apple 자신뿐이에요.

### 9-3. 권장 답 (개발자가 실제로 받는 것)

| ASC 유형 | 수집 | 사용자와 연결 | 추적 | 목적 | 무엇 |
|---|---|---|---|---|---|
| 사용자 콘텐츠 → **기타 사용자 콘텐츠** (Other User Content) | 예 | **예** | 아니요 | **앱 기능** | 신고한 AI 답, (체크하면) 바로 앞 내 메시지, 고른 이유와 의견. 1년 뒤 삭제 |
| 진단 → **기타 진단 데이터** (Other Diagnostic Data) | 예 | **예** | 아니요 | **앱 기능** | 신고에 붙는 모델 이름, 앱 버전 |
| 식별자 → **사용자 ID** (User ID) | 예 | 예 | 아니요 | **앱 기능** | 신고·베타 신청에 붙는 사본의 설치 비밀값 SHA-256 해시(남용 방지). "assigned user ID … that can be used to identify a particular user or account"에 가장 가까워요 |
| 연락처 정보 → **이메일 주소** (Email Address) | 예 | 예 | 아니요 | **앱 기능** | 커넥터 → Google 로그인 베타 신청 때 직접 적는 Gmail 주소. 테스트 사용자 등록과 결과 메일 1회. 처리 후 30일 뒤 삭제 |

- "연결됨"을 **예**로 둔 이유: 내용 안에 개인정보가 있을 수 있고(Apple: 개인정보는 연결된 것으로 봄), 설치 해시로 같은 서버의 신고끼리 묶여요.
- 목적을 "앱 기능"으로 둔 이유: Apple의 앱 기능 정의에 "prevent fraud, implement security measures … or perform customer support"가 있어요.
  신고는 해로운 답을 찾아 고치는 안전 기능이에요. 분석·광고·마케팅에는 쓰지 않아요.
- 추적: 전부 **아니요**. 광고 식별자(IDFA)도, ATT도 안 써요(`NSPrivacyTracking false`).

### 9-4. 그 밖의 데이터 (사용자 서버로만 → 권장: 체크 안 함)

| 데이터 (ASC 유형) | 언제 | 어디로 | 개발자 접근 | 권장 답 | 보수적 대안 |
|---|---|---|---|---|---|
| 채팅 메시지 (기타 사용자 콘텐츠) | 보낼 때 | 내 서버 → 내가 고른 AI 회사 | 없음 | 체크 안 함(신고분은 9-3) | 수집·연결·앱 기능 |
| 사진·동영상 (Photos or Videos) | 첨부할 때(PhotosPicker, 위치 정보 지움) | 내 서버 → AI 회사 | 없음 | 체크 안 함 | 수집·연결·앱 기능 |
| 파일·문서 (기타 사용자 콘텐츠) | 첨부·공유할 때 | 내 서버 | 없음 | 체크 안 함 | 수집·연결·앱 기능 |
| **정확한 위치** (Precise Location) | 위치를 허용하면 앱을 열 때(최대 15분마다). 사용자가 iOS에서 "정확한 위치"를 끄면 대략적 위치 | 내 서버(최신 1개만). 날씨 서비스엔 약 1km로 반올림 | 없음 | 체크 안 함 | 정확한 위치·대략적 위치 둘 다 수집·연결·앱 기능 |
| **연락처** (Contacts) | 이 기기에서 → 연락처를 켤 때만 | 내 서버(암호화, 끄면 삭제) | 없음 | 체크 안 함 | 수집·연결·앱 기능 |
| **캘린더** (기타 사용자 콘텐츠로 신고. Apple 목록에 "캘린더" 유형이 따로 없음) | 캘린더를 켤 때만, 7일 전~60일 후 | 내 서버(암호화) | 없음 | 체크 안 함 | 기타 사용자 콘텐츠에 포함 |
| 오디오 (Audio Data) | 마이크 버튼 | **어디에도 안 감**: iOS 음성 인식이 글로 바꾸고 글만 메시지로 감(가능하면 기기 안 인식) | 없음 | 체크 안 함 | 체크 안 함 |
| 카메라 | QR·카드 스캔(기기 안), 사진 찍어 첨부(→ 사진) | 스캔 결과는 어디에도 안 감 | 없음 | 체크 안 함 | 사진에 포함 |
| 결제 정보 (Payment Info) | 지갑에 카드를 넣을 때 | 내 서버. 자동결제 끈 카드는 iPhone 키로 암호화돼 서버도 못 읽음 | 없음 | 체크 안 함 | 수집·연결·앱 기능 |
| 이름 (Name) | 처음에 적는 내 이름 | 내 서버 | 없음 | 체크 안 함 | 수집·연결·앱 기능 |
| 이메일 (메일 커넥터 연결 시) | 메일을 앱 비밀번호로 연결할 때 | 내 서버 | 없음 | 체크 안 함(베타 신청분은 9-3) | 수집·연결·앱 기능 |
| 기타 민감 정보 — 키체인 로그인·API 키 | 양식으로 넣을 때 | 내 서버(암호화, AI 모델은 못 봄) | 없음 | 체크 안 함 | "기타 데이터 유형"으로 수집·연결·앱 기능 |
| 건강·피트니스 (Health, Fitness) | — | **iPhone 앱엔 HealthKit이 없어요** | — | 체크 안 함 | 체크 안 함 |
| 기기 ID (Device ID) | 서버가 발급한 기기 토큰(내 서버가 기기 구분용) | 내 서버 | 없음 | 체크 안 함 | 수집·연결·앱 기능 |
| 생체 정보 (Face ID) | 키체인·카드 열 때 | iOS가 처리, 앱·서버로 안 옴 | — | 체크 안 함 | 체크 안 함 |
| 검색·방문 기록 (Search/Browsing History) | 에이전트가 서버의 자기 브라우저로 방문 | 내 서버 | 없음 | 체크 안 함 | 수집·연결·앱 기능 |

### 9-5. 같이 고칠 것 (이번 작업에서는 고치지 않았어요)

- **개인정보처리방침**(`src/assistant/site/pages/privacy.*`): 1장이 "The iPhone app will follow the same rules when it ships"라고만 하고 3장
  표가 Android 기준이에요(FCM 푸시, ML Kit, Health Connect, 업데이트 확인). iPhone 앱 열(푸시는 Apple APNs로 직접(SDK 없음), ML Kit 없음, VisionKit 기기 안 인식,
  iOS 음성 인식, HealthKit 없음)과 **"첫 메시지 전 AI 서비스 동의"**(무엇을, 어디로, 동의 기록: 기기·시각, 철회 `DELETE /api/ai-consent`)를
  넣어야 해요. 심사자는 방침과 성분표가 맞는지 봐요.
- **PrivacyInfo.xcprivacy**: `NSPrivacyCollectedDataTypes`가 비어 있어요. 9-3의 네 유형을 성분표에 넣는다면 매니페스트에도 같은 유형을 넣는
  게 맞아요(OtherUserContent, OtherDiagnosticData, UserID, EmailAddress; Linked true, Tracking false, Purpose AppFunctionality).
- iPhone 앱 마이 → 개인정보 페이지에 `https://kelo.chat/privacy` 링크가 없어요(이제 동의 화면에는 있어요). 마이에도 하나 두면 5.1.1(i)
  "in-app"이 더 분명해져요.

---

## 10. 수출 규정 / Export compliance

- `ios/Kelo/project.yml`: `ITSAppUsesNonExemptEncryption: false` (앱 Info.plist와 공유 확장은 키가 없지만, 확장은 앱과 함께 심사돼요).
- 앱의 암호화: ① https(URLSession/ATS) ② 기기 안 데이터 보호용 AES-GCM(CryptoKit: 화면 캐시, 결제 카드의 종단간 암호화, 키체인 PIN 금고)
  ③ Keychain/Face ID ④ 0.9.0부터 앱↔서버 종단간 암호화(KE2E: HPKE RFC 9180 = X25519 + HKDF-SHA256 + ChaCha20-Poly1305)와
  Keyguard 키 감싸기(Secure Enclave P-256 + HPKE). 전부 **CryptoKit/Security(Apple OS가 제공하는 암호화)**로만 하고, 직접 넣은
  암호 라이브러리(OpenSSL, libsodium 등)나 자체·비표준 알고리즘은 없어요. KE2E는 IETF 표준 알고리즘을 조합한 메시지 형식일 뿐이에요.
- 답: **"면제(exempt)"**. ASC가 물으면 "앱이 암호화를 쓰나요? → 예(표준 암호화)" 다음 "면제 대상 → 예" 흐름이 되고, plist 값 `false` 덕분에
  빌드마다 묻지 않아요. 면제 근거: 미국 EAR의 대중 시장(mass market) 표준 암호화 / 인증·데이터 보호 목적. 프랑스 배포 서류도 필요 없어요.
- 0.9.0 재검토(2026-11-10): KE2E가 들어갔지만 ASC 질문의 답은 그대로예요. "독자·비표준 알고리즘?" → 아니요(IETF 표준),
  "OS 암호화 대신/추가로 표준 알고리즘을 직접 구현?" → 아니요(모두 CryptoKit). 그래서 면제, plist `false` 유지.
- 주의: CryptoKit 밖의 암호 라이브러리를 앱에 넣거나 독자 알고리즘을 쓰게 되면 다시 판단해야 해요.

## 11. 연령 등급 설문 / Age rating (2025년 새 설문)

ASC API `ageRatingDeclaration`의 항목 그대로예요(지금 전부 비어 있음). Apple 정의는 "Age ratings values and definitions" 페이지 기준이에요.
**AI 전용 질문은 없어요.** AI 챗봇은 "앱 안 콘텐츠"의 빈도 질문과 웹 접근·사용자 콘텐츠 질문으로 판단돼요.

### 앱 안 제어 기능 (In-app controls)
| 항목 | 답 | 이유 |
|---|---|---|
| Parental Controls | 아니요 | 보호자 제어 기능 없음 |
| Age Assurance | 아니요 | 연령 확인 기능 없음 |

### 기능 (Capabilities)
| 항목 | 답 | 이유 |
|---|---|---|
| **Unrestricted Web Access** | **예** | 정의: "Users can navigate to any webpage within the app or freely browse the web. May include: embedded browser functionality". 작업 화면에서 에이전트의 브라우저를 실시간으로 보고 **직접 이어받아 조작**할 수 있어요(Play도 "예"). → **16+** |
| User-Generated Content | 아니요 | 정의는 "broad distribution of content created by users". Kelo는 내 서버의 내 대화만 있고 다른 사람에게 퍼지지 않아요 |
| Messaging and Chat | 아니요 | 정의는 "Users can directly communicate **with one another**". AI와의 대화이고, 사용자끼리 연결되지 않아요 |
| Social Media | 아니요 | 피드·공유 없음 |
| Advertising | 아니요 | 광고 없음 |

### 콘텐츠 빈도 (None / Infrequent / Frequent)
| 항목 | 답 | 이유 |
|---|---|---|
| Profanity or Crude Humor | 없음 | 앱이 제공하는 콘텐츠 없음(답은 사용자가 고른 모델이 사용자 요청에 따라 씀) |
| Horror/Fear Themes | 없음 | 〃 |
| Alcohol, Tobacco, or Drug Use or References | 없음 | 〃 |
| Mature or Suggestive Themes | 없음 | 〃 |
| Sexual Content or Nudity / Graphic | 없음 / 아니요 | 〃 |
| Cartoon or Fantasy Violence / Realistic Violence / Prolonged Graphic Violence | 없음 / 없음 / 아니요 | 〃 |
| Guns or Other Weapons | 없음 | 〃 |
| Medical or Treatment Information | 없음 | 의료 앱이 아니고 HealthKit도 없음. (일반 비서라 건강 질문에 답할 수는 있어요 — 아래 결정 참고) |
| Health or Wellness Topics | 아니요 | 〃 |
| Simulated Gambling / Contests | 없음 / 없음 | |
| Gambling / Loot Boxes | 아니요 / 아니요 | 에이전트가 웹사이트에서 대신 결제할 수 있지만 도박 기능이 아니에요(일반 사이트 구매, 승인 필수) |

### 결과와 권장
- 위 답이면 Apple 계산 등급은 **16+**(제한 없는 웹 접근 때문). 한국은 별도 등급(GRAC 값)으로 표시돼요.
- **권장: "Override to Higher Age Rating" → 18+.** 이유: 결제 카드 저장·대리 결제, 제한 없는 웹, 무엇이든 답하는 AI 모델, 서버를 직접 운영하는
  성인용 도구라서 Play에서도 대상 연령을 18세 이상으로 정했어요. 18+로 올리면 "Medical"·"Mature" 같은 빈도 질문을 AI 답 기준으로 어떻게 볼지에
  대한 다툼도 줄어요. (소유자 결정)
- Age Suitability URL(선택): 비워 둬도 돼요.
- Made for Kids: 아니요.

---

## 12. 심사 메모 / App Review notes (영어, `docs/review/notes-apple.txt` 기반)

ASC → 앱 심사 정보: **로그인 필요: 예**. 사용자 이름 칸 = 서버 주소, 비밀번호 칸 = 로그인 코드. 메모는 아래를 붙여 넣어요.
`{…}`는 `assistant review` 명령이 만든 값으로 바꿔요(심사 서버는 `review-apple` 사본, `docs/review/README.md`).

```
ABOUT KELO
Kelo is the app for a personal AI assistant that its owner runs on their own server (self-hosted, with Docker). The app holds no
assistant itself: it connects to the owner's server, and everything happens there. There is no account with us: the app pairs with a
server, the way a remote pairs with a TV. For this review we run a demo server just for App Review, with an AI model already set up,
so you can use the app right away. It has no real accounts, cards or personal data.

HOW TO CONNECT (about 1 minute)
1. Open Kelo. The first screen is "Get started".
2. Tap "Paste link".
3. Enter this link exactly (it is the server address followed by the sign-in code):
   {link}
4. Tap "Connect", then "Connect" again to confirm the server. The chat opens.

Server address: {address}
Sign-in code:   {code}  (not case-sensitive; the dashes are optional)

The code works until {expires} and for up to {uses} devices. Every device that connects with it joins the same demo assistant.

BEFORE THE FIRST MESSAGE (guideline 5.1.2(i))
When you send your first message, Kelo first shows "Before your first message": it says that your messages, attached files and
results from connected services go to the AI service chosen in Model (on this demo server: {provider_company}), links to the
privacy policy, and offers Agree / Not now. With "Not now" nothing is sent and your text stays in the box. After "Agree" the message
is sent. The answer is stored once on the server; the model picker and My > Model always show "Your chats go to <service>".

WHAT TO TRY
- Chat: ask anything, for example "What can you do?", "Plan a 3-day trip to Seoul" or "Summarize the news about electric cars".
  Replies come from a real AI model, so they take a few seconds.
- Work tab: running tasks; you can watch the assistant's own browser live and take over.
- Connectors tab: the services Kelo can work with (calendars, mail, GitHub, Notion and more). Connecting one is optional and uses your
  own account on that service; nothing is needed for the review.
- Schedules: ask "Remind me in 5 minutes to stretch". The result arrives in the chat. (Notifications: if you allow them, Kelo can tell you when a result is ready. The banner is a fixed line, "Kelo has an update"; the
  details come from your own server when you open the app. Push needs the operator's push service to be set up; the review server
  does not depend on it.)
- My tab: the model in use and where chats go, devices, Keychain, and the app's settings.

PERMISSIONS
Each is asked only when you use the feature: camera (pairing QR code, card scan, taking a photo to send), microphone and speech
recognition (voice input), Face ID (Keychain and cards), calendar and contacts (only after turning them on in Connectors > On this
iPhone), location (briefing weather and "near me" questions; sent only to the owner's own server). None is needed for the review.

PURCHASES
Kelo has no in-app purchases and sells nothing. It can prepare an order on a website, but it always shows an Allow / Deny card with
the item and the total before anything that costs money, and it never pays without that approval. No payment card is saved on this
demo server.

REPORTING A REPLY
Touch and hold any reply from Kelo and choose "Report", pick the reason and send it. Reports go to the Kelo developer.

DATA AND ACCOUNTS
Chats and files are stored on the owner's server, not by the developer. To stop using a server, My > My devices > disconnect this
device; the owner can delete the chat history in My or remove the server. No account is created in the app, so there is no account
to delete.

CONTACT
support@kelo.chat
```

> 새 자리 표시: `{provider_company}` — 심사 서버의 모델 회사(예: Google). 템플릿 파일 `docs/review/notes-apple.txt`에는 아직 반영하지
> 않았어요(이 문서가 초안). 승인되면 템플릿에 옮기세요.

---

## 13. 가이드라인 위험 점검표

| 가이드라인 | Kelo가 지키는 방법 / 빠진 것 | 위험 |
|---|---|---|
| **2.1 App Completeness** — "include demo account info (and turn on your back-end service!) if your app includes a login" | 심사용 `review-apple` 사본 + 로그인 코드(12장). **빠진 것: 심사 서버를 만들고 심사 기간 내내 켜 두기, 모델 키 한도 설정.** 설치 스크립트(kelo.chat/install.sh)가 출시 때 열려 있어야 설명과 맞아요. 지원 URL 페이지 없음(6장) | 중 |
| **2.5.2** — 코드 내려받아 실행 금지 | 앱은 SwiftUI 네이티브, 웹뷰·JavaScriptCore 없음(grep 확인). 서버가 보내는 건 데이터(JSON, 마크다운, 브라우저 화면 이미지)뿐. 스킬·Nest 패키지는 **서버**에서 돌고 앱의 기능을 바꾸지 않아요 | 낮음 |
| **3.1.1 In-App Purchase** | 앱에서 파는 것 없음, 잠금 해제 없음, 구독 없음, 외부 결제 유도 문구 없음. 에이전트의 웹사이트 결제는 실물·외부 서비스 구매라 3.1.1 대상 아님(3.1.5 범주) | 낮음 |
| **4.2 Minimum Functionality** | 웹사이트 포장이 아니라 네이티브 앱(채팅, 승인 카드, 라이브 뷰, 공유 확장, Face ID 키체인, 캘린더·연락처·위치 연동). 다만 **서버 없이는 아무것도 안 돼서** "앱만으로 쓸 수 없음"(4.2.3(i) "work on its own without requiring installation of another app")을 지적받을 수 있어요. 다른 *앱*이 아니라 서버 소프트웨어이고, 3.1.3(f)의 "companion" 앱들(VoIP·웹 호스팅 클라이언트)과 같은 구조라고 메모에 써 두었어요 | 중 |
| **5.1.1 Data Collection and Storage** | 권한은 기능을 쓸 때만, 목적 문구 20개 언어. 개인정보처리방침 URL 있음. **빠진 것: 방침에 iPhone 앱 내용 없음(9-5), 앱 마이 화면에 방침 링크 없음** | 중 |
| **5.1.1(v) Account Sign-In / 계정 삭제** — "If your app supports account creation, you must also offer account deletion within the app" | 앱에서 계정을 만들지 않아요(서버와 페어링). 그래서 계정 삭제 의무 없음. 기기 연결 끊기, 대화 기록 삭제, 이 기기에서 항목 끄기(서버 사본 삭제)가 앱에 있어요. 메모에 적어 둠 | 낮음 |
| **5.1.2(i) Data Use and Sharing** — "You must clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so." | **이번에 만들었어요**(아래 14장). 첫 메시지 전 동의 화면: 무엇을, 어디로(지금 고른 서비스 이름), 방침 링크, 동의/나중에. 나중에 = 아무것도 안 보냄. 서버에 한 번 저장(기기·시각). 모델 선택 화면과 모델 페이지에 "대화를 보내는 곳: …" 상시 표시 | 낮음~중 (14-3) |
| **4.8 Login Services (Sign in with Apple)** | 앱의 "주 계정"이 없어요(서버 페어링 코드·QR). Google/GitHub 로그인은 **커넥터**(그 서비스의 데이터를 쓰려는 연결)이지 Kelo 계정 로그인이 아니에요 → 4.8 대상 아님(예외 "client for a specific third-party service … sign in to their mail … account directly to access their content"에도 해당) | 낮음 |
| 1.2 / AI 생성 콘텐츠 | 답 길게 누르기 → 신고 → 개발자 서버, 운영자 화면에서 처리. 연락처 support@kelo.chat | 낮음 |
| **5.1.1(ix) 규제 분야** — 금융 서비스는 법인이 제출해야 함 | 결제 카드 저장 + 에이전트 대리 결제. Kelo는 돈을 보관·송금하지 않지만, 심사자가 "금융 서비스"로 보면 **개인 개발자 계정**이 문제될 수 있어요. 메모에 "외부 상점에서 사용자의 카드로, 매번 승인" 명시 | 중 |
| 2.3.10 / 2.3.7 메타데이터 | 다른 플랫폼 언급 없음, 키워드에 다른 상표 없음, 스크린샷은 실제 화면(다른 에이전트 작업) | 낮음 |
| 2.5.4 백그라운드 | `UIBackgroundModes: fetch`만, BGTask로 알림 확인. 오디오·위치 백그라운드 없음 | 낮음 |

---

## 14. AI 데이터 공유 동의 (가이드라인 5.1.2(i)) — 확인 결과와 만든 것

### 14-1. 지금 가이드라인 문구

App Review Guidelines (https://developer.apple.com/app-store/review/guidelines/, 페이지 표시 **"Last Updated: October 16, 2026"**,
2026-11-10에 확인). "third-party AI" 문구는 **2025년 11월 13일** 개정에서 들어왔어요.

> **5.1.2 Data Use and Sharing (i)** Unless otherwise permitted by law, you may not use, transmit, or share someone's personal data
> without first obtaining their permission. You must provide access to information about how and where the data will be used.
> **You must clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit
> permission before doing so.** … Your app may not require users to enable system functionalities (such as push notifications, location
> services, tracking) in order to access functionality …

### 14-2. 확인 결과 (만들기 전)

iPhone 앱은 **첫 사용 전에 알리고 동의를 받는 단계가 없었어요.** 있던 것:
- 마이 → 개인정보 페이지의 문장 "What you send in a chat does go to the AI service you picked" — 사용자가 찾아 들어가야 보이고 동의를 받지 않음.
- 모델 화면의 "비공식 제공자" 동의(Claude/ChatGPT 구독 로그인 등) — **약관 위험** 안내이지 데이터 공유 동의가 아니고, API 키 제공자에는 안 나옴.
→ 5.1.2(i)의 "clearly disclose … and obtain explicit permission before doing so"에 못 미쳐서, 세 앱에 동의 단계를 만들었어요.

### 14-3. 만든 것 (소유자 설계: 한 번만 동의 + 상시 표시)

- **첫 메시지 전에 한 번**: 앱이 보내는 모든 메시지에 `"ai_consent": "check"`가 붙고, 이 서버에서 아직 아무도 동의하지 않았으면 서버가
  메시지를 **저장도 전송도 하기 전에** 428 `ai_consent_required`로 돌려보내요. 앱은 말풍선을 거두고 글을 입력창에 되돌린 뒤 묻어요:
  - 제목 "첫 메시지를 보내기 전에 / Before your first message"
  - "Kelo는 답을 받으려고 메시지와 거기에 필요한 것(첨부한 파일, 연결한 서비스에서 가져온 결과)을 내가 고른 AI 서비스로 보내요."
  - "지금 고른 서비스: Google. 모델에서 보고 바꿀 수 있어요." (지금 고른 회사 이름)
  - "나중에를 누르면 아무것도 보내지 않아요." + **개인정보처리방침** 링크(https://kelo.chat/privacy)
  - **동의** → 서버에 기록(`PUT /api/ai-consent`) 후 같은 메시지를 보내요. **나중에**(또는 닫기) → 아무것도 안 보내고 글은 입력창에 남아요.
- **서버에 한 번 저장**: `meta`의 `ai_consent` = 동의 시각, 동의한 기기 id, 그때 고른 회사, 버전. 다른 기기는 다시 묻지 않아요. 로그에 기기와
  회사가 남아요. `DELETE /api/ai-consent`로 철회하면 다음 메시지에서 다시 물어요.
- **제공자 바꿀 때는 팝업 없음**: 대신 모델 선택 시트와 마이 → 모델 페이지의 선택한 제공자 아래에 늘 한 줄
  "대화를 보내는 곳: Google / Your chats go to Google" (로컬 모델이면 "대화는 내 네트워크 밖으로 나가지 않아요"). `GET /api/providers`에
  `company` 필드가 생겼어요.
- 내 네트워크의 모델(LM Studio, Ollama, 사설 주소의 사용자 지정 엔드포인트)만 쓰면 제3자가 없으므로 묻지 않아요. 나중에 인터넷의 서비스를
  고르면 그때 첫 메시지 전에 한 번 물어요.
- API 클라이언트·옛 앱(필드를 안 보냄)은 막지 않아요(호환). 심사자가 쓰는 새 iPhone 앱은 늘 확인해요.

### 14-4. 왜 이 방식이 5.1.2(i)를 만족한다고 보는지

문구는 두 가지를 요구해요: **"clearly disclose where personal data will be shared with third parties, including with third-party AI"**,
그리고 **"obtain explicit permission before doing so"**.
- *where를 분명히 알림*: 동의 화면이 무엇이(메시지·첨부·연결 서비스 결과) 어디로(사용자가 고른 AI 서비스, **지금은 그 회사 이름**) 가는지
  말하고 방침을 링크해요. 이후에도 "where"는 모델 화면에 **늘** 보여요 — 받는 곳이 바뀌면 그 화면에 바로 새 이름이 나와요.
- *보내기 전에 명시적 허락*: 동의 버튼을 누르기 전에는 서버가 메시지를 저장·전송하지 않아요(서버 테스트로 확인). "나중에"는 거절로 처리돼요.
- *받는 곳이 바뀔 때*: 새 회사로 보내는 건 **사용자가 직접 그 회사를 고르는 행동**(모델 화면에서 선택, 그 아래 "대화를 보내는 곳: X")에서만
  일어나요. 앱이 몰래 바꾸지 않아요. 고르는 행동 자체가 그 회사로 보내겠다는 명시적 선택이에요.
- 문구에 "**each** third party마다 따로" 같은 말은 없어요. "where … will be shared"를 알리고 "explicit permission"을 받으라는 거예요.
  그래서 **회사마다 따로 동의해야 한다는 요구로 읽히지는 않는다**고 판단했어요.
- 남는 위험(솔직히): 심사자가 "동의할 때 이름이 나온 회사(예: Google)에 대해서만 허락받은 것"으로 엄격히 읽을 수 있어요. 그 경우 대응은
  간단해요: 서버의 기록에 회사가 이미 남으니, `ai_consent.required()`에서 "현재 회사 ≠ 기록된 회사"일 때도 428을 주게 한 줄만 바꾸면 회사가
  바뀔 때마다 다시 물어요(앱 쪽은 그대로 동작). 처음 제출은 소유자 설계대로 내고, 이 이유로 거절되면 그렇게 바꾸는 걸 권해요.
