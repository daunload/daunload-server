# 개인 정보 보호 관행 입력 안내

현재 확장 소스(1.3.0)의 동작을 확인한 입력 초안입니다. 스토어에 올린 ZIP이 다른 버전이면 먼저 비교하세요. 아래 인증은 게시자가 실제 운영·데이터 이용 방식까지 확인하고 직접 체크해야 합니다.

## 단일 목적 설명 — 복사할 문구

사용자가 https://daunload.cloud/에서 선택한 본인의 강의 파일을 다운로드하고, 연동된 대시보드에서 텍스트 변환을 하도록 강의 오디오를 추출하는 확장프로그램입니다. 영상은 학교 CDN에서 사용자 PC의 선택한 폴더로 직접 다운로드합니다. 개별·전체 받기, 대기열, 진행률 확인과 취소는 이 다운로드 목적을 지원합니다. 또한 같은 강의 영상에서 오디오를 추출해, 연동된 대시보드에서 브라우저 안의 텍스트 변환(자막 생성)에 쓰도록 전달합니다.

## 원격 코드

선택: **아니요. 원격 코드를 사용하지 않습니다.**

설명란이 남아 있다면 아래 문구를 사용합니다. ‘예’를 선택한 상태를 유지하기 위해 이유를 만들어 입력하지 마세요.

모든 실행 로직은 확장 ZIP에 포함된 bridge.js, worker.js, manager.js, offscreen.js, offscreen.html, audio-extract.mjs와 함께 번들된 mp4box 라이브러리 파일 3개(원격이 아닌 ZIP 내 포함 파일)에 있습니다. 외부 JavaScript나 WebAssembly를 내려받아 실행하지 않으며 eval, new Function 또는 원격 스크립트 삽입을 사용하지 않습니다. 원격에서 가져오는 영상은 저장할 미디어 데이터이며 실행 코드가 아닙니다. daunload.cloud와의 메시지는 영상 주소·파일명과 다운로드 상태를 전달합니다.

## storage 권한 이유 — 복사할 문구

chrome.storage.session에 다운로드 작업의 영상 주소, 파일명, 대기·진행·완료 상태, 받은 용량, 작업 식별자 및 다운로드 창 식별자를 임시 보관합니다. 서비스 워커가 일시 중단되어도 작업을 조회하고 중복 팝업을 방지하며, 대시보드와 다운로드 창의 상태를 맞추기 위해 필요합니다. 학교 비밀번호나 로그인 쿠키를 이 저장소에 저장하지 않습니다.

## offscreen 권한 이유 — 복사할 문구

사용자가 https://daunload.cloud/ 대시보드에서 강의 영상의 텍스트 변환(자막 생성)을 직접 시작했을 때만 오프스크린 문서를 엽니다. 이 문서는 학교 CDN에서 해당 영상을 구간별로 읽어, 브라우저 내장 WebCodecs AudioDecoder로 소리만 디코딩하고 16kHz 모노 오디오로 바꿔 대시보드 탭에 넘깁니다. 서비스 워커에는 AudioDecoder가 없고 수십 분짜리 작업 상태를 유지할 수 없어서 오프스크린 문서가 필요합니다. 화면에 아무것도 표시하지 않고, 확장 ZIP에 포함된 코드(offscreen.js, audio-extract.mjs, 번들된 mp4box)만 실행하며, 영상과 오디오를 어떤 서버로도 보내지 않습니다. 변환이 끝나거나 취소되면 문서를 닫습니다.

(영문이 필요하면)

The offscreen document is opened only when the user starts transcription of a lecture video on the https://daunload.cloud/ dashboard. It reads that video from the school CDN in byte ranges, decodes only the audio track with the browser's built-in WebCodecs AudioDecoder, converts it to 16 kHz mono PCM and hands it to the dashboard tab for in-browser speech-to-text. A service worker cannot do this: AudioDecoder is not available there and it cannot keep the state of a job that runs for tens of minutes. The document shows no UI, runs only code bundled in the extension package (offscreen.js, audio-extract.mjs and the bundled mp4box library), and never sends video or audio to any server. It is closed when the transcription finishes or is cancelled.

## declarativeNetRequest 권한 이유 — 복사할 문구

사용자가 요청한 강의 영상을 학교 CDN에서 직접 받기 위해, 확장프로그램이 시작한 학교·CDN 요청에 한해 Referer와 Origin 헤더를 설정합니다. 학교 도메인 밖으로 향하는 확장 요청과 리다이렉트는 차단합니다. 헤더 수정은 확장 ID가 요청 시작자인 경우로 제한하며 사용자의 일반 웹 탐색 요청은 변경하지 않습니다. 이 권한의 차단 기능을 사용하므로 declarativeNetRequest를 요청합니다.

## 호스트 권한 이유 — 복사할 문구

