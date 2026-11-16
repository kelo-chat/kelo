# 내려받은 앱이 공개된 소스와 같은지 확인하기

영어 원문: [../verify-builds.md](../verify-builds.md)

Kelo의 소스 코드는 공개돼 있어요. 이 문서는 내 폰이나 컴퓨터에 있는 앱이 그 소스로 만들어졌는지 직접 확인하는 방법을 알려 줘요.
어디까지 확실하게 확인되는지, 어디부터 약한지, 어디는 아예 불가능한지도 그대로 적었어요.

| 가진 것 | 확인할 수 있는 것 | 확실한 정도 |
|---|---|---|
| Google Play의 Android 앱 | 바이트 단위 비교는 안 돼요. Google Play가 배포하는 앱에 자체 보호 코드를 넣어요(자동 보호, Kelo는 켜 둠). 확인하려면 kelo.chat의 APK를 쓰세요 | Play 버전 자체는 확인 불가 |
| kelo.chat의 Android APK | 태그가 붙은 소스를 직접 빌드해서 내 폰의 APK와 비교 | 서명만 빼고 바이트 단위로 같음 |
| Windows 설치 파일 (MSI, EXE, MSIX) 또는 설치된 앱 | 서명만 빼고 앱의 모든 파일을 소스와 비교 (`tools/verify/windows-verify.ps1`) | 코드는 확실, 서명 자체는 확인 안 됨. 2차 확인: `gh attestation verify` (출처 증명) |
| macOS 디스크 이미지 | 아직 없음 (운영자의 Apple 인증서가 필요해요) | 아직 없음 |
| iPhone 앱 | 읽을 수 있는 부분을 소스와 비교 | 약함. 바이트 단위 비교는 불가능 (아래 참고) |
| 서버 이미지 | `cosign verify` (`release-images.yml` 참고) | 공개 워크플로가 서명 |

무엇을 확인하든 Kelo가 아닌 두 가지는 믿어야 해요. 하나는 도구(Docker, JDK, Google의 Android 빌드 도구, Gradle과 라이브러리.
아래처럼 체크섬으로 고정해 뒀어요), 다른 하나는 내가 궁금한 부분의 소스를 실제로 읽어 봤다는 점이에요. 이 확인이 증명하는 건
"파일이 그 소스를 빌드한 결과와 같다"는 것이지, 소스에 실수가 없다는 뜻은 아니에요.

## Android

### 명령 하나로

Docker, `git`, `adb`(Android platform-tools), Java 17 이상(`bundletool`용), `python3`, `curl`이 필요해요. 폰을 USB 디버깅을 켠
상태로 연결하고(`adb devices`에 보여야 해요) 실행하세요.

```sh
git clone https://github.com/kelo-chat/kelo && cd kelo
git checkout <내 버전의 태그>                 # 버전은 My 페이지에 있고, 태그는 GitHub에 있어요
tools/verify/android-verify.sh                # 폰의 버전으로 태그를 찾아요. 직접 정하려면 --tag <태그>
```

스크립트가 하는 일:

1. 폰에 앱의 버전과 설치한 곳(Google Play인지 파일인지)을 물어봐요.
2. 태그의 소스를 받아서 `apps/android/kelo/version.properties`의 버전이 폰과 같은지 확인해요.
3. 고정된 Docker 이미지에서 `tools/build/android-reproducible.sh`로 빌드해요. 처음 빌드는 오래 걸려요. Apple 실리콘 Mac에서는
   Linux x86-64 이미지를 에뮬레이션으로 돌려서 몇 시간이 걸리고, x86-64 PC에서는 훨씬 빨라요.
4. **Google Play 버전** (2026-11-15부터 Kelo는 Google Play 자동 보호를 켜 두어서 Google이 Play 빌드에 검사 코드를 넣어요. 그래서 이 비교는 코드 차이를 보고해요. 확인은 kelo.chat의 APK로 하세요. 아래 단계는 보호가 없는 빌드용으로 남겨 둬요): `bundletool build-apks --connected-device`가 번들에서 내 폰이 받아야 할 APK를 만들고, `adb`가 폰에
   설치된 APK(`base.apk`와 `split_config.*.apk`)를 가져오고, `tools/verify/apkdiff.py`가 같은 조각끼리(base, `config.arm64_v8a`,
   `config.xxhdpi`, `config.en` 등) 짝지어 비교해요. **파일 버전:** APK 하나를 폰의 APK와 비교해요.
5. `APKs match!` 또는 다른 점을 보여 주고, 같으면 0, 다르면 1로 끝나요.

