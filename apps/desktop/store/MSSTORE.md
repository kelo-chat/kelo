# Kelo를 Microsoft Store에 자동으로 올리기

한 번만 설정하면 이 Mac에서 명령 한 줄로 새 MSIX를 Store 제출로 만들어 올릴 수 있어요. 스크립트는 `apps/desktop/store/msstore.py`(파이썬 표준 라이브러리만 써요)예요.
**첫 제출은 반드시 Partner Center에서 직접** 만들어야 해요(`apps/desktop/README.md`의 "Microsoft Store"). 나이 등급 설문까지 끝낸 제출이 한 번 있어야 API를 쓸 수 있어요.

## A. Partner Center에서 API용 앱 만들기

1. https://partner.microsoft.com/dashboard 에 들어가요.
2. 오른쪽 위 **톱니(설정) → 계정 설정 → 사용자 관리**(Users)로 가요.
3. 계정에 Microsoft Entra(옛 Azure AD) 디렉터리가 연결돼 있어야 해요. 안 돼 있으면 **테넌트**(Tenants) 메뉴에서 기존 디렉터리를 연결하거나 새로 만들어요. 디렉터리의 전역 관리자 권한이 필요해요.
4. **Microsoft Entra 애플리케이션**(Azure AD applications) 탭 → **새 Microsoft Entra 애플리케이션 추가**(없으면 "새로 만들기") → 역할은 **관리자**(Manager)로 해요. Microsoft 문서가 Manager 역할을 요구해요.
5. 만든 애플리케이션 이름을 눌러서 **테넌트 ID**와 **클라이언트 ID**를 복사해요.
6. **새 키 추가**(Add new key)를 누르고 나온 **키** 값을 복사해요. 이 값은 이 화면에서 한 번만 보여요. 잃어버리면 새 키를 만들어요.

## B. Store ID 찾기

Partner Center → **앱 및 게임** → Kelo AI → **제품 ID**(Store ID)예요. `9N`으로 시작하는 12자리예요.

## C. 설정 파일 만들기

터미널에서 (키를 채팅이나 git에 절대 붙이지 마세요):

```
mkdir -p ~/.config/kelo
nano ~/.config/kelo/msstore.env
chmod 600 ~/.config/kelo/msstore.env
```

파일 내용:

```
TENANT_ID=<테넌트 ID>
CLIENT_ID=<클라이언트 ID>
CLIENT_SECRET=<키 값>
APP_ID=9N...
```

파일을 나만 읽을 수 있게(`chmod 600`) 해두지 않으면 스크립트가 실행을 거부해요. 다른 위치를 쓰려면 `--env 경로` 또는 `KELO_MSSTORE_ENV`를 써요. `apps/desktop/store/*.env`는 git에서 무시돼요. 키는 만료되니(보통 1~2년) 새 키를 만들면 파일만 바꿔요.

## D. 명령

```
python3 -I apps/desktop/store/msstore.py check
python3 -I apps/desktop/store/msstore.py submit --msix dist/windows-<버전>/Kelo.msix --notes-en "What's new" --notes-ko "새 소식"
python3 -I apps/desktop/store/msstore.py status
python3 -I apps/desktop/store/msstore.py commit
```

1. `check`: 로그인, 앱 이름, 마지막 게시 제출, 대기 중 제출이 있는지 보여줘요. 아무것도 바꾸지 않아요.
2. `submit`: 마지막 게시 제출을 복사한 초안 제출을 만들고, 옛 패키지는 삭제 표시, 새 MSIX를 올려요. **심사로 보내지 않아요.** MSIX의 Identity 이름이 Store 제품과 다르거나 버전이 이미 게시된 것보다 높지 않거나, 대기 중인 제출이 있으면 거부해요(대기 중 제출은 이 도구가 지우지 않아요. Partner Center에서 끝내거나 지워요). 끝나면 "draft submission <id> is ready"라고 알려줘요. Partner Center에서는 **보기만** 하고 바꾸지 마세요(바꾸면 API가 막혀요).
3. `commit`: 대기 중인 초안(또는 `--submission ID`)을 확정(심사 요청)하고 최대 `--wait`분(기본 20) 동안 상태를 지켜봐요. 대기 중 제출이 없으면 거부해요. 심사 자체는 며칠 걸릴 수 있어요. 나중에는 `status`로 봐요. (`submit --commit`은 초안 만들기와 확정을 한 번에 해요.)

주의: Microsoft 문서에 따르면 API로 만든 제출을 Partner Center에서 **수정하면** 그 제출은 API로 더 이상 바꾸거나 확정할 수 없어요. 초안은 Partner Center에서 보기만 하고, 보내는 건 `--commit`으로 하는 걸 권해요. 가격 v2(Pricing Version 2) 앱은 가격 부분을 API가 못 읽어요. 그 외 부분은 문제없어요.

종료 코드: 0 정상, 2 거부/오류, 124 기다리다 시간 초과.

## 상품 설명에 넣을 문장 (2026-11-17)
Partner Center의 상품 설명(영어·한국어 모두)에 한 문장을 넣어요. 심사관이 이 기능을 볼 수 있어야 해요.
- 영어: "On Windows, the app can also install the Kelo server on this PC: it downloads the signed server installer and runs it after one administrator confirmation."
- 한국어: "Windows에서는 앱이 이 PC에 Kelo 서버도 설치할 수 있어요. 서명된 서버 설치 파일을 내려받아 관리자 확인 한 번으로 설치해요."
이 파일에서 설명을 올리지는 않아요. 제출은 사람이 Partner Center에서 해요.

## 확인하지 못한 것

Microsoft 문서(learn.microsoft.com "Create and manage submissions using Microsoft Store services")로 역할(Manager), 키/ID 위치, 토큰 주소를 확인했어요. 사용자 관리 화면의 정확한 한국어 메뉴 이름(테넌트, Microsoft Entra 애플리케이션 탭 문구)은 Partner Center 한국어 화면에서 직접 확인하지 못했어요. 실제 Microsoft 서버로는 시험하지 않았고(가짜 서버 테스트만), 앱 정보가 `packageIdentityName`을 돌려주는지는 실제 응답으로 확인이 필요해요(없으면 이름 비교를 건너뛰고 알려줘요).
