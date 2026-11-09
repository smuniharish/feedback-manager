# Deploying an application with Feedback Manager

Feedback Manager is a library, not a hosted API or agent runtime. The supplied
container runs a finite, synthetic learning/retrieval smoke application. Adapt its
providers and lifecycle management inside your own application before serving
real tenants.

## Podman and Compose

From the repository root:

```bash
podman build -f deploy/Containerfile -t localhost/feedback-manager-learning:0.1.2 .
podman compose -f deploy/compose.yaml up --build --abort-on-container-exit --exit-code-from learning-smoke
```

The application runs as UID/GID 10001, with read-only code, dropped capabilities,
no privilege escalation, writable temporary storage and a named persistent state
volume. Its database health checks, actual indexing/retrieval and isolation
assertions determine success; this is a Job, not a server with a fake HTTP probe.
Run it again against the same volume to verify restart persistence. The expected
output includes `evidence: 2; other-agent results: 0`.

Stop and remove only this Compose application's containers with `podman compose
-f deploy/compose.yaml down`. Add `--volumes` only if you explicitly intend to
destroy this application's retained synthetic state.

## Kubernetes

Load the built image into your cluster's local image store, or publish it to an
approved registry and change the image reference. Then:

```bash
kubectl apply --dry-run=server -f deploy/kubernetes-job.yaml
kubectl apply -f deploy/kubernetes-job.yaml
kubectl wait --for=condition=complete job/feedback-manager-learning-smoke --timeout=300s
kubectl logs job/feedback-manager-learning-smoke
```

The smoke manifest uses `emptyDir` intentionally: it tests execution, not cluster
durability. For persisted state, replace the `state` volume with an
application-managed `ReadWriteOnce` PVC on a filesystem suitable for SQLite WAL
and LadybugDB. Ensure UID/GID 10001 can write it. Set Job parallelism to one and do
not schedule competing writer jobs against the same database.

No cluster context is assumed by the repository tests; a local manifest check is
not evidence of an actual Kubernetes rollout. Configure image pull policy,
registry credentials, storage class, resource limits and backups for your cluster.

## Scaling and health

- Embedded LadybugDB is single-writer. Multiple writer pods on a shared volume are
  not supported. Use an application-supplied network graph provider for that topology.
- SQLite feedback/artifacts/jobs are a complete **single-host example**, not a
  distributed coordination system. Back up state consistently and retain change
  journals until their consumers have checkpointed them.
- Durable learning is opt-in and configuration-checked; use a durable artifact
  repository, job store and atomic feedback journal together.
- For a long-lived application, expose readiness using in-process provider health
  methods. Do not have a probe launch a second LadybugDB writer process.
- Enter the manager's async context to start recovery. On shutdown stop/drain
  workers before closing stores, then flush your telemetry exporters.
- Watch dead-letter counts, retries, quarantined unscoped feedback and projection
  reindex failures. Backend outages are errors, not empty successful searches.
