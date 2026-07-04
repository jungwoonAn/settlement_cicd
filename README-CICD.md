# CI/CD 파이프라인 사용 안내

## 레포 구조 (가정)
Dockerfile의 `COPY src/ .` 및 테스트의 `from settlement.main import app` 임포트를 기준으로
아래 구조를 가정했습니다. 실제 레포 구조가 다르면 workflow의 `src/`, `tests/` 경로를 맞춰주세요.

```
.
├── .github/workflows/ci-cd.yml
├── src/settlement/
│   ├── main.py
│   ├── models/models.py
│   └── services/settlement_service.py
├── tests/test_settlement.py
├── k8s/{deployment.yaml, service.yaml}
├── scripts/setup-runner-vm.sh
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

## 실행 전 반드시 확인할 것 (로컬 검증 결과)

파이프라인을 실제로 돌려본 결과, **현재 코드 그대로는 2개 게이트에서 실패**합니다.

1. **black 포맷 검사 실패** — `main.py`, `models.py`, `settlement_service.py`,
   `test_settlement.py` 4개 파일이 black 스타일(정렬된 `=`, 주석 정렬 등)과 맞지 않습니다.
   교육용 코드가 가독성을 위해 콜론/등호를 수동 정렬해둔 부분이 원인입니다.
   → 로컬에서 `black src/ tests/` 한 번 돌려서 커밋하거나, CI에 편입하기 전에 포맷을 맞춰두세요.

2. **커버리지 80% 미달 (현재 75.12%)** — `main.py`의 lifespan/시드 데이터 함수,
   에러 핸들링 분기(500 처리), `settlement_service.py`의 실패(FAILED) 분기 등이
   테스트에서 다뤄지지 않고 있습니다.
   → 80% 게이트를 그대로 쓰려면 테스트 케이스를 몇 개 더 추가해야 합니다.
     (원하시면 커버리지 채우는 테스트도 만들어 드릴 수 있습니다.)

ruff는 import 정렬(I001), 미사용 import(F401), `except`에서 `raise ... from e` 누락(B904),
FastAPI `Query()`를 기본값으로 바로 호출하는 패턴(B008) 등을 지적합니다 —
`ruff check --fix`로 대부분 자동 수정되지만 B904/B008은 코드 수정이 필요합니다.

## 파이프라인 단계

| 단계 | 도구 | 실패 조건 |
|---|---|---|
| lint | ruff, black | 스타일/정적 분석 위반 시 |
| test | pytest + pytest-cov | 커버리지 80% 미만 시 (`--cov-fail-under=80`) |
| build | docker (멀티스테이지) | 빌드 실패 시 |
| scan | Trivy | CRITICAL 취약점 발견 시 (`exit-code: 1`) |
| deploy | kind load + kubectl | main 브랜치 push 시에만 실행 |

## 레지스트리 없이 배포하는 이유

Docker Hub/ECR/GCR 같은 별도 저장소가 없으므로,
`docker build`로 만든 이미지를 **kind 클러스터 노드에 직접 로드**합니다.

```
docker build → kind load docker-image → kubectl set image
```

이 방식의 제약:
- self-hosted runner와 kind 클러스터가 **반드시 같은 VM**에 있어야 함 (지금 구성이 그렇습니다)
- 이미지가 클러스터 노드 로컬에만 존재하므로 `imagePullPolicy: IfNotPresent`가 필수
  (`Always`로 두면 레지스트리에서 pull을 시도하다 실패합니다)
- 여러 러너/여러 VM으로 확장하려면 결국 로컬 레지스트리(`registry:2` 컨테이너를
  kind와 같은 docker network에 붙이는 방식)가 필요해집니다 — 지금은 단일 VM이라 불필요

## VM 최초 설정

`scripts/setup-runner-vm.sh` 를 VirtualBox Ubuntu 24.04 VM에서 `sudo bash`로 1회 실행하면
Docker, kubectl, kind, kind 클러스터(`settlement-local`), Trivy까지 준비됩니다.
이후 GitHub 레포 Settings → Actions → Runners 안내에 따라 러너를 등록하세요
(라벨에 `settlement`을 추가해야 workflow의 `runs-on: [self-hosted, settlement]`과 매칭됩니다).

## 배포 확인

kind는 외부에서 바로 접근이 안 되므로 포트포워딩으로 확인합니다.

```bash
kubectl port-forward svc/settlement -n settlement 8000:80
curl localhost:8000/health
```
