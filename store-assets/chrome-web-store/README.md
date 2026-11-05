# Chrome 웹 스토어 등록 자료

현재 확장프로그램 이름인 **아유 하기싫어 다운로더**에 맞춘 한국어 등록 자료입니다.

## 입력할 문구

- `short-description.txt`: 짧은 소개 문구.
- `description.txt`: 기능과 설치 이유를 중심으로 작성한 상세 설명. 파일 내용을 그대로 복사해 사용합니다.

## 업로드할 이미지

- 스토어 아이콘: `store-icon-128.png` — 128×128, RGBA PNG. 실제 그림 96×96, 사방 16px 투명 여백.
- 스크린샷 1: `screenshots/01-folder-choice.jpg` — 저장 폴더 선택.
- 스크린샷 2: `screenshots/02-bulk-queue.jpg` — 4개 영상 등록, 2개 저장 중·2개 대기.
- 스크린샷 3: `screenshots/03-completed.jpg` — 저장 완료 상태.
- 스크린샷 4: `screenshots/04-cancel-and-error.jpg` — 취소와 오류 안내.

스크린샷은 모두 **1280×800 JPEG, 알파 채널 없음**입니다. 01→04 순서로 업로드합니다. 실제 확장 HTML/CSS/렌더러를 사용하고 브라우저 표시 배율을 80%로 설정해 캡처했습니다. 강의명·용량·진행 상태는 개인정보가 없는 예시 데이터이며 각 화면에 예시임을 표시했습니다. 폴더 선택 대화상자나 실제 강의 파일은 캡처하지 않았습니다.

`store-upload-assets.zip`은 이 등록 자료를 모은 전달용 ZIP입니다. 압축을 풀어 스토어의 설명·아이콘·스크린샷 항목에 각각 넣으세요. 확장 실행 코드 제출용 ZIP은 기존 `dist/ssu-downloader.zip`입니다.

규격 참고: [Chrome 공식 이미지 안내](https://developer.chrome.com/docs/webstore/images).

## 화면이 바뀌었을 때 다시 만들기

```bash
.venv/bin/python scripts/build-store-assets.py
```

프로젝트의 Playwright와 로컬 Chrome을 사용합니다. 로그인과 학교 서버 접속 없이 예시 화면을 캡처합니다. `asset-manifest.json`에 원본 확장 버전과 파일 규격을 기록합니다. 생성 후 실제 이미지가 잘렸는지 확인하고 업로드하세요.


## 스토어 아이콘 규격

스토어 아이콘은 128×128 캔버스에 기존 그림을 96×96으로 배치하고, 사방 16px을 투명하게 비웠습니다. 전체 캔버스의 테두리나 큰 그림자는 추가하지 않았습니다. 밝고 어두운 배경에서 확인했습니다. 스크린샷의 알파 금지 조건과 달리, 아이콘에는 공식 가이드에 따라 투명 여백을 사용합니다.

원본 그림은 `extensions/ssu-downloader/branding/store-icon-artwork.png`에 보관합니다. 반복 생성해도 그림이 계속 작아지지 않도록 이 원본에서 내보냅니다.

```bash
.venv/bin/python scripts/build-store-icon.py
python3 scripts/build-download-extension.py
```

## 게시 전 입력 오류 해결

`privacy-practices.md`에 단일 목적, 원격 코드 선택, 권한별 이유, 데이터 사용 공개와 이메일 인증 절차를 정리했습니다. 실제 운영에 맞춰 확인한 뒤 스토어에 입력합니다.

스토어의 개인정보처리방침 URL 입력값: `https://daunload.cloud/privacy.html` (운영 배포 후 공개 여부 확인). 공개 문의처: `daun4799@gmail.com`.
