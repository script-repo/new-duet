# Tandem on Kubernetes (MicroK8s)

Plain manifests for one Tandem pod on a single-node cluster, applied with kustomize:

| file | what |
|---|---|
| `kustomization.yaml` | entry point for the default (mock) deploy: just `base/` |
| `base/namespace.yaml` | namespace `tandem` |
| `base/pvc.yaml` | `tandem-data`, 5Gi, `microk8s-hostpath`, ReadWriteOnce, mounted at `/data` (SQLite, sandboxes, artifacts) |
| `base/deployment.yaml` | 1 replica, `Recreate` (RWO volume), uid/fsGroup 10001, probes on `/api/health` |
| `base/service.yaml` | NodePort `30090` -> 8000 |
| `base/kustomization.yaml` | ConfigMap `tandem-config` (non-secret settings) and the image tag |
| `overlays/openrouter/` | opt-in live mode on OpenRouter's free router (below) |

The manifests sit in `base/` because kustomize refuses an overlay nested inside its own base.

The image is `ghcr.io/script-repo/new-duet`, published by `.github/workflows/ghcr.yml` on every push to
`main` as `latest` and `sha-<short>`.

## 1. Secrets (out of band, never committed)

`TANDEM_AUTH_TOKEN` is **required** by the deployment: the NodePort is reachable from the LAN, and without
a token anyone who can reach it can use the assistant. The pod stays in `CreateContainerConfigError`
until this secret exists (key `token`, exposed to the app as `TANDEM_AUTH_TOKEN`).

```sh
kubectl create namespace tandem --dry-run=client -o yaml | kubectl apply -f -
kubectl -n tandem create secret generic tandem-auth \
  --from-literal=token="$(openssl rand -hex 24)"
# read it back to sign in from the browser:
kubectl -n tandem get secret tandem-auth -o jsonpath='{.data.token}' | base64 -d; echo
```

Provider keys are optional (`tandem-env`, mounted with `envFrom ... optional: true`). Build it from a
`.env` written from `.env.example` (only `KEY=value` lines; comments and blanks are skipped). For OpenRouter put `OPENROUTER_API_KEY` in it:

```sh
kubectl -n tandem create secret generic tandem-env --from-env-file=.env
# after changing it: kubectl -n tandem rollout restart deploy/tandem
```

Values in `tandem-env` override the ConfigMap (it comes later in `envFrom`), so leave `TANDEM_MOCK`,
`TANDEM_PUBLIC_URL`, `TANDEM_HOST`, `TANDEM_PORT` and `TANDEM_DATA_DIR` out of that `.env`.

## 2. Apply

```sh
kubectl apply -k deploy/k8s
kubectl -n tandem rollout status deploy/tandem
curl -fsS http://192.168.68.127:30090/api/health
```

Open <http://192.168.68.127:30090/> and sign in with the token.

## Settings (the patch point)

`base/kustomization.yaml` -> `configMapGenerator` `tandem-config`:

- `TANDEM_PUBLIC_URL=http://192.168.68.127:30090`: how browsers and providers reach the server (OAuth
  redirects, push callbacks). Change it if the node IP or port changes, or put it behind https.
- `TANDEM_MOCK=true`: the first pass runs mock models with the demo data even if provider keys are
  present. Set it to `false` (and create `tandem-env`) for live models. Without `TANDEM_MOCK`, Tandem is
  still in mock mode when there are no provider keys and no `tandem.yaml`.
- `TANDEM_TIMEZONE=America/Toronto`: schedule and briefs.

The ConfigMap name carries a content hash, so editing a literal and re-applying rolls the pod. To pin
an image build instead of `latest`, set `images[0].newTag: sha-<short>`.

## Live mode: OpenRouter free models (opt-in)

```sh
kubectl -n tandem create secret generic tandem-env \
  --from-literal=OPENROUTER_API_KEY=<key> --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k deploy/k8s/overlays/openrouter
kubectl -n tandem rollout status deploy/tandem
curl -fsS http://192.168.68.127:30090/api/health   # "mode":"live"
```

What the overlay changes:

- `overlays/openrouter/tandem.yaml` goes into ConfigMap `tandem-yaml`, mounted read-only at `/config`,
  and `TANDEM_CONFIG=/config/tandem.yaml` points Tandem at it (`Settings.config` in
  `src/tandem/config.py`; `serve` has no `--config` flag). Both model tiers (`cheap`, `strong`) and all
  four roles (`triage`, `brief`, `chat`, `worker`) use `openai_compatible` / `openrouter/free` /
  `https://openrouter.ai/api/v1` with the key from `OPENROUTER_API_KEY`. `sandbox.mode: auto`.
- `TANDEM_MOCK=false`. This matters: `TANDEM_MOCK=true` replaces every model in `tandem.yaml` with mock ones.

Precedence of environment variables in the container, highest first:

1. the container's explicit `env:` list (the overlay's `deployment-patch.yaml` sets `TANDEM_MOCK=false`
   and `TANDEM_CONFIG` here, plus `TANDEM_AUTH_TOKEN` from the base);
2. `envFrom`, where the later source wins on a duplicate key: secret `tandem-env` (listed second) beats ConfigMap `tandem-config`;
3. the image's `ENV` (`TANDEM_HOST`, `TANDEM_PORT`, `TANDEM_DATA_DIR`).

So the overlay's `TANDEM_MOCK=false` wins even if `tandem-env` carries a `TANDEM_MOCK`. The overlay also
merges the same values into `tandem-config` so the ConfigMap doesn't contradict the pod. Edits to
`tandem.yaml` change the ConfigMap's hash and roll the pod. Without a key the app still starts and
reports `"mode":"live"`, but every model call fails with OpenRouter's 401 `Missing Authentication header`.
Back to mock: `kubectl apply -k deploy/k8s` (it leaves the unused `tandem-yaml-*` ConfigMap; delete it
or add `--prune` if you care).

With no `connectors:` in this `tandem.yaml`, live mode has only the `webhook` inbox, not the demo data.
Add `connectors: [{type: fake, name: demo}]` to keep the seeded demo (as in docs/REAL-MODELS.md §3.2).

## Notes

- **Sandbox:** the image has no Docker CLI or socket, so `sandbox.mode: auto` falls back to the local
  sandbox (a scrubbed subprocess in `/data/sandboxes/<task>`, no network isolation; shell commands need
  approval by default). No Docker socket is mounted, on purpose.
- **Private image:** if the GHCR package is private, the cluster needs a pull secret:
  `kubectl -n tandem create secret docker-registry ghcr --docker-server=ghcr.io --docker-username=<user> --docker-password=<PAT with read:packages>`
  and `imagePullSecrets: [{name: ghcr}]` in the pod spec. Making the package public avoids both.
- Postgres and the ambientq quality loop from `docker-compose.yml` are not included; SQLite on the PVC
  is the store. Set `TANDEM_DATABASE_URL` in `tandem-env` to use an external Postgres.
- One replica only: SQLite and the in-process scheduler are not built for more.
