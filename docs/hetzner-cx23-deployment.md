# Hetzner CX23 배포 가이드

**CPX02를 사용한다면 [CPX02 전용 가이드](hetzner-cpx02-deployment.md)를 먼저 읽는다.**
CPX02는 RAM 1GB·디스크 20GB라서 이 문서의 전체 서비스 실행과 서버 내 빌드를 그대로 적용하지 않는다.

대상은 **CX23 / x86_64 / RAM 4GB / SSD 40GB / Ubuntu 24.04 LTS**다.
독일 또는 핀란드 리전에 IPv4를 추가하고, 서버 한 대에서 전체 서비스를 운영한다.
이 문서는 새 서버에 처음 설치하는 순서다. 기존 서버 업데이트와 복구는 뒤의 별도 절차를 따른다.

## 1. 배포 구성과 준비물

```text
인터넷 → Hetzner Firewall → Caddy :80/:443 → FastAPI :8000
                                            ├─ PostgreSQL 16
                                            ├─ Redis 7 → 동기화·알림 워커
                                            └─ DB 재생 큐 → 재생 워커 → Chrome
```

- 강의는 서버 전체에서 하나씩 재생하고, 사용자별 영상 한 개씩 순환 처리한다.
- 오디오 추출은 동시 1개로 시작한다. Whisper 음성 인식은 사용자 브라우저에서 실행한다.
- API·PostgreSQL·Redis는 호스트 포트를 열지 않는다. 외부 웹 접속은 Caddy만 받는다.
- 운영용 설정은 `deploy/hetzner/compose.yml`이다. 루트의 `docker-compose.yml`과 합쳐 실행하지 않는다.
- 운영용 Compose는 API 시작 때 자동 마이그레이션을 하지 않는다. 관리자가 먼저 한 번 실행한다.
- DB, Redis 작업, HTTPS 인증서는 Docker 볼륨에 저장한다. 단일 서버이므로 서버 장애 시 서비스가 중단된다.

준비물:

1. Hetzner 계정과 CX23 생성 가능한 리전의 재고.
2. SSH 공개키와 개인키. 개인키는 로컬 PC에 보관한다.
3. DNS를 설정할 수 있는 도메인 또는 서브도메인. 예: `lms.example.com`.
4. 이 프로젝트의 현재 소스 코드. 배포할 변경 사항이 서버에 모두 전달되어야 한다.

아래 `SERVER_IP`, `YOUR_PUBLIC_IP`, `lms.example.com`은 실제 값으로 바꾼다.

## 2. Hetzner 서버와 방화벽 생성

Hetzner Console에서 프로젝트를 만들고 서버를 생성한다.

| 항목 | 선택 |
|---|---|
| Location | 독일 또는 핀란드 중 CX23 재고가 있는 곳 |
| Image | Ubuntu 24.04 LTS |
| Type | **CX23, Intel/AMD x86** |
| Networking | Public IPv4 활성화, IPv6는 선택 |
| SSH keys | 본인의 공개키 등록 |
| Volumes | 추가하지 않음 |
| 이름 | 예: `ssu-lms-prod` |

현재 Docker 이미지가 `linux/amd64` Chrome을 사용하므로 ARM 상품인 CAX는 선택하지 않는다.

Cloud Firewall을 만들어 서버에 적용한다. 인바운드는 다음만 허용한다.

| 프로토콜·포트 | Source | 용도 |
|---|---|---|
| TCP 22 | 본인 공인 IPv4 `YOUR_PUBLIC_IP/32` | SSH |
| TCP 80 | 모든 IPv4, IPv6를 쓴다면 IPv6도 허용 | 인증서 발급·HTTP 리다이렉트 |
| TCP 443 | 모든 IPv4, IPv6를 쓴다면 IPv6도 허용 | HTTPS |

아웃바운드 제한은 설정하지 않는다. 학교 LMS/CDN, DNS, 패키지 저장소, 인증서 서버 등에 접속해야 한다.
IP가 바뀌면 SSH에 다시 접속하기 전에 방화벽의 22번 Source를 수정한다.
Docker 공개 포트는 UFW 규칙을 우회할 수 있으므로 이 가이드는 Cloud Firewall과 포트 미공개를 함께 사용한다.

