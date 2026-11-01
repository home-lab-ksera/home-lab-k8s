# home-lab-k8s Makefile (task runner)

.DEFAULT_GOAL := help

.PHONY: help all phase1 phase2 bootstrap phase5 vm k8s verify recover diagrams validate validate-local
.PHONY: upgrade upgrade-safe upgrade-precheck upgrade-control-plane upgrade-workers upgrade-postcheck
.PHONY: containerd-precheck containerd-safe
.PHONY: add-runner add-runners-all kubelet-certificates

help:
	@echo "home-lab-k8s task runner"
	@echo ""
	@echo "Phases:"
	@echo "  make all                 - phase1 -> phase2 -> bootstrap -> phase5を順番に実行"
	@echo "  make phase1 / make vm     - VMの構成"
	@echo "  make phase2 / make k8s    - k8sの構成"
	@echo "  make kubelet-certificates - serving CSRの自動承認導入と全ノードTLS検証"
	@echo "  make bootstrap            - GitOps bootstrap（ArgoCD + root Application）"
	@echo "  make phase5 / make verify       - 確認"
	@echo "  make validate                   - Docker内のmise toolchainでCI相当の検証"
	@echo "  make validate-local             - ローカル導入済みtoolchainで検証"
	@echo "  make recover                    - Ubuntu再起動後のk8s復旧"
	@echo "  make diagrams                   - クラスタ全体構成図を生成 (cluster-diagram.png)"
	@echo ""
	@echo "Runners:"
	@echo "  make add-runner REPO=<name> - 共通Runner用workflow生成"
	@echo "  make add-runners-all            - settings.tomlからworkflow一括生成"

all:
	@./automation/scripts/run.sh all

phase1 vm:
	@./automation/scripts/run.sh phase1

phase2 k8s:
	@./automation/scripts/run.sh phase2

kubelet-certificates:
	@bash automation/scripts/kubelet-serving-certificates.sh --prepare

bootstrap:
	@./automation/scripts/run.sh bootstrap

phase5 verify:
	@./automation/scripts/run.sh phase5

validate:
	@docker build -f automation/toolchain/Dockerfile --build-arg LOCAL_UID=$$(id -u) --build-arg LOCAL_GID=$$(id -g) -t k8s-myhome-validator .
	@docker run --rm -v "$$PWD":/work -w /work k8s-myhome-validator

validate-local:
	@automation/scripts/ci/validate.sh

recover:
	@./automation/scripts/recover-after-reboot.sh

diagrams:
	@./automation/scripts/generate-cluster-diagram.sh

upgrade:
	@./automation/scripts/run.sh upgrade

upgrade-safe:
	@./automation/scripts/run.sh upgrade-safe

upgrade-precheck:
	@./automation/scripts/run.sh upgrade-precheck

upgrade-control-plane:
	@./automation/scripts/run.sh upgrade-control-plane

upgrade-workers:
	@./automation/scripts/run.sh upgrade-workers

upgrade-postcheck:
	@./automation/scripts/run.sh upgrade-postcheck

containerd-precheck:
	@./automation/scripts/run.sh containerd-precheck

containerd-safe:
	@./automation/scripts/run.sh containerd-safe

add-runner:
	@if [ -z "$(REPO)" ]; then \
		echo "REPO変数が必要です (例: make add-runner REPO=my-project)"; \
		exit 1; \
	fi
	@if [ -n "$(MIN)$(MAX)" ]; then echo "MIN/MAXは使用しません。共通上限は runners-appset.yaml で管理します。"; fi
	@cd automation/scripts/github-actions && ./add-runner.sh "$(REPO)" "$(or $(STRATEGY),latest)"

add-runners-all:
	@bash -c 'source automation/scripts/settings-loader.sh load 2>/dev/null || true; cd automation/scripts/github-actions && ./add-runners-bulk.sh'