`apkdiff.py`가 비교에서 빼는 것은 아래뿐이에요. `META-INF/MANIFEST.MF`와 `META-INF/*.SF|RSA|DSA|EC`(jar 서명. APK 서명 블록은 zip 안의
파일이 아니에요), `stamp-cert-sha256`(Google Play 도장), 그리고 `AndroidManifest.xml` 안에서 Google Play가 넣는 `<meta-data>`
(`com.android.vending.derived.apk.id`, `com.android.stamp.source`, `com.android.stamp.type`). 파일은 압축을 푼 바이트로 비교해서
zip 압축 방식이나 파일 순서가 달라도 상관없어요. Google Play는 자기 키로 서명하므로 서명은 어떻게 해도 같을 수 없어요. 이 방법으로
확인할 수 없는 것이 바로 서명이에요.

조각이 없다거나 더 있다고 나오면, 빌드가 연결된 기기를 기준으로 만든 언어·화면 밀도 목록과 폰의 목록이 다른 거예요(Google Play는
설정된 언어를 모두 봐요). 양쪽에 다 있는 파일을 비교한 결과를 보세요. 한쪽에만 있는 조각이 앱이 바뀌었다는 뜻은 아니에요.

### 직접 하나씩

```sh
git clone https://github.com/kelo-chat/kelo && cd kelo && git checkout <태그>
tools/build/android-reproducible.sh --targets play-aab --out build-out   # 또는 sideload-apk
java -jar bundletool-all-1.18.3.jar build-apks --bundle=build-out/kelo-0.1.N-play-release-unsigned.aab \
     --output=mine.apks --connected-device --overwrite
mkdir phone && for p in $(adb shell pm path chat.kelo.app | tr -d '\r' | sed 's/^package://'); do adb pull "$p" phone/; done
python3 tools/verify/apkdiff.py --splits mine.apks phone
```

kelo.chat의 APK는 `tools/build/android-reproducible.sh --targets sideload-apk`로 빌드한 뒤
`python3 tools/verify/apkdiff.py build-out/kelo-0.1.N-sideload-release-unsigned.apk <설치한 APK>`로 비교해요. 서명 지문은
운영자의 업로드 키예요. `apksigner verify --print-certs <apk>`로 볼 수 있어요.

### 스크립트 없이: CI

릴리스 태그마다 GitHub Actions가 같은 Docker 환경에서 공개적으로 빌드하고([`release-build.yml`](../../.github/workflows/release-build.yml)),
파일마다 증명이 붙어요.

```sh
gh attestation verify kelo-0.1.N-play-release-unsigned.aab -R kelo-chat/kelo
```

그 실행이 만든 파일의 SHA-256은 실행 요약과 `SHA256SUMS`에 있어요. 내가 빌드한 `SHA256SUMS`와 비교하세요. `-unsigned` 파일은
바이트 단위로 같아요. 운영자는 올리기 전에 서명만 더해요.

### 어디서나 같은 결과가 나오게 한 방법

| 달라질 수 있는 것 | Kelo가 한 일 |
|---|---|
| 빌드 시각으로 정한 버전 | 버전은 `apps/android/kelo/version.properties`(`build=N`이면 versionCode N, versionName `0.1.N`)에서 읽고 릴리스마다 함께 커밋해요. `tools/build/android.sh version`이 써 줘요. "2026 후반부터의 분" 규칙은 운영자가 릴리스를 시작할 때 번호를 고르는 데만 써요 |
| JDK, Gradle, Android SDK, 빌드 도구 | `apps/android/reproducible/Dockerfile`: JDK 이미지는 digest로, Android 플랫폼 36(r02)과 빌드 도구 36.0.0은 SHA-256으로, Gradle 8.11.1은 wrapper의 `distributionSha256Sum`으로 고정 |
| 마이에 보이는 커밋 | 마이의 버전 줄 끝에는 앱이 빌드된 커밋의 짧은 해시가 있고, 누르면 GitHub의 그 커밋으로 가요(`BuildConfig.SOURCE_COMMIT`, `ReleaseInfo.SOURCE_COMMIT`, Info.plist `KeloSourceCommit`). `tools/build/android-reproducible.sh`가 빌드하는 커밋의 sha를 컨테이너 안으로 넘겨서(`-PkeloCommit`) 같은 커밋이면 결과도 같아요. `--worktree` 빌드는 "수정됨"으로 표시되고 링크가 없어요. sha는 파일의 일부라서, `tools/build/android.sh`가 다른 커밋에서 만든 APK는 태그의 빌드와 바로 이 값만 달라요 |
| 라이브러리와 플러그인 | `gradle/libs.versions.toml`에 정확한 버전, 내려받는 파일은 모두 `gradle/verification-metadata.xml`과 대조(재현 빌드에서는 엄격하게) |
| 번들 안의 R8 시간 기록 | R8이 실행 시간과 스레드 수를 `BUNDLE-METADATA/com.android.tools/r8.json`에 적어요. 이 두 숫자를 비워요(`app/build.gradle.kts`). 번들을 두 번 빌드했을 때 다른 곳은 여기뿐이었어요 |
| APK 안의 암호화된 의존성 목록 | `dependenciesInfo { includeInApk = false }`: 파일 APK에는 빌드마다 바뀌는 난수가 없어요(번들은 평문 목록을 그대로 둬요) |
| 소스 폴더, 사용자, 시간대, 기계 | 시험함: 서로 다른 폴더 두 곳, 컨테이너 안의 다른 폴더, 시간대 두 개에서 같은 파일 |
| 네이티브 라이브러리 | 이미지에 NDK가 없어서 네이티브 라이브러리를 그대로 둬요. NDK가 있으면 strip하는데(이것도 결과는 같지만), 둘을 섞으면 안 돼요 |
| Firebase 설정 | `apps/android/kelo/app/google-services.json`을 소스에 포함해요(다음 절) |

