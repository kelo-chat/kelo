import { SubPage } from "./Rows";
import { ShieldIcon } from "./icons";

const POINTS: [string, string][] = [
  ["비밀번호와 키는 안전한 보관함에 있어요", "웹사이트 로그인과 API 키는 AES-256으로 암호화해 Kelo 서버에 저장해요. 어느 사이트에서 쓸 수 있는지도 함께 묶여 있어서, 다른 곳으로는 쓸 수 없어요. 값은 화면에도, 에이전트에게도 다시 보여 주지 않아요."],
  ["에이전트는 값을 볼 수 없어요", "비밀번호는 채팅에 입력하지 않고 보관함의 입력 칸에만 넣어요. 에이전트는 '있다'는 것만 알고, 등록한 사이트에 서버가 대신 입력해요. 쓸 때마다 사용 기록에 남고, 에이전트는 기록을 읽거나 지울 수 없어요."],
  ["대화와 파일은 Kelo 서버에 있어요", "대화 기록, 올린 문서, 메모는 서버를 설치한 컴퓨터에 저장돼요. 다만 대화할 때 보내는 내용은 고른 AI 서비스(모델 제공자)로 전송돼요."],
  ["중요한 일은 먼저 물어봐요", "기본 설정에서는 웹에서 클릭·입력하기, 연결한 서비스에 쓰기, 비용이 드는 일(음성·이미지 만들기), 팟캐스트 공개를 허락받은 뒤에만 해요."],
  ["기기마다 따로 끊을 수 있어요", "'내 기기'에서 폰마다 연결을 끊을 수 있어요. 한 기기를 끊어도 나머지는 그대로예요."],
  ["에이전트가 할 수 있는 일은 정해져 있어요", "에이전트는 Kelo 서버에서 명령어를 실행하지 못하고, 정해진 도구로만 파일과 웹을 다뤄요. 웹은 공개된 주소만 열 수 있어요."],
];

/** Plain-language answers to "what happens to my data". Everything here is something the app really does. */
export function PrivacyPage({ onBack }: { onBack: () => void }) {
  return (
    <SubPage title="개인 정보 보호" onBack={onBack}>
      <div className="card">
        {POINTS.map(([t, d]) => (
          <div key={t} className="keybox">
            <b className="between" style={{ justifyContent: "flex-start", gap: 8 }}><ShieldIcon />{t}</b>
            <span className="small muted">{d}</span>
          </div>
        ))}
      </div>
    </SubPage>
  );
}