https://*.ssu.ac.kr/* 및 https://*.commonscdn.com/*는 학교와 강의 CDN의 미디어 호스트에서 사용자가 선택한 영상 파일을 직접 다운로드하기 위해 필요합니다. 허용된 학교·CDN 하위 호스트의 요청과 리다이렉트에 접근하고 필요한 요청 헤더 규칙을 적용합니다. https://daunload.cloud/* 콘텐츠 스크립트는 사용자가 선택한 영상의 주소·파일명을 확장에 전달하고 상태를 대시보드에 알리기 위해 실행됩니다. 이 연동과 요청 영상 다운로드 이외의 탐색 이력을 수집하기 위한 권한이 아닙니다.

## 데이터 사용 공개

‘서버로 보내지 않는다’는 이유만으로 ‘어떤 데이터도 처리하지 않는다’를 선택하지 마세요. 로컬 처리·저장도 공개 대상입니다.

현재 코드에서 처리하는 데이터와 대응하는 스토어 분류를 실제 입력 화면의 정의에 맞춰 공개합니다.

- 웹사이트 콘텐츠: 선택한 강의 영상, 영상명·파일명.
- 웹 기록: 요청한 학교/CDN 영상 URL 및 연동 사이트 원점. 전체 브라우징 이력을 읽는 기능은 없습니다.
- 웹사이트 콘텐츠(오디오): 텍스트 변환을 위해 디코딩한 오디오(16 kHz PCM). 연동된 대시보드 탭에만 전달되고 브라우저 안에서 처리되며, 확장프로그램은 이를 어떤 서버로도 보내지 않습니다.
- 사용자 활동: 해당 다운로드의 시작·취소·진행·완료 상태. 일반 클릭·키 입력을 추적하는 기능은 없습니다.

확장 자체는 학교 비밀번호·로그인 쿠키를 전달받지 않습니다. 로그인과 권한 확인은 연동 웹서비스에서 처리합니다. 영상은 학교/CDN에서 PC로 전송하고, 확장은 상태를 대시보드에 전달합니다. 대시보드는 현재 로그인한 사용자에 대한 다운로드 완료 이력을 서버에 보고합니다. 웹서비스는 이와 별개로 계정별 일별 로그인·다운로드 횟수(90일 보관, 계정 삭제 시 삭제)와 재생·출석·제출 기록을 처리하며, 운영자가 문제 확인을 위해 이를 열람할 수 있습니다. 운영자는 비밀번호, 학교 로그인 세션, 알림 토큰, 전사 본문, 과제 점수를 열람하지 않습니다. 이 웹서비스 기록은 확장프로그램이 아니라 웹사이트에서 처리하며, 처리방침의 적용 범위는 웹서비스 전체입니다. 일반적인 CDN 접속 과정에서 학교/CDN 서버가 사용자 IP와 요청 정보를 볼 수 있다는 점도 처리방침에 반영합니다.

이름·학번 등 식별 정보가 파일명이나 URL에 포함되거나, 운영 환경에 별도 분석·광고·추적 코드가 추가된 경우에는 해당 데이터 유형도 함께 점검하세요. 이 초안만으로 실제 운영의 모든 데이터 이용을 인증할 수는 없습니다.

## 데이터 사용 인증

판매·목적 외 이용/전송·신용 평가/대출 관련 이용을 하지 않는다는 각 인증 문구를 읽고 실제 운영도 일치하면 체크합니다. 원격 코드나 권한 이유 입력만으로 이 인증이 대신되지 않습니다.

## 게시자 연락처 이메일

Chrome Web Store 개발자 대시보드의 계정/설정으로 이동해 실제 연락 가능한 이메일을 입력하고 저장합니다. 이메일 인증을 요청하고 받은 메일의 인증 링크를 누른 뒤 대시보드에서 인증 완료 여부를 확인합니다. 이 단계는 게시자의 이메일 접근이 필요합니다.

## 개인정보처리방침 URL과 저장

확장의 영상·URL·작업 상태 처리, 로컬 임시 보관, 대시보드에 전달하는 상태, 서버의 완료 이력, 학교/CDN에 대한 네트워크 요청, 보관·삭제 방법, 웹서비스 전체의 기록과 운영자 열람 범위, 실제 연락처를 설명하는 공개 개인정보처리방침 URL을 입력합니다. 존재하지 않는 URL이나 로그인해야 읽을 수 있는 페이지를 넣지 마세요. 이 파일은 내부 입력 안내이며 공개 처리방침을 대신하지 않습니다.

필드를 입력하고 원격 코드 ‘아니요’ 및 데이터 인증을 확인한 후 개인 정보 보호 관행 탭에서 **임시저장**합니다. 이메일 인증까지 완료한 뒤 상품으로 돌아와 게시 전 오류 목록을 다시 확인합니다.

## 공식 참고 문서

- https://developer.chrome.com/docs/webstore/cws-dashboard-privacy
- https://developer.chrome.com/docs/webstore/set-up-account
- https://developer.chrome.com/docs/extensions/develop/migrate/remote-hosted-code
- https://developer.chrome.com/docs/webstore/program-policies/user-data-faq
