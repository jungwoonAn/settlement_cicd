#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# VirtualBox Ubuntu 24.04 VM 초기 셋업
#   - Docker, kind, kubectl 설치
#   - kind 클러스터 생성 (이름: settlement-local)
#   - GitHub Actions self-hosted runner를 위한 사용자 준비
#
# 실행: sudo bash setup-runner-vm.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

RUNNER_USER="${SUDO_USER:-$(whoami)}"
KIND_CLUSTER="settlement-local"

echo ">> 1. 기본 패키지 업데이트"
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release

echo ">> 2. Docker 설치"
if ! command -v docker &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
fi

# runner 유저가 sudo 없이 docker 명령 실행 가능하도록
usermod -aG docker "${RUNNER_USER}"

echo ">> 3. kubectl 설치"
if ! command -v kubectl &>/dev/null; then
  curl -fsSLo /usr/local/bin/kubectl \
    "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
  chmod +x /usr/local/bin/kubectl
fi

echo ">> 4. kind 설치"
if ! command -v kind &>/dev/null; then
  curl -fsSLo /usr/local/bin/kind \
    https://kind.sigs.k8s.io/dl/v0.24.0/kind-linux-amd64
  chmod +x /usr/local/bin/kind
fi

echo ">> 5. kind 클러스터 생성 (${KIND_CLUSTER})"
if ! kind get clusters | grep -qx "${KIND_CLUSTER}"; then
  cat <<EOF | kind create cluster --name "${KIND_CLUSTER}" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
EOF
else
  echo "   이미 존재함, 건너뜀"
fi

echo ">> 6. Trivy 설치 (선택 - trivy-action이 자체 다운로드하지만 로컬 캐시용으로 미리 설치 권장)"
if ! command -v trivy &>/dev/null; then
  curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
    | sh -s -- -b /usr/local/bin
fi

echo ""
echo "완료. 이후 절차:"
echo "  1) 로그아웃/재로그인하여 docker 그룹 적용"
echo "  2) GitHub 레포 Settings > Actions > Runners > New self-hosted runner 안내에 따라"
echo "     actions-runner 설치 및 './config.sh' 실행 (label: settlement 추가 권장)"
echo "     예) ./config.sh --url https://github.com/<org>/<repo> --token <TOKEN> --labels settlement"
echo "  3) './run.sh' 또는 systemd 서비스로 등록: sudo ./svc.sh install && sudo ./svc.sh start"
echo "  4) kubectl config current-context 로 kind-${KIND_CLUSTER} 컨텍스트가 잡히는지 확인"
