# main 자동 배포 설정

`.github/workflows/deploy.yml`은 `main` push(다른 브랜치의 PR merge 포함) 또는 수동 실행으로 동작한다.
테스트 → GitHub 호스팅 러너에서 linux/amd64 이미지 빌드 → GHCR 업로드 → SSH 배포 순서다.
서버에서 이미지를 빌드하지 않으며 이미 보유한 레이어는 다시 내려받지 않는다.
이미지는 커밋 태그로 보관하고, 실제 배포는 변경할 수 없는 digest를 사용한다.

## 1. 서버 준비

기존 `/opt/ssu-lms/deploy/hetzner/.env`, PostgreSQL 데이터, Redis, HTTPS가 정상인 서버를 전제로 한다.
서버 `.env`를 그대로 사용하거나 GitHub Environment Secret `PRODUCTION_ENV`로 관리할 수 있다.
비밀값을 저장소 파일·커밋·이미지에 포함하지 않는다.

서버에서 배포 계정이 설정 파일을 업로드할 수 있도록 확인한다.

```bash
sudo install -d -o deploy -g deploy -m 750 /opt/ssu-lms/deploy/hetzner
sudo chown deploy:deploy /opt/ssu-lms/deploy/hetzner/{compose.yml,compose.sh,backup.sh,Caddyfile}
command -v flock
```

Mac에서 자동 배포 전용 키를 만든다. 개인키는 Git에 추가하지 않는다.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/daunload_actions -C github-actions-daunload -N ''
cat ~/.ssh/daunload_actions.pub | ssh deploy@SERVER_IP 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'
```

키는 배포 권한을 가진다. 다른 사람에게 전달하지 않는다.
서버에서 `sudo visudo -f /etc/sudoers.d/ssu-lms-deploy`로 다음 규칙을 등록한다.

```sudoers
deploy ALL=(root) NOPASSWD: /usr/bin/bash /opt/ssu-lms/deploy/hetzner/deploy.sh *
```

CI는 `sudo -n`을 사용하므로 비밀번호 입력이 필요하면 실패한다.
해당 스크립트를 수정할 수 있는 배포 계정은 서버 관리 권한을 가진 것으로 취급한다.

## 2. GitHub Environment와 Secrets

저장소 **Settings → Environments → New environment → `production`**을 만든다.
Deployment branches를 `main`으로 제한한다. 매번 자동 배포하려면 Required reviewers는 설정하지 않는다.
환경의 Secrets에 다음을 추가한다.

| 이름 | 값 |
|---|---|
| `DEPLOY_HOST` | 실제 서버 IP 또는 SSH용 호스트명. Cloudflare 프록시 주소 대신 직접 접속 주소 사용 |
| `DEPLOY_SSH_KEY` | Mac의 `~/.ssh/daunload_actions` 개인키 전체 |
| `DEPLOY_KNOWN_HOSTS` | 접속 대상 서버의 검증된 SSH host key 행 |
| `PRODUCTION_ENV` (선택) | 운영 `.env`의 전체 내용. 등록하면 배포 시 서버 설정을 이 내용으로 교체 |

### 환경변수를 GitHub에서 변경하기

`production` 환경의 **Add environment secret**에서 `PRODUCTION_ENV`를 만든다.
현재 서버의 `/opt/ssu-lms/deploy/hetzner/.env` 내용을 바탕으로 필요한 값만 수정한 뒤 전체를 붙여 넣는다.
로컬 개발용 루트 `.env`가 아니라 **운영용 `.env`**를 사용한다.

```dotenv
DOMAIN=daunload.cloud
POSTGRES_PASSWORD=기존_서버의_값
FERNET_KEY=기존_서버의_값
JWT_SECRET=기존_서버의_값
ADMIN_STUDENT_IDS=["관리자학번"]
PLAYBACK_HEADLESS=true
PLAYBACK_BROWSER_CHANNEL=chrome
MAX_AUDIO_STREAMS=1
TIMEZONE=Asia/Seoul
TZ=Asia/Seoul
```

위 내용은 형식 예시다. 비밀번호와 키에는 반드시 실제 기존 값을 넣는다.
파일 전체를 교체하므로 기존의 다른 운영 설정이 있다면 함께 포함한다.
서버가 사용하는 DATABASE_URL·REDIS_URL은 Compose가 내부 주소로 지정한다.

Secret 수정 자체는 배포를 시작하지 않는다.
수정 후 **Actions → Deploy production → Run workflow → main**을 실행하거나 다음 `main` push를 기다린다.
값을 변경하면 API·워커·Caddy를 재생성해 적용한다. HTTPS 확인도 변경된 `DOMAIN`을 사용한다.
Secret을 삭제하거나 비워 두면 그 이후부터 서버의 현재 `.env`를 유지한다. 이전 설정으로 복원되는 것은 아니다.

배포 스크립트는 새 설정을 로그에 출력하지 않고 검증한 뒤, 기존 설정과 DB를 백업한다.
검증·다운로드·백업 실패 시 기존 `.env`를 교체하지 않는다.
기존 FERNET_KEY 또는 POSTGRES_PASSWORD 변경은 별도의 데이터·DB 비밀번호 전환 절차가 필요하므로 자동 반영을 거부한다.
JWT_SECRET 변경은 허용하지만 기존 로그인·다운로드 토큰이 무효화된다.
성공·실패 시 전송용 임시 `.env`를 정리한다.

Environment Variables는 필요할 때만 설정한다.

| 이름 | 기본값 |
|---|---|
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_PORT` | `22` |