참고: [Hetzner Firewall](https://docs.hetzner.com/cloud/firewalls/getting-started/creating-a-firewall/),
[Docker 방화벽 주의사항](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).

## 3. 서버 초기 설정

**로컬 PC에서:**

```bash
ssh root@SERVER_IP
```

**서버의 root 세션에서:**

```bash
apt-get update
apt-get upgrade -y
apt-get install -y ca-certificates curl python3 rsync sudo nano
adduser deploy
usermod -aG sudo deploy
install -d -o deploy -g deploy -m 700 /home/deploy/.ssh
install -o deploy -g deploy -m 600 /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
install -d -o deploy -g deploy -m 750 /opt/ssu-lms
```

`adduser`에서 설정한 비밀번호는 `deploy`의 sudo 실행에 사용한다.
새 로컬 터미널에서 `ssh deploy@SERVER_IP`와 `sudo -v`가 되는지 확인한다.
관리 작업은 이후 `deploy` 계정으로 한다. 이 가이드는 Docker 그룹에 사용자를 추가하지 않고 `sudo`로 실행한다.

CX23의 메모리 여유를 위해 새 서버에 2GB swap을 추가할 수 있다. swap은 메모리 부족 시의 완충 수단이다.
이미 swap이 있다면 먼저 `swapon --show`로 확인하고 이 블록은 건너뛴다.

```bash
if [ ! -e /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
free -h
```

## 4. Docker 설치

**서버의 deploy 세션에서**, 새 Ubuntu 서버를 기준으로 Docker 공식 apt 저장소를 추가한다.
기존 Docker·Podman 설치가 있는 서버는 [공식 설치 문서](https://docs.docker.com/engine/install/ubuntu/)의 충돌 패키지 항목을 먼저 확인한다.

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF_DOCKER
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF_DOCKER
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

## 5. 소스 코드 업로드

현재 로컬 작업 내용을 그대로 올리려면 **로컬 PC의 프로젝트 루트에서** 다음을 실행한다.
이 방식은 아직 Git에 커밋하지 않은 파일도 전달한다. `--delete`는 사용하지 않는다.

```bash
rsync -az \
  --exclude='.git/' --exclude='.venv/' --exclude='.env' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' \
  --exclude='*.db' --exclude='*.dump' --exclude='backups/' \
  ./ deploy@SERVER_IP:/opt/ssu-lms/
```

Git으로 관리한다면 배포할 커밋을 push한 뒤 서버에서 clone하는 방식도 가능하다.
둘 중 하나로 운영하고, 배포한 소스 버전을 기록한다. Git에 없는 로컬 변경은 clone만으로 전달되지 않는다.

**서버에서:**

```bash
cd /opt/ssu-lms
chmod +x deploy/hetzner/compose.sh deploy/hetzner/backup.sh
```

이후 명령을 짧게 쓰기 위해 셸 함수를 정의한다. SSH를 새로 열 때 다시 정의한다.

```bash
dc() { sudo /opt/ssu-lms/deploy/hetzner/compose.sh "$@"; }
```

`compose.sh`는 실행 위치와 무관하게 프로젝트 이름을 `ssu-lms`로 고정하고 운영용 `.env`를 읽는다.
따라서 루트의 개발용 `.env`는 운영 설정으로 사용되지 않는다.

## 6. DNS와 환경변수

DNS 관리 화면에 다음 레코드를 추가한다.

| 유형 | 이름 | 값 |
|---|---|---|
| A | `lms` | 서버의 공인 IPv4 |

IPv6를 올바르게 설정하지 않았다면 AAAA 레코드는 만들지 않는다.
Cloudflare를 쓴다면 최초 확인 단계에서는 **DNS only**로 둔다.

**서버에서**, 처음 한 번만 환경 파일을 생성한다.
이미 데이터가 있는 운영 서버에서는 암호화 키를 새로 생성하지 않는다.

```bash
cd /opt/ssu-lms
umask 077
cp -n deploy/hetzner/.env.example deploy/hetzner/.env
python3 - <<'PY'
import base64
import os
from pathlib import Path
import secrets

p = Path('deploy/hetzner/.env')
s = p.read_text()
values = {
    'CHANGE_ME_POSTGRES': secrets.token_hex(32),
    'CHANGE_ME_FERNET': base64.urlsafe_b64encode(os.urandom(32)).decode(),
    'CHANGE_ME_JWT': secrets.token_urlsafe(48),
}
for marker, value in values.items():
    s = s.replace(marker, value)
p.chmod(0o600)
p.write_text(s)
PY
nano deploy/hetzner/.env
```

`DOMAIN=lms.example.com`을 실제 도메인으로 바꾼다. `https://`나 경로는 넣지 않는다.
`MAX_AUDIO_STREAMS=1`은 유지한다. `LMS_ID`, `LMS_PASSWORD`는 서버 운영에 필요 없다.

- `POSTGRES_PASSWORD`: URL에 안전한 16진수로 생성한다. 기존 DB 볼륨이 생긴 뒤 `.env`의 값만 바꿔도 DB 비밀번호는 바뀌지 않는다.
- `FERNET_KEY`: 저장한 학교 계정 정보를 복호화하는 키다. DB와 함께 복구할 수 있도록 별도로 안전하게 보관한다.
- `JWT_SECRET`: 로그인·다운로드 토큰에 사용한다. 변경하면 기존 토큰이 무효화된다.
- 환경 파일을 Git에 올리거나 외부에 공유하지 않는다. Docker 빌드에서도 제외된다.

## 7. 이미지 빌드, 마이그레이션, 실행

**서버에서:**

```bash
cd /opt/ssu-lms
dc config --quiet
dc build api
dc up -d --wait postgres redis
dc run --rm --no-deps api alembic upgrade head
dc up -d --wait
dc ps
```

- `api` 이미지를 한 번 빌드하고 두 워커가 같은 이미지를 사용한다.
- Playwright·Chrome·ffmpeg를 설치하므로 최초 빌드는 시간이 걸리고 디스크를 수 GB 사용한다.
- `alembic upgrade head`가 성공한 뒤 다음 명령으로 넘어간다. 실패하면 워커를 시작하지 말고 오류부터 해결한다.
- 운영용 Compose는 프로필 없이 재생 워커까지 시작한다. `--profile amd64`를 추가할 필요 없다.
- API 상태가 정상인 뒤 워커와 Caddy가 시작된다. `--wait`는 워커 프로세스 내부의 정상 동작까지 보장하지 않으므로 로그도 확인한다.
- `shm_size: 1gb`는 Chrome용 공유 메모리의 상한이며 1GB를 미리 예약하는 설정은 아니다.

DNS가 서버를 가리키고 80·443번 포트가 열려 있으면 Caddy가 인증서를 발급·갱신한다.
인증서는 `caddydata` 볼륨에 보존된다. [Caddy HTTPS 요구사항](https://caddyserver.com/docs/automatic-https#overview)

## 8. 배포 확인

**로컬 PC 또는 서버에서** 실제 도메인으로 확인한다.

```bash
curl -fsS https://lms.example.com/health
```

정상이면 `{"ok":true}`가 반환된다. 브라우저에서 `https://lms.example.com`을 연다.
`/health`는 API 응답 확인용이며 학교 LMS 연결이나 재생 성공을 보장하지 않는다.

**서버에서:**

```bash
dc logs --tail=100 api worker sync-worker caddy
sudo docker stats --no-stream
df -h /
```

다음도 실제로 확인한다.

1. 본인 계정 로그인과 강의·할 일 동기화.
2. 짧은 강의 하나의 재생과 LMS 완료 상태 확인.
3. 두 계정으로 요청했을 때 한 영상씩 순환하고 대기 상태가 표시되는지 확인.
4. 영상 다운로드와 브라우저 전사 기능 확인. Whisper 모델은 사용자 기기에 내려받는다.
5. 새 SSH 세션에서 8000·5432·6379번 호스트 공개 포트가 없음을 `sudo docker ps`의 Ports에서 확인.

## 9. DB 백업과 외부 보관

백업 스크립트는 실행할 때마다 파일 두 개를 만든다.

- `ssu-lms-날짜.tar.gz`: DB dump, Compose·Caddy 설정
- `ssu-lms-secrets-날짜.env`: 운영 `.env`. 저장된 학교 비밀번호를 복호화하는 `FERNET_KEY`가 들어 있다.

두 파일이 함께 유출되면 모든 사용자의 학교 비밀번호가 드러난다. 외부 사본은 서로 다른 곳에 보관한다
(예: DB 백업은 외부 저장소, secrets 파일은 비밀번호 관리자).

`/etc/ssu-lms/backup-age-recipients`에 [age](https://github.com/FiloSottile/age) 수신자(공개키)를 넣어 두면
두 파일을 모두 암호화해 `.age` 파일로만 남긴다. 개인키는 서버에 두지 않는다.

```bash
age-keygen -o ssu-lms-backup-key.txt   # 로컬 PC에서 실행. 출력된 public key(age1...)를 아래에 넣는다
sudo apt-get install -y age            # 서버에서
sudo install -d -m 700 /etc/ssu-lms
echo 'age1...' | sudo tee /etc/ssu-lms/backup-age-recipients > /dev/null
```

```bash
sudo /opt/ssu-lms/deploy/hetzner/backup.sh
sudo ls -lh /var/backups/ssu-lms
```

백업은 `/var/backups/ssu-lms/`에 root 전용 권한으로 저장된다.
로컬 백업은 14일간 보관한다. 40GB 디스크이므로 DB 증가 시 보관 기간과 외부 저장 방식을 조정한다.
`pg_restore --list`로 dump 형식을 확인하지만 실제 복구 가능 여부는 복구 연습으로 확인해야 한다.

매일 한국 시각 04:15에 실행하려면 서버에서 다음 cron 파일을 만든다.
아래는 **서버 시간대를 Asia/Seoul로 변경**하는 방식이다.

```bash
sudo timedatectl set-timezone Asia/Seoul
sudo tee /etc/cron.d/ssu-lms-backup > /dev/null <<'CRON'
15 4 * * * root /opt/ssu-lms/deploy/hetzner/backup.sh >> /var/log/ssu-lms-backup.log 2>&1
CRON
sudo chmod 644 /etc/cron.d/ssu-lms-backup
```

서버가 삭제되면 로컬 백업도 사라진다. **로컬 PC에서** 실제 파일명으로 외부 사본을 받는다.
아래는 초기 등록한 root SSH 키를 사용하는 예다. root SSH를 제한했다면 별도 백업 전송 계정이나 암호화된 외부 저장소를 사용한다.

```bash
mkdir -p backups
chmod 700 backups
scp root@SERVER_IP:/var/backups/ssu-lms/ssu-lms-실제날짜.tar.gz backups/
chmod 600 backups/*
```

`ssu-lms-secrets-실제날짜.env`는 DB 백업과 같은 곳에 두지 말고 비밀번호 관리자 등 별도 위치에 보관한다.
age로 암호화했다면 파일명 끝에 `.age`가 붙는다.

이 명령은 수동 복사다. 무인 운영 전에는 외부 백업 전송도 자동화하고 실패 여부를 모니터링한다.
서버 스냅샷은 추가 복구 수단으로 쓸 수 있지만 DB dump와 암호화 키의 외부 보관도 유지한다.

## 10. 새 버전 업데이트

운영용 `.env`와 Docker 볼륨을 보존한다. 아래는 짧은 서비스 중단이 있는 업데이트다.

1. 변경된 소스를 5절의 `rsync`로 업로드하거나, Git으로 관리 중이면 검토한 커밋으로 갱신한다.
2. 서버에서 아래를 실행한다. 빌드 실패 시 기존 실행 컨테이너는 유지된다.

```bash
cd /opt/ssu-lms
sudo /opt/ssu-lms/deploy/hetzner/backup.sh
sudo docker image tag ssu-lms:local ssu-lms:previous
dc build api
# 요청과 작업을 멈춘 상태에서 스키마를 변경한다.
dc stop caddy worker sync-worker api
dc run --rm --no-deps api alembic upgrade head
dc up -d --wait
dc logs --tail=100 api worker sync-worker caddy
```

명령이 실패하면 원인을 확인하고 다음 단계로 넘어가지 않는다.
마이그레이션이 중복 활성 묶음을 지적하면, 이전 버전 API로 같은 사용자의 불필요한 묶음을 취소한 뒤 재시도한다.
임의로 DB 행을 삭제하지 않는다.

스키마가 이전 코드와 호환될 때만 다음처럼 이전 이미지를 다시 실행할 수 있다.

```bash
sudo env APP_IMAGE_TAG=previous /opt/ssu-lms/deploy/hetzner/compose.sh up -d --no-build --wait
```

이 명령은 DB 스키마를 되돌리지 않는다. 스키마 호환성이 없으면 배포 전 백업과 해당 소스 버전으로 복구한다.
롤백 뒤 설정의 `APP_IMAGE_TAG`도 의도한 버전으로 맞춘다. 그렇지 않으면 다음 실행 시 `local` 이미지로 다시 바뀐다.

이미지 정리는 새 버전이 정상이고 롤백 이미지가 보존된 것을 확인한 뒤 한다.
다음 명령은 태그 없는 이미지만 대상으로 한다.

```bash
sudo docker image prune
sudo docker system df
```

**`docker compose down -v`, `docker volume prune`, 볼륨을 포함한 전체 정리는 운영 DB·인증서를 지울 수 있다.**
단순 재시작에는 `dc restart`를 사용한다.

## 11. 백업에서 복구

다음은 **새 서버 또는 기존 데이터를 대체해도 되는 복구 대상**에서의 절차다.
기존 데이터가 있다면 먼저 현재 상태를 백업한다. 복구는 dump 시점 이후의 변경을 포함하지 않는다.
먼저 2~5절대로 Docker와 해당 백업 시점의 소스를 준비한다.

age로 암호화한 백업은 **로컬 PC에서** 먼저 복호화한다.

```bash
age -d -i ssu-lms-backup-key.txt -o ssu-lms-실제날짜.tar.gz ssu-lms-실제날짜.tar.gz.age
age -d -i ssu-lms-backup-key.txt -o ssu-lms-secrets-실제날짜.env ssu-lms-secrets-실제날짜.env.age
```

백업 파일과 secrets 파일을 서버에 업로드한 뒤 아래처럼 해제한다.

```bash
sudo install -d -m 700 /root/ssu-lms-restore
sudo tar -xzf /업로드한경로/ssu-lms-실제날짜.tar.gz -C /root/ssu-lms-restore
```

업로드 경로와 파일명은 실제 값으로 바꾼다.
정상 운영 중인 서비스가 있다면 `dc stop caddy worker sync-worker api`로 쓰기를 먼저 중지한다.

```bash
sudo install -o deploy -g deploy -m 600 /업로드한경로/ssu-lms-secrets-실제날짜.env /opt/ssu-lms/deploy/hetzner/.env
```

도메인이나 서버 IP가 바뀌면 DNS와 `.env`의 `DOMAIN`을 수정한다.
복구한 `POSTGRES_PASSWORD`가 이미 존재하는 DB 볼륨의 비밀번호와 일치해야 한다.
새 볼륨은 복구한 `.env` 값으로 초기화되지만, 기존 볼륨의 비밀번호는 환경 파일 복사만으로 바뀌지 않는다.

```bash
dc build api
dc up -d --wait postgres redis
sudo sh -c '/opt/ssu-lms/deploy/hetzner/compose.sh exec -T postgres pg_restore -U lms -d lms --clean --if-exists --no-owner --exit-on-error < /root/ssu-lms-restore/database.dump'
dc run --rm --no-deps api alembic upgrade head
dc up -d --wait
```

`pg_restore --clean`은 dump에 포함된 기존 객체를 대체한다. 복구 전용 대상에만 실행한다.
복구한 작업 중 `running` 상태는 워커가 다시 확인하고 이어 처리한다.
재생 중이던 영상은 LMS 상태를 재조회한 뒤 처음부터 재생될 수 있다.
인증서를 새로 발급받고 8절 검증을 다시 수행한다. 복구용 파일은 확인 후 안전한 외부 보관 정책에 맞춰 관리한다.

## 12. 자주 생기는 문제

| 증상 | 확인할 것 |
|---|---|
| HTTPS 인증서 발급 실패 | A/AAAA 값, Cloud Firewall 80·443, `dc logs --tail=100 caddy` |
| Caddy가 502를 반환 | `dc ps`, API 로그, API 상태가 healthy인지 |
| 재생 대기 상태가 계속됨 | `dc logs --tail=100 worker`, Chrome 오류, 마이그레이션 완료 여부 |
| 재생 워커가 이미 실행 중 | 루트 개발용 Compose나 호스트 워커도 실행 중인지 확인. 운영 워커는 하나만 실행 |
| DB 인증 실패 | 최초 볼륨 생성 당시 비밀번호와 운영 `.env`가 일치하는지 확인 |
| `CHANGE_ME` 또는 키 오류 | `deploy/hetzner/.env`의 세 비밀값과 실제 도메인을 설정했는지 확인 |
| 빌드 도중 종료·서버 느려짐 | `free -h`, `df -h /`, `sudo docker stats --no-stream`, 호스트 `sudo journalctl -k --grep='Out of memory'` |
| 오디오 추출이 바쁨(429) | 초기 동시 한도 1개. 먼저 CPU 사용을 확인하고 작업 종료 후 재시도 |
| DB 연결 수 초과 | API나 워커를 추가로 실행했는지 확인. 기본 구성은 각 1개이며 DB 최대 연결 수는 60 |
| 웹은 되지만 학교 연동 실패 | API·동기화·재생 워커 로그, 학교 로그인 변화, 유럽 리전에서 학교 CDN 연결 여부 |

운영 중에는 RAM·CPU·디스크 사용률, 다운로드 전송량, 재생 대기 시간, 백업 성공 여부를 확인한다.
헬스 엔드포인트 응답과 워커 프로세스 실행 여부만으로 전체 기능의 성공을 판단하지 않는다.