확인되지 않는 것: Google Play 서명(Play가 자기 키로 서명해요), 그리고 업로드 뒤에 Google Play가 위의 도장 말고 더 바꾸는 것. 실제 Play 설치로는
아직 시험하지 못했어요. 비교 과정은 에뮬레이터에 `bundletool`로 설치한 번들로 시험했어요. 첫 Play 릴리스가 나오면 운영자가
`android-verify.sh`를 한 번 돌려 보고 이 문서에 결과를 적을 거예요.

### Firebase 파일과 내가 직접 빌드할 때

푸시 알림에는 `apps/android/kelo/app/google-services.json`이 필요해요. 이 파일은 Firebase **클라이언트** 설정이고, 모든 APK 안에 들어
있는 것과 같은 파일이라 공개돼도 괜찮아서 소스와 함께 커밋해요. 그래서 소스를 빌드하면 스토어의 APK와 같은 APK가 나와요. 안에는
프로젝트 번호와 ID, 앱 ID, 클라이언트 API 키가 있어요(Google Cloud에서 이 앱만 쓸 수 있게 제한해 두세요). 서비스 계정 키나 비밀은 없고,
푸시를 보내는 키는 운영자의 서버에만 있어요.

**내 Firebase 프로젝트**로 빌드하려면 이 파일을 내 것으로 바꾸세요(Firebase 콘솔 → 프로젝트 설정 → Android 앱 `chat.kelo.app` →
`google-services.json` 다운로드. 모양은 `google-services.json.example`에 있어요). 푸시 없이 빌드하려면 파일을 지우면 돼요.
이 경우 내 빌드는 스토어 빌드와 바로 이 파일만큼 다르고, 비교에서도 그렇게 나와요.

## Windows

이미 설치된 Kelo(`C:\Program Files\Kelo`), 내려받은 MSI(`Kelo-<version>.msi`), 또는 Microsoft Store용 MSIX를 공개된 소스와
직접 비교할 수 있어요. 설치 파일 자체는 jpackage와 MSI 도구가 시각과 ID를 파일에 적기 때문에 바이트 단위로 재현되지 않지만,
**그 안에 들어 있는 앱의 모든 파일**은 소스를 빌드한 결과와 정확히 같은지 확인할 수 있어요.

Windows PowerShell(5.1 이상) 또는 PowerShell 7에서 이 저장소를 복제한 뒤 실행하세요.

```powershell
powershell -ExecutionPolicy Bypass -File tools\verify\windows-verify.ps1 <Kelo-1.0.9.msi | Kelo-1.0.9.0.msix | "C:\Program Files\Kelo">
```

(EXE 설치 파일은 MSI와 같은 파일을 담고 있으므로 `kelo.chat/download/windows-msi`에서 MSI를 받아 확인하면 돼요.)

스크립트가 하는 일:

1. MSI를 임시 폴더에 관리자 권한 없이 풀거나(`msiexec /a`), MSIX(zip)를 풀거나, 이미 설치된 폴더를 직접 읽어요.
2. 앱의 jar 안에서(`chat.kelo.desktop.core.ReleaseInfo`) 어떤 소스 커밋에서 빌드됐는지와 패키지 번호를 읽어요(직접 지정하려면 `-Commit <40자리 해시>`).
3. GitHub에서 해당 커밋의 소스를 내려받거나(zip이라 PC에 `git`이 없어도 돼요), `-Source <폴더>`로 지정한 소스를 써요.
4. `apps/desktop/packaging/windows/JDK`에 고정된 Java 런타임(Eclipse Temurin, `%LOCALAPPDATA%\kelo\jdk`에 한 번 내려받고 SHA-256 확인)과 고정 zip 시각 도구(`tools/build/zipclock`)를 써서 `gradlew createDistributable`로 서명 없이 빌드해요.
5. 두 앱 폴더를 파일 단위로, jar 안은 항목 단위로 비교해요(`tools/verify/appdiff.py --windows`). Python 3는 PC에 설치된 것을 쓰거나 고정된 작은 임베디드 Python(`apps/desktop/packaging/windows/PYTHON`)을 자동으로 내려받아요.

모든 파일이 같으면 0으로 끝나고, 다른 파일이 있으면 목록을 출력하고 1로 끝나요.

### 비교에서 빼는 것과 그 이유

- **Authenticode 서명:** `.exe`와 `.dll` 파일(jar 안의 `skiko.dll` 등 포함)은 끝에 붙는 인증서 테이블과 PE 헤더 체크섬을 양쪽 모두 제거하고 비교해요. 서명은 운영자의 인증서로만 만들 수 있어 다른 사람이 재현할 수 없으므로, 실행 코드와 데이터만 비교해요.
- **MSIX 패키징 파일:** `AppxManifest.xml`, `AppxSignature.p7x`, `AppxBlockMap.xml`, `[Content_Types].xml`, `AppxMetadata/`, `Assets/`(스토어용 이미지)는 MSIX 패키징 도구가 앱 바깥에 덧붙이는 파일이므로 비교에서 제외해요.

### 2차 확인: 출처 증명 (Provenance)

설치 파일 자체에는 타임스탬프와 고유 ID가 들어가 바이트 단위 재현이 되지 않으므로, 릴리스 태그마다 GitHub Actions가
[`release-build.yml`](../../.github/workflows/release-build.yml)에서 `.msi`, `.exe`, `.msix`에 빌드 증명(attestation)을 붙여요.

```sh
gh attestation verify Kelo-1.0.9.msi -R kelo-chat/kelo
```

이 증명은 GitHub Actions가 `desktop-windows.yml` 워크플로를 사용해 공개 저장소의 해당 커밋에서 빌드했음을 증명해 줘요. 설치 파일에는 아직 코드 서명이 없어서 Windows SmartScreen이 먼저 물어봐요.

## macOS

디스크 이미지는 운영자의 Mac에서 만들고, 아직 서명도 공증도 없어서 소스 말고 확인할 방법이 없어요. Developer ID 인증서가 생기면
같은 워크플로로 빌드하고 증명을 붙일 거예요.

## iPhone

App Store 앱은 바이트 단위 비교가 **불가능**해요. Apple이 앱을 계정마다 암호화(FairPlay)하고 다시 서명하고, 기기에 맞게 잘라서
주기 때문에, 운영자를 포함해 누구도 소스에서 내 폰에 있는 그 파일을 만들 수 없어요. 우리가 무엇을 만들어도 이건 바뀌지 않고,
바뀐다고 말하면 거짓이에요.

대신 확인할 수 있는 것:

- **읽을 수 있는 부분.** App Store 앱의 `Info.plist`, 권한(entitlements), 리소스, 번역 문구, 연결된 프레임워크 목록은 암호화되지
  않아요. 앱 식별자와 팀, 요청하는 기능과 권한(이유 문구 포함), 연결된 프레임워크가 보여서 분석이나 광고 프레임워크가 들어 있는지
  알 수 있어요. 소스의 `apps/ios/`와 비교하세요.
- **앱이 하는 일.** 폰을 내가 통제하는 프록시 뒤에 두고 앱이 어디에 접속하는지 보세요. 내가 알려 준 서버와, 개인정보 페이지에 적힌
  용도의 kelo.chat이어야 해요.
- **직접 빌드.** `apps/ios/`는 내 서명으로 Xcode에서 빌드돼요. 내가 만든 앱은 그 소스의 앱이고, 스토어 앱 옆에서 돌려 비교할 수 있어요.
- **서버 쪽.** 내 데이터에는 내가 운영하는 서버가 가장 중요해요. 서버 이미지는 공개 워크플로에서 빌드하고 서명해요.

만들지 않은 아이디어: 살펴볼 수 있는 파일이 필요한 사람을 위해 CI에서 만든 iPhone 아카이브에 증명을 붙이는 것. 그래도 그건 App Store 파일은 아니에요.

## 앱 안의 업데이트