Mac에서 host key 행을 확인한다.

```bash
ssh-keyscan -t ed25519 -H SERVER_IP > /tmp/daunload_known_hosts
ssh-keygen -lf /tmp/daunload_known_hosts
cat /tmp/daunload_known_hosts
```

`ssh-keyscan` 결과만으로 서버의 진위를 확인할 수는 없다.
서버 콘솔에서 `sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`로 확인한
지문과 Mac에서 얻은 키의 지문을 비교한 뒤 등록한다.
SSH 포트가 22가 아니라면 `ssh-keyscan -t ed25519 -p PORT -H SERVER_IP`를 사용한다.
워크플로에서는 host key 검증을 끄지 않는다.

GHCR 인증은 GitHub이 자동 발급하는 `GITHUB_TOKEN`을 사용하므로 별도 PAT는 필요 없다.
패키지 설정을 별도로 변경했다면 해당 저장소의 Actions에 패키지 읽기·쓰기 권한을 허용한다.
서버에는 배포 중에만 임시 Docker 인증 파일을 만들고 종료 시 삭제한다.

## 3. SSH 방화벽 확인

GitHub 호스팅 러너에서 서버의 SSH 포트에 도달할 수 있어야 한다.
22번 포트를 본인 IP `/32`에만 허용했다면 GitHub Actions는 접속하지 못한다.
고정 출구 IP를 쓰는 러너나 VPN을 구성하면 방화벽에서 그 IP만 허용할 수 있다.
기본 GitHub 호스팅 러너의 주소는 바뀌므로 특정 러너 IP 하나를 고정해서 등록하지 않는다.
SSH를 모든 IP에 허용하는 운영 방식을 선택한다면 SSH 키 인증을 사용하고 비밀번호 로그인을 비활성화한다.
웹 포트 80·443은 기존대로 유지한다.

## 4. 최초 실행

워크플로와 배포 스크립트를 커밋하고 `main`에 push한다.
GitHub **Actions → Deploy production**에서 실행 결과를 확인한다.
이후 `main`에 PR을 merge하면 같은 절차가 자동 실행된다. 직접 push해도 실행된다.
워크플로 전체와 서버 배포에는 각각 동시 실행 잠금이 있으며 실행 중인 배포를 새 커밋으로 취소하지 않는다.

서버 배포 순서는 다음과 같다.

1. GHCR 로그인과 이미지 다운로드. 실패하면 기존 서비스 유지.
2. 선택적으로 새 환경 설정 검증, 이전 이미지 참조 보관과 DB·환경 설정 백업. 실패하면 기존 서비스 유지.
3. API·워커·Caddy 중지 후 선택적으로 `.env` 교체. 이 시점부터 짧은 서비스 중단 발생.
4. 새 이미지로 `alembic upgrade head`. 실패하면 다음 단계 중단.
5. 새 API·워커·Caddy 실행과 컨테이너 상태 확인.
6. 새 `DOMAIN`의 공개 HTTPS 상태 확인 후 성공한 이미지 참조를 서버의 `.release.env`에 저장.

브라우저·ffmpeg·외부 PostgreSQL 통합 테스트는 자동 배포의 테스트 단계에서 제외한다.
HTTP 상태 확인은 실제 LMS 재생 성공까지 보장하지 않는다.
새 이미지는 모든 변경을 포함하므로 API·재생 워커·동기화 워커를 함께 업데이트한다.

## 5. 이미지 선택과 실패 복구

`compose.sh`는 `.env` 다음에 `.release.env`를 읽어 성공한 이미지 digest를 유지한다.
자동 배포를 시작한 뒤 `.env`의 `APP_IMAGE_TAG`만 바꾸어서는 이미지가 바뀌지 않는다.
수동으로 이미지 참조를 지정하려면 환경변수 `APP_IMAGE`를 사용한다.

이전 이미지 참조는 `.release.env.previous`에, DB 백업은 `/var/backups/ssu-lms/`에 보관된다.
환경 설정을 교체한 경우 바로 이전 `.env`는 서버의 `.env.previous`에도 보관된다.
백업 아카이브에는 배포 전 `.release.env`도 포함된다(최초 자동 배포 전에는 없을 수 있다).
**마이그레이션 이후 자동 롤백은 하지 않는다.** 이전 코드와 새 스키마가 호환되는지 확인해야 한다.

스키마가 호환되는 경우에만 서버에서 이전 이미지 선택을 복원하고 실행한다.

```bash
sudo cp /opt/ssu-lms/deploy/hetzner/.release.env.previous /opt/ssu-lms/deploy/hetzner/.release.env
sudo /opt/ssu-lms/deploy/hetzner/compose.sh up -d --no-build --wait
```

이 명령은 DB를 되돌리지 않는다. 호환되지 않으면 배포 전 DB 백업과 이전 코드·설정으로 복구한다.
환경 설정도 되돌려야 한다면 `.env.previous` 또는 백업의 `app.env`를 복원한다.
마이그레이션 단계 이후 실패하면 새 `.env`는 유지되며 자동으로 이전 설정을 복원하지 않는다.
실패 시 `compose.sh ps -a`, `compose.sh logs --tail=100 api worker sync-worker`로 상태를 확인한다.
디스크 부족을 피하도록 백업과 이미지 용량을 관찰하고, 검증된 이전 이미지는 보존한다.

참고: [GitHub 이미지 게시](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images),
[Docker Actions 캐시](https://docs.docker.com/build/ci/github-actions/cache/),
[GitHub 동시 실행](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
