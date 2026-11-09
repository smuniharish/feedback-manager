"""Static deployment invariants; cluster acceptance still requires a real cluster."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_finite_hardened_deployment_manifests():
    compose = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    job = yaml.safe_load((ROOT / "deploy" / "kubernetes-job.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["learning-smoke"]
    assert service["read_only"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["volumes"] == ["learning-state:/data"]
    assert service["restart"] == "no"
    assert service["mem_limit"] == "2g"
    assert service["cpus"] == 2
    assert job["apiVersion"] == "batch/v1"
    assert job["kind"] == "Job"
    assert job["spec"]["activeDeadlineSeconds"] == 600
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["securityContext"]["runAsNonRoot"]
    assert pod["securityContext"]["runAsUser"] == 10001
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    (container,) = pod["containers"]
    assert container["image"] == service["image"]
    assert not container["securityContext"]["allowPrivilegeEscalation"]
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert set(container["resources"]) == {"requests", "limits"}
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {"/data", "/tmp"}
    assert pod["volumes"][0] == {"name": "state", "emptyDir": {}}