kelo.chat에서 받은 Android APK와 Windows·macOS 앱은 kelo.chat에서 새 버전이 있는지 확인해요. 웹사이트만 믿지는 않아요.
`https://kelo.chat/download/latest.json`의 각 항목에는 Ed25519 서명이 붙어 있고, 이 서명은 서버에 올라가지 않는 키로 운영자
본인의 컴퓨터에서 만들어요. 앱에는 짝이 되는 공개 키가 들어 있어서, **서명이 맞지 않는 항목은 아예 보여 주지 않아요.** 서명이
없거나, 다른 키로 서명했거나, 서명한 뒤에 바뀐 항목은 없는 항목처럼 취급해서 아무것도 표시하지 않아요. 파일은 목록을 받아 온
바로 그 주소에서, https로만 내려받아요(내 컴퓨터에서 도는 테스트용 목 서버만 http를 써요). 내려받은 파일의 sha256과 크기는
서명된 값과 같아야 해요. Google Play와 App Store 빌드는 각 스토어가 업데이트해요.

서명이 덮는 내용은 정확히 아래 텍스트예요(UTF-8, 줄바꿈은 `\n`, 마지막에 `\n` 하나).

```
kelo-release-v1
platform=<android | windows | windows-msi | mac>
file=<파일 이름>
version=<버전>
version_code=<빌드 번호, 없으면 비움>
size=<바이트>
sha256=<파일의 소문자 16진수>
commit=<16진수 40자, 없으면 비움>
```

`commit`은 그 파일을 만든 공개 소스 커밋(`kelo-chat/kelo`)이에요. 그래서 앱이 설치하는 업데이트가 여러분이 읽을 수 있는 소스와
이어져요. Android는 위 절들대로 다시 빌드해 볼 수도 있어요. `latest.json`의 항목에는 이 값이 `"commit"`으로, 그 옆에
`"sig"`(서명, base64)와 `"key"`(키 ID: 원본 공개 키 sha256의 앞 16자리 16진수)로 들어가요. 커밋 없이 올린 항목은 `commit=` 줄이
비어 있어요.

공개 키는 `apps/shared/src/commonMain/kotlin/chat/kelo/app/update/ReleaseKeys.kt`에 있어요(키 ID → 원본 32바이트 키의
base64). 서버에도 같은 목록이 `server/src/assistant/release_keys.py`에 있어요. 키를 두 개까지 함께 적어 둘 수 있어서, 키를 바꿀
때 한꺼번에 갈아엎지 않아도 돼요.

항목을 직접 확인하려면(`pip install cryptography`, `ReleaseKeys.kt`의 공개 키를 `PUB`에 넣으세요):

```python
import base64, hashlib, json, urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
PUB, platform = "<ReleaseKeys.kt의 base64 공개 키>", "windows"       # android | windows | windows-msi | mac
r = json.load(urllib.request.urlopen("https://kelo.chat/download/latest.json"))[platform]
pub = base64.b64decode(PUB)
assert hashlib.sha256(pub).hexdigest()[:16] == r["key"], "다른 키로 서명됨"
text = (f"kelo-release-v1\nplatform={platform}\nfile={r['file']}\nversion={r['version']}\nversion_code={r.get('version_code', '')}\n"
        f"size={r['size']}\nsha256={r['sha256']}\ncommit={r.get('commit', '')}\n")
Ed25519PublicKey.from_public_bytes(pub).verify(base64.b64decode(r["sig"]), text.encode())   # 틀리면 InvalidSignature
print("서명 확인됨;", "만든 커밋 " + r["commit"] if r.get("commit") else "커밋 표시 없음")
```

그다음 내려받은 파일의 `shasum -a 256 <파일>` 값을 `sha256`과 비교하세요.

## 운영자용: 릴리스할 때

1. `tools/build/android.sh version`(`apps/android/kelo/version.properties`를 써 줘요), `CHANGELOG.md` 갱신, 커밋, 태그.
2. `tools/build/android-reproducible.sh --sign`: 바로 그 커밋에서 Play 번들과 sideload APK를 만들고 업로드 키로 서명해요. 서명한
   번들을 Play에 올리고, 서명한 APK는 kelo.chat에 올려요.
3. 태그를 공개 저장소에 올리면 `release-build.yml`이 같은 파일을 빌드하고 증명을 붙여요. 그 `SHA256SUMS`가 2번의 `-unsigned`
   합과 같아야 해요(비교하고, 다르면 배포하지 마세요).
4. Google Play에 올라간 뒤, Play에서 설치한 폰에서 `tools/verify/android-verify.sh`를 한 번 돌려 보세요.
