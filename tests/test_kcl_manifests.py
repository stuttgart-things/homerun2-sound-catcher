"""The KCL profile: the browser-only Kubernetes deployment.

Structural checks always run. Rendering needs the KCL toolchain's Python
bindings (``pip install kcl-lib``) and is skipped without them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from sound_catcher.profile import parse_profile

ROOT = Path(__file__).resolve().parents[1]
KCL_DIR = ROOT / "kcl"
DEPLOY_PROFILE = ROOT / "tests" / "kcl-deploy-profile.yaml"

EXPECTED_MANIFESTS = {
    "serviceaccount.serviceAccount",
    "configmap.configMap",
    "profile_configmap.profileConfigMap",
    "secret.secretRedis",
    "secret.secretApi",
    "secret.secretS3",
    "deploy.deployment",
    "service.service",
    "httproute.httproute",
}

_MANIFEST_LIST = re.compile(r"manifests\s*=\s*\[\s*m for m in \[(.*?)\]\s*if m\s*\]", re.DOTALL)


def test_main_exports_the_expected_resources_and_no_namespace():
    """As in led-catcher: a Namespace in a co-tenanted profile makes every
    Argo CD Application claim it, and one prune takes the stack down."""
    match = _MANIFEST_LIST.search((KCL_DIR / "main.k").read_text())
    assert match
    exported = {line.split("#")[0].strip() for line in match.group(1).splitlines()} - {""}
    assert exported == EXPECTED_MANIFESTS
    for source in KCL_DIR.glob("*.k"):
        assert not re.search(r"""kind\s*:\s*"Namespace\"""", source.read_text()), source.name


def render(**overrides) -> list[dict]:
    api = pytest.importorskip("kcl_lib.api")
    options = yaml.safe_load(DEPLOY_PROFILE.read_text())
    options.update(overrides)
    args = api.ExecProgramArgs(
        k_filename_list=["main.k"],
        work_dir=str(KCL_DIR),
        args=[
            api.Argument(name=k, value=str(v).lower() if isinstance(v, bool) else str(v)) for k, v in options.items()
        ],
    )
    result = api.API().exec_program(args)
    if result.err_message:
        raise ValueError(result.err_message)
    return yaml.safe_load(result.yaml_result)["manifests"]


def by_kind(manifests: list[dict], kind: str) -> dict:
    return next(m for m in manifests if m["kind"] == kind)


def env_of(deployment: dict) -> dict:
    return {e["name"]: e.get("value") for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_one_pod_browser_sink_writable_tmp():
    deployment = by_kind(render(), "Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    env = env_of(deployment)
    assert env["SOUND_SINKS"] == "browser"
    assert env["TTS_CACHE_DIR"].startswith("/tmp/")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert {"name": "tmp", "mountPath": "/tmp"} in container["volumeMounts"]


def test_profile_configmap_is_a_valid_profile_naming_bundled_clips():
    data = by_kind([m for m in render() if m["metadata"]["name"].endswith("-profile")], "ConfigMap")["data"]
    profile = parse_profile(yaml.safe_load(data["profile.yaml"]))
    assert profile.rules and profile.quiet_hours
    for rule in profile.rules:
        if rule.sound:
            assert (ROOT / "sounds" / rule.sound).is_file(), rule.sound


def test_sse_route_only_with_a_timeout_set():
    rules = by_kind(render(), "HTTPRoute")["spec"]["rules"]
    assert rules[0]["matches"][0]["path"]["value"] == "/api/events"
    assert rules[0]["timeouts"] == {"request": "0s"}
    assert rules[-1]["matches"][0]["path"]["value"] == "/"
    plain = by_kind(render(**{"config.httpRouteSseTimeout": ""}), "HTTPRoute")["spec"]["rules"]
    assert len(plain) == 1 and "timeouts" not in plain[0]


def test_secrets_only_when_given():
    kinds = [m["kind"] for m in render()]
    assert "Secret" not in kinds
    secrets = [m for m in render(**{"config.apiToken": "t", "config.redisPassword": "p"}) if m["kind"] == "Secret"]
    assert sorted(s["metadata"]["name"] for s in secrets) == [
        "homerun2-sound-catcher-api",
        "homerun2-sound-catcher-redis",
    ]


def test_s3_sync_off_by_default_and_credentials_from_the_s3_secret():
    env = env_of(by_kind(render(), "Deployment"))
    assert env["SOUNDS_S3_BUCKET"] == ""
    manifests = render(
        **{
            "config.s3Bucket": "homerun-sounds",
            "config.s3Endpoint": "https://minio.example.com",
            "config.s3AccessKey": "ak",
            "config.s3SecretKey": "sk",
        }
    )
    deployment = by_kind(manifests, "Deployment")
    env = env_of(deployment)
    assert env["SOUNDS_S3_BUCKET"] == "homerun-sounds"
    assert env["SOUNDS_S3_ENDPOINT"] == "https://minio.example.com"
    refs = {
        e["name"]: e["valueFrom"]["secretKeyRef"]
        for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        if e["name"].startswith("AWS_")
    }
    assert refs["AWS_ACCESS_KEY_ID"] == {"name": "homerun2-sound-catcher-s3", "key": "accessKey", "optional": True}
    assert "homerun2-sound-catcher-s3" in [m["metadata"]["name"] for m in manifests if m["kind"] == "Secret"]


def test_s3_keys_go_together():
    with pytest.raises(ValueError):
        render(**{"config.s3AccessKey": "ak"})
