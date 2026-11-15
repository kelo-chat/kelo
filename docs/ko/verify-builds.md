# 내려받은 앱이 공개된 소스와 같은지 확인하기

영어 원문: [../verify-builds.md](../verify-builds.md)

Kelo의 소스 코드는 공개돼 있어요. 이 문서는 내 폰이나 컴퓨터에 있는 앱이 그 소스로 만들어졌는지 직접 확인하는 방법을 알려 줘요.
어디까지 확실하게 확인되는지, 어디부터 약한지, 어디는 아예 불가능한지도 그대로 적었어요.

| 가진 것 | 확인할 수 있는 것 | 확실한 정도 |
|---|---|---|
| Google Play의 Android 앱 | 태그가 붙은 소스를 직접 빌드해서 내 폰의 APK와 비교 | 서명만 빼고 바이트 단위로 같음 |
| kelo.chat의 Android APK | APK 하나를 같은 방법으로 비교 | 서명만 빼고 바이트 단위로 같음 |
| Windows 설치 파일 (MSI, EXE, MSIX) | `gh attestation verify`로 어느 워크플로와 커밋이 만들었는지 확인 | 출처 증명. 같은 바이트로 다시 만들 수는 없음 |
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
2. 태그의 소스를 받아서 `android/kelo/version.properties`의 버전이 폰과 같은지 확인해요.
3. 고정된 Docker 이미지에서 `tools/build/android-reproducible.sh`로 빌드해요. 처음 빌드는 오래 걸려요. Apple 실리콘 Mac에서는
   Linux x86-64 이미지를 에뮬레이션으로 돌려서 몇 시간이 걸리고, x86-64 PC에서는 훨씬 빨라요.
4. **Google Play 버전:** `bundletool build-apks --connected-device`가 번들에서 내 폰이 받아야 할 APK를 만들고, `adb`가 폰에
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
| 빌드 시각으로 정한 버전 | 버전은 `android/kelo/version.properties`(`build=N`이면 versionCode N, versionName `0.1.N`)에서 읽고 릴리스마다 함께 커밋해요. `tools/build/android.sh version`이 써 줘요. "2026 후반부터의 분" 규칙은 운영자가 릴리스를 시작할 때 번호를 고르는 데만 써요 |
| JDK, Gradle, Android SDK, 빌드 도구 | `android/reproducible/Dockerfile`: JDK 이미지는 digest로, Android 플랫폼 36(r02)과 빌드 도구 36.0.0은 SHA-256으로, Gradle 8.11.1은 wrapper의 `distributionSha256Sum`으로 고정 |
| 라이브러리와 플러그인 | `gradle/libs.versions.toml`에 정확한 버전, 내려받는 파일은 모두 `gradle/verification-metadata.xml`과 대조(재현 빌드에서는 엄격하게) |
| 번들 안의 R8 시간 기록 | R8이 실행 시간과 스레드 수를 `BUNDLE-METADATA/com.android.tools/r8.json`에 적어요. 이 두 숫자를 비워요(`app/build.gradle.kts`). 번들을 두 번 빌드했을 때 다른 곳은 여기뿐이었어요 |
| APK 안의 암호화된 의존성 목록 | `dependenciesInfo { includeInApk = false }`: 파일 APK에는 빌드마다 바뀌는 난수가 없어요(번들은 평문 목록을 그대로 둬요) |
| 소스 폴더, 사용자, 시간대, 기계 | 시험함: 서로 다른 폴더 두 곳, 컨테이너 안의 다른 폴더, 시간대 두 개에서 같은 파일 |
| 네이티브 라이브러리 | 이미지에 NDK가 없어서 네이티브 라이브러리를 그대로 둬요. NDK가 있으면 strip하는데(이것도 결과는 같지만), 둘을 섞으면 안 돼요 |
| Firebase 설정 | `android/kelo/app/google-services.json`을 소스에 포함해요(다음 절) |

