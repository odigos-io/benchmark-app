# Everything targets your current kubectl context unless KUBE_CONTEXT is set.
KUBE_CONTEXT ?=
CELLS ?= s schatty m l xl
KUBECTL = kubectl $(if $(KUBE_CONTEXT),--context $(KUBE_CONTEXT))

# A registry your cluster can pull from: ECR, ACR, Artifact Registry, GHCR, Harbor...
#   REGISTRY=myregistry.azurecr.io/benchmark-app make images render
REGISTRY ?=
TAG ?= v0.1.0
APP_IMAGE ?= $(REGISTRY)/app:$(TAG)
ECHO_IMAGE ?= $(REGISTRY)/echo:$(TAG)
RUNNER_IMAGE ?= $(REGISTRY)/runner:$(TAG)
# kubectl inside the orchestrator; keep it within one minor version of your cluster.
KUBECTL_VERSION ?= v1.33.4

.PHONY: images app-image echo-image runner-image render lint test verify-nodes install-odigos \
        registry-secret deploy-shared deploy-cells

images: app-image echo-image runner-image

app-image:
	@: $${REGISTRY?set REGISTRY}
	docker build --platform linux/amd64 -t $(APP_IMAGE) app
	docker push $(APP_IMAGE)

echo-image:
	@: $${REGISTRY?set REGISTRY}
	docker build --platform linux/amd64 -t $(ECHO_IMAGE) downstream
	docker push $(ECHO_IMAGE)

runner-image:
	@: $${REGISTRY?set REGISTRY}
	docker build --platform linux/amd64 --build-arg KUBECTL_VERSION=$(KUBECTL_VERSION) -t $(RUNNER_IMAGE) runner
	docker push $(RUNNER_IMAGE)

# Writes k8s/rendered/: one manifest per cell plus the orchestrator, pointing at your images.
render:
	@: $${REGISTRY?set REGISTRY (the same value you built the images with)}
	@mkdir -p k8s/rendered
	@for c in $(CELLS); do \
	  kubectl kustomize k8s/cells/$$c \
	    | sed -e 's#benchmark-app/app:latest#$(APP_IMAGE)#' -e 's#benchmark-app/echo:latest#$(ECHO_IMAGE)#' \
	    > k8s/rendered/cell-$$c.yaml && echo "rendered k8s/rendered/cell-$$c.yaml"; \
	done
	@sed -e 's|$${RUNNER_IMAGE}|$(RUNNER_IMAGE)|' k8s/runner/orchestrator.yaml > k8s/rendered/orchestrator.yaml \
	  && echo "rendered k8s/rendered/orchestrator.yaml"

test:
	@python3 analysis/selftest.py

lint:
	@for f in bin/*.sh bin/lib/*.sh infra/*.sh infra/*/*.sh; do bash -n $$f && echo "ok $$f"; done
	@python3 -m py_compile bin/lib/*.py analysis/*.py && echo "ok python"
	@for c in $(CELLS); do kubectl kustomize k8s/cells/$$c >/dev/null && echo "ok kustomize $$c"; done
	@python3 analysis/selftest.py

verify-nodes:
	KUBE_CONTEXT=$(KUBE_CONTEXT) bash infra/verify-nodes.sh

install-odigos:
	KUBE_CONTEXT=$(KUBE_CONTEXT) bash infra/install-odigos.sh

# Only needed if your registry requires credentials. One pull secret per cell namespace.
#   REGISTRY_SERVER=myregistry.azurecr.io REGISTRY_USER=... REGISTRY_PASSWORD=... make registry-secret
registry-secret:
	@: $${REGISTRY_SERVER?set REGISTRY_SERVER} $${REGISTRY_USER?set REGISTRY_USER} $${REGISTRY_PASSWORD?set REGISTRY_PASSWORD}
	@for c in $(CELLS); do \
	  $(KUBECTL) create namespace cell-$$c --dry-run=client -o yaml | $(KUBECTL) apply -f - >/dev/null; \
	  $(KUBECTL) -n cell-$$c create secret docker-registry registry-pull \
	    --docker-server=$$REGISTRY_SERVER --docker-username=$$REGISTRY_USER --docker-password=$$REGISTRY_PASSWORD \
	    --dry-run=client -o yaml | $(KUBECTL) apply -f - ; \
	done

deploy-shared:
	$(KUBECTL) apply -f k8s/sink/otlp-sink.yaml
	$(KUBECTL) apply -f k8s/runner/namespace.yaml
	$(KUBECTL) apply -f k8s/runner/node-probe.yaml
	$(KUBECTL) apply -f k8s/odigos/destination.yaml

deploy-cells:
	@for c in $(CELLS); do $(KUBECTL) apply -f k8s/rendered/cell-$$c.yaml; done
