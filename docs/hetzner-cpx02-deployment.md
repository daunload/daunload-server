# Hetzner CPX02 배포 가이드

CPX02는 **x86 / 1 vCPU / RAM 1GB / 디스크 20GB**다.
[Hetzner 공식 발표](https://docs.hetzner.cloud/whats-new)의 2026-10-06 항목에서 사양을 확인할 수 있다.
CX23용 가이드는 RAM 4GB·디스크 40GB를 전제로 하므로, CPX02에서는 이 문서의 변경 사항을 적용한다.

## 1. 운영 범위 결정

현재 서비스는 API, PostgreSQL, Redis, 동기화 워커, Chrome 재생 워커, Caddy를 실행한다.
동시 재생을 하나로 제한해도 **Chrome 한 개의 메모리 사용량은 남는다**.
서버가 하는 ffmpeg 오디오 추출도 별도 부하다. Whisper 추론은 사용자 브라우저가 담당한다.

CPX02에서는 두 가지 운영 범위를 선택할 수 있다.

| 범위 | 실행 서비스 | 의미 |
|---|---|---|
| 조회 중심 | API·DB·Redis·동기화·Caddy | 서버 재생을 사용하지 않는 소규모 시험 운영 |
| 전체 기능 시험 | 위 구성 + 재생 워커 | RAM·swap·디스크를 관찰하며 실제 강의로 검증해야 하는 구성 |

**현재 전체 기능의 CPX02 자원 사용량은 실측하지 않았다.** 1GB에서의 안정적인 다수 사용자 운영을 보장하지 않는다.
60건 동시 요청 테스트는 큐의 중복 등록·복구 검증이며, 이 서버에서 Chrome과 ffmpeg를 돌린 부하 테스트가 아니다.
swap은 메모리 부족 시 완충 수단이고 RAM의 대체품은 아니다.

아래 절차는 기능을 임의로 삭제하지 않는다. 재생 워커와 오디오 기능을 켜고 끄는 단계가 명시돼 있다.

## 2. 서버 생성부터 환경변수까지

[기본 배포 가이드](hetzner-cx23-deployment.md)의 **1~6절**을 따르되 다음을 바꾼다.

- 서버 Type은 **CPX02, AMD x86**로 선택한다. Ubuntu 24.04 LTS와 IPv4를 사용한다.
- 초기 설정에서 **2GB swap을 먼저 구성**한다. 이미 swap이 있다면 중복 생성하지 않는다.
- 서버 이름은 `ssu-lms-prod`처럼 정하면 된다. Compose 프로젝트 이름은 기존과 같은 `ssu-lms`다.
- `deploy/hetzner/.env`에서 처음에는 **`MAX_AUDIO_STREAMS=0`**으로 둔다.
  이 값은 ffmpeg 오디오 추출 요청을 거절하게 한다. 전사 UI가 자동으로 숨겨지는 설정은 아니다.
- 기본 가이드의 **서버에서 `dc build api`를 실행하는 단계는 건너뛴다.** 다음 절에서 로컬 빌드 이미지를 전송한다.
- 서버의 `.env`와 기존 DB가 있다면 암호화 키를 새로 생성하지 않는다.

서버에서 쓰는 단축 함수는 동일하다. 새 SSH 세션마다 정의한다.

```bash
dc() { sudo /opt/ssu-lms/deploy/hetzner/compose.sh "$@"; }
```

먼저 확인한다.

```bash
free -h
swapon --show
df -h /
```

디스크 20GB에는 OS, swap, 애플리케이션 이미지, PostgreSQL, Redis, 로그, 백업이 모두 들어간다.
Playwright·Chrome·ffmpeg가 포함된 현재 이미지는 일반적인 API 전용 이미지보다 크다.
이미지 크기를 실제로 확인한 뒤, 로드·업데이트·백업을 위한 여유 공간을 남긴다.

## 3. 이미지 빌드는 로컬 PC에서

**로컬 PC의 프로젝트 루트에서** Docker Desktop 또는 Docker Engine을 실행한 상태로 빌드한다.
서버에 전달한 소스와 같은 버전으로 빌드한다.
Apple Silicon Mac에서도 대상 아키텍처는 서버에 맞춰 `linux/amd64`로 지정한다.

```bash
docker buildx build --platform linux/amd64 -t ssu-lms:local --load .
docker image inspect ssu-lms:local --format '{{.Os}}/{{.Architecture}} size={{.Size}} bytes'
docker save ssu-lms:local | gzip > /tmp/ssu-lms-image.tar.gz
ls -lh /tmp/ssu-lms-image.tar.gz
```

압축 파일 크기는 실제 Docker 이미지가 차지할 공간보다 작을 수 있다.
이미지·설정만으로 사용 공간이 빠듯하면 배포를 진행하기 전에 디스크 또는 서버 사양을 재검토한다.

**로컬 PC에서**, 초기 등록한 root SSH 키로 서버에 이미지를 직접 스트리밍한다.
서버에 압축 파일을 별도로 저장하지 않아 20GB 디스크의 사용량을 줄일 수 있다.

```bash
gzip -dc /tmp/ssu-lms-image.tar.gz | ssh root@SERVER_IP 'docker load'
```

`SERVER_IP`를 실제 IP로 바꾼다. 이 방식은 root SSH 키 접속이 가능한 경우를 전제로 한다.
`ssh -t`는 바이너리 전송에 사용하지 않는다.
root SSH를 막은 환경이라면 별도의 이미지 레지스트리를 사용하거나, 파일 전송 후 서버에서 `sudo docker load`를 실행한다.

**서버에서** 확인한다.

```bash
sudo docker image inspect ssu-lms:local --format '{{.Os}}/{{.Architecture}} size={{.Size}} bytes'
sudo docker system df
df -h /
```

## 4. DB 준비와 조회 중심 실행

기존 개발용 Compose나 호스트 워커를 동시에 실행하지 않는다.
운영용 Compose의 `.env`가 준비된 상태에서 서버에서 실행한다.
명령 하나가 실패하면 다음으로 넘어가기 전에 원인을 해결한다.

```bash
cd /opt/ssu-lms
dc config --quiet
dc up -d --wait postgres redis
dc run --rm --no-deps api alembic upgrade head
dc up -d --no-build --wait api sync-worker caddy
dc ps
sudo docker stats --no-stream
```

소스만 업로드해서는 컨테이너에 반영되지 않는다. 3절의 이미지 전송까지 끝나 있어야 한다.
API·동기화·Caddy를 명시해 실행하므로 이 단계에서는 재생 워커를 시작하지 않는다.
과거에 전체 구성을 실행한 서버라면 다음으로 재생 워커를 중지한다.

```bash
dc stop worker
```

`unless-stopped` 정책으로 수동 중지한 워커는 Docker 재시작 시 자동 실행되지 않는다.
그러나 이후 **서비스 이름 없이 `dc up -d`를 실행하면 재생 워커도 다시 시작된다**.
조회 중심 운영을 유지할 때는 항상 `api sync-worker caddy`를 명시한다.

브라우저에서 로그인과 동기화를 확인하고 다음도 확인한다.

```bash
curl -fsS https://lms.example.com/health
dc logs --tail=100 api sync-worker caddy
```

도메인은 실제 값으로 바꾼다.
**현재 앱은 재생 워커 중지 여부에 따라 재생 요청을 자동 차단하지 않는다.**
조회 중심 모드에서는 재생 요청을 하지 않는다. 요청하면 DB 큐에 대기하고, 워커를 켠 뒤 처리된다.
이 상태를 다수 사용자에게 재생 기능이 제공되는 운영 환경으로 안내하지 않는다.

## 5. 전체 기능을 시험하려면

조회·동기화 상태가 안정적인 것을 확인한 뒤 재생 워커 하나를 추가한다.
오디오 추출은 우선 `MAX_AUDIO_STREAMS=0`을 유지한다.

```bash
dc up -d --no-build worker
dc logs --tail=100 worker
sudo docker stats
```

본인 계정으로 짧은 강의 하나를 재생하면서 **별도 SSH 세션에서** 확인한다.

```bash
free -h
vmstat 1
```

`docker stats`는 `Ctrl+C`, `vmstat`도 `Ctrl+C`로 종료한다.
다음 조건을 실제로 확인한다.

1. 강의 재생과 LMS 완료 확인까지 정상적으로 끝나는지.
2. 재생 중 대시보드 조회·로그인이 계속 응답하는지.
3. 컨테이너가 반복 재시작하거나 OOM으로 종료되지 않는지.
4. `vmstat`의 `si`·`so`가 지속적으로 증가하며 서비스가 느려지지 않는지.
5. 다운로드 요청을 더했을 때도 메모리·네트워크 사용이 감당되는지.

종료·메모리 문제는 다음으로 확인한다.

```bash
dc ps -a
dc logs --tail=100 api worker sync-worker
sudo journalctl -k --grep='Out of memory'
```

부하를 감당하지 못하면 워커를 중지하고 조회 중심 구성으로 되돌린다.

```bash
dc stop worker
```

재생 중단 후 남은 작업은 DB에 보존되며, 워커를 다시 시작하면 LMS 상태를 재확인한다.
중단된 영상은 처음부터 재생될 수 있다.
메모리 부족이 반복되면 swap을 계속 늘리기보다는 Chrome 작업을 다른 기기로 분리하거나 RAM이 더 큰 서버로 변경한다.

## 6. 오디오 추출·전사를 켜려면

Chrome 재생과 API가 안정적인 것을 확인한 뒤에만 `deploy/hetzner/.env`를 수정한다.

```dotenv
MAX_AUDIO_STREAMS=1
```

환경변수 변경은 단순 `restart`로 반영되지 않으므로 API 컨테이너를 다시 만든다.

```bash
dc up -d --no-build --force-recreate api
```

CPX02에서는 오디오 추출과 Chrome 재생을 동시에 시험하기 전에, 재생 워커를 중지한 상태로
오디오 추출 하나의 부하부터 확인한다. 기본 한도보다 크게 설정하지 않는다.
기능을 다시 끄려면 `MAX_AUDIO_STREAMS=0`으로 바꾸고 API를 재생성한다.
`0`일 때 오디오 API는 429로 응답한다. UI 비활성화 기능을 추가한 것은 아니다.

## 7. 백업·업데이트·복구에서 달라지는 점

HTTPS·DB 백업·외부 보관·복구 원칙은 [기본 가이드 8~12절](hetzner-cx23-deployment.md#8-배포-확인)과 같다.
단, CPX02에서는 다음을 적용한다.

- **업데이트 이미지도 로컬에서 빌드한다.** 서버에서 `dc build api`를 하지 않는다.
- 새 이미지를 로드하기 전 기존 이미지를 롤백용 태그로 보관하고 디스크 여유를 확인한다.
- 이미지 로드 후 DB 백업 → 서비스 중지 → 마이그레이션 → 필요한 서비스만 시작한다.
- 조회 중심 모드라면 마지막 실행도 `dc up -d --no-build --wait api sync-worker caddy`로 한다.
- 전체 기능 시험을 계속한다면 확인 후 `dc up -d --no-build worker`를 추가한다.
- 복구 절차의 `dc build api`도 백업 시점 소스의 로컬 빌드·이미지 전송으로 대체한다.
- 기본 백업 스크립트는 로컬에 14일 보관한다. 20GB 디스크에서는 실제 DB 크기에 맞춰 보관 기간을 조정하고,
  외부 백업을 정기적으로 전송한다. 백업 실패와 남은 디스크를 확인한다.
- 정상 동작과 롤백 이미지 보관을 확인한 뒤 `sudo docker image prune`으로 태그 없는 이미지만 정리한다.
  **볼륨 정리는 실행하지 않는다.**

CPX02에서 먼저 확인해야 하는 것은 소규모 실제 재생의 안정성이다.
이 검증이 끝나기 전까지 다수 사용자 대상 전체 기능 공개를 위한 충분한 사양이라고 판단하지 않는다.