확인되지 않는 것: Google Play 서명(Play가 자기 키로 서명해요), 그리고 업로드 뒤에 Google Play가 위의 도장 말고 더 바꾸는 것. 실제 Play 설치로는
아직 시험하지 못했어요. 비교 과정은 에뮬레이터에 `bundletool`로 설치한 번들로 시험했어요. 첫 Play 릴리스가 나오면 운영자가
`android-verify.sh`를 한 번 돌려 보고 이 문서에 결과를 적을 거예요.

### Firebase 파일과 내가 직접 빌드할 때

푸시 알림에는 `android/kelo/app/google-services.json`이 필요해요. 이 파일은 Firebase **클라이언트** 설정이고, 모든 APK 안에 들어
있는 것과 같은 파일이라 공개돼도 괜찮아서 소스와 함께 커밋해요. 그래서 소스를 빌드하면 스토어의 APK와 같은 APK가 나와요. 안에는
프로젝트 번호와 ID, 앱 ID, 클라이언트 API 키가 있어요(Google Cloud에서 이 앱만 쓸 수 있게 제한해 두세요). 서비스 계정 키나 비밀은 없고,
푸시를 보내는 키는 운영자의 서버에만 있어요.

**내 Firebase 프로젝트**로 빌드하려면 이 파일을 내 것으로 바꾸세요(Firebase 콘솔 → 프로젝트 설정 → Android 앱 `chat.kelo.app` →
`google-services.json` 다운로드. 모양은 `google-services.json.example`에 있어요). 푸시 없이 빌드하려면 파일을 지우면 돼요.
이 경우 내 빌드는 스토어 빌드와 바로 이 파일만큼 다르고, 비교에서도 그렇게 나와요.

## Windows

`release-build.yml`이 릴리스 태그마다 `desktop-windows.yml`을 불러서 `.msi`, `.exe`, `.msix`에 증명을 붙여요.

```sh
gh attestation verify Kelo-1.0.3.msi -R kelo-chat/kelo
```

이 증명은 GitHub Actions가 `kelo-chat/kelo`의 어느 커밋에서 그 워크플로로 만들었고, 그 뒤로 바뀌지 않았다는 것을 보여 줘요.
같은 바이트로 다시 만들 수는 없어요. 설치 도구가 파일에 시각과 ID를 적기 때문이에요. 무엇이 빌드됐는지는 워크플로 파일과 커밋을
읽어서 확인하세요. 설치 파일에는 아직 코드 서명이 없어서 Windows SmartScreen이 먼저 물어봐요.

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
  알 수 있어요. 소스의 `ios/`와 비교하세요.
- **앱이 하는 일.** 폰을 내가 통제하는 프록시 뒤에 두고 앱이 어디에 접속하는지 보세요. 내가 알려 준 서버와, 개인정보 페이지에 적힌
  용도의 kelo.chat이어야 해요.
- **직접 빌드.** `ios/`는 내 서명으로 Xcode에서 빌드돼요. 내가 만든 앱은 그 소스의 앱이고, 스토어 앱 옆에서 돌려 비교할 수 있어요.
- **서버 쪽.** 내 데이터에는 내가 운영하는 서버가 가장 중요해요. 서버 이미지는 공개 워크플로에서 빌드하고 서명해요.

만들지 않은 아이디어: 살펴볼 수 있는 파일이 필요한 사람을 위해 CI에서 만든 iPhone 아카이브에 증명을 붙이는 것. 그래도 그건 App Store 파일은 아니에요.

## 운영자용: 릴리스할 때

1. `tools/build/android.sh version`(`android/kelo/version.properties`를 써 줘요), `CHANGELOG.md` 갱신, 커밋, 태그.
2. `tools/build/android-reproducible.sh --sign`: 바로 그 커밋에서 Play 번들과 sideload APK를 만들고 업로드 키로 서명해요. 서명한
   번들을 Play에 올리고, 서명한 APK는 kelo.chat에 올려요.
3. 태그를 공개 저장소에 올리면 `release-build.yml`이 같은 파일을 빌드하고 증명을 붙여요. 그 `SHA256SUMS`가 2번의 `-unsigned`
   합과 같아야 해요(비교하고, 다르면 배포하지 마세요).
4. Google Play에 올라간 뒤, Play에서 설치한 폰에서 `tools/verify/android-verify.sh`를 한 번 돌려 보세요.
