"""Tests for sharing Utter: install.sh, the model download, the certificate, README and licence."""

import dataclasses
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from scripts import build_app, download_model as dm

ROOT = Path(__file__).resolve().parent.parent


# ---- Model download ------------------------------------------------------------------------

def _repo_listing(weights: bytes, name: str = "weights.safetensors"):
    return [
        {"name": "config.json", "size": 2, "sha256": None},
        {"name": name, "size": len(weights), "sha256": hashlib.sha256(weights).hexdigest()},
    ]


def _fake_fetch(files):
    calls = []

    def fetch(repo, local_dir):
        calls.append(repo)
        for name, data in files.items():
            (Path(local_dir) / name).write_bytes(data)
    return fetch, calls


def test_download_checks_the_files_then_puts_the_model_where_utter_looks(tmp_path):
    from engine.transcriber import resolve_local_model
    weights = b"w" * 100
    fetch, calls = _fake_fetch({"config.json": b"{}", "weights.safetensors": weights})
    folder = dm.download("large-v3-turbo", tmp_path, fetch=fetch,
                         list_files=lambda repo: _repo_listing(weights), say=lambda m: None)
    assert calls == ["mlx-community/whisper-large-v3-turbo"]
    assert folder == tmp_path / "whisper-large-v3-turbo-mlx" and dm.model_ready(folder)
    assert not (tmp_path / "whisper-large-v3-turbo-mlx.part").exists()
    assert resolve_local_model("large-v3-turbo", tmp_path)[0] == str(folder)

    again, calls = _fake_fetch({})
    dm.download("large-v3-turbo", tmp_path, fetch=again, list_files=lambda r: [], say=lambda m: None)
    assert calls == []  # already there: nothing downloaded


@pytest.mark.parametrize("model", list(dm.MODELS))
def test_every_downloadable_model_is_found_by_utter(tmp_path, model):
    from engine.transcriber import resolve_local_model
    folder = tmp_path / dm.MODELS[model][1]
    folder.mkdir()
    (folder / "weights.npz").write_bytes(b"x")
    assert resolve_local_model(model, tmp_path, fallback_size="") == (str(folder), model)


def test_a_cut_off_download_is_never_used_and_carries_on_next_time(tmp_path):
    from engine.transcriber import resolve_local_model
    weights = b"w" * 100
    fetch, _ = _fake_fetch({"config.json": b"{}", "weights.safetensors": weights[:40]})
    with pytest.raises(dm.DownloadError, match="(?s)not complete.*again"):
        dm.download("small", tmp_path, fetch=fetch, list_files=lambda r: _repo_listing(weights),
                    say=lambda m: None)
    assert not (tmp_path / "whisper-small-mlx").exists()
    assert (tmp_path / "whisper-small-mlx.part").is_dir()  # kept, so the next run resumes
    assert resolve_local_model("small", tmp_path, fallback_size="")[0] is None

    fetch, _ = _fake_fetch({"weights.safetensors": weights})
    dm.download("small", tmp_path, fetch=fetch, list_files=lambda r: _repo_listing(weights), say=lambda m: None)
    assert dm.model_ready(tmp_path / "whisper-small-mlx")


def test_a_damaged_weights_file_is_removed_so_it_is_downloaded_again(tmp_path):
    weights = b"w" * 100
    fetch, _ = _fake_fetch({"config.json": b"{}", "weights.safetensors": b"x" * 100})  # right size
    with pytest.raises(dm.DownloadError, match="damaged"):
        dm.download("small", tmp_path, fetch=fetch, list_files=lambda r: _repo_listing(weights), say=lambda m: None)
    assert not (tmp_path / "whisper-small-mlx.part" / "weights.safetensors").exists()


def test_a_network_error_says_how_to_carry_on(tmp_path):
    def broken(repo, local_dir):
        raise OSError("connection reset")
    with pytest.raises(dm.DownloadError, match="(?s)connection reset.*again"):
        dm.download("small", tmp_path, fetch=broken, list_files=lambda r: [], say=lambda m: None)


def test_a_model_already_on_the_mac_needs_no_question(tmp_path, monkeypatch):
    folder = tmp_path / "whisper-large-v3-turbo-mlx"
    folder.mkdir()
    (folder / "weights.safetensors").write_bytes(b"x")
    monkeypatch.setattr(dm, "ask", lambda *a, **k: pytest.fail("asked"))
    monkeypatch.setattr(dm, "download", lambda *a, **k: pytest.fail("downloaded"))
    assert dm.main(["--models-dir", str(tmp_path)]) == 0


def test_saying_no_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "mac_memory_bytes", lambda: 16 * 1024 ** 3)
    monkeypatch.setattr(dm, "ask", lambda *a, **k: False)
    monkeypatch.setattr(dm, "download", lambda *a, **k: pytest.fail("downloaded"))
    assert dm.main(["--models-dir", str(tmp_path)]) == 1


def test_an_8_gb_mac_is_offered_the_small_model(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "mac_memory_bytes", lambda: 8 * 1024 ** 3)
    questions, got = [], []
    monkeypatch.setattr(dm, "ask", lambda q, *a, **k: questions.append(q) or True)
    monkeypatch.setattr(dm, "download", lambda model, folder, **k: got.append(model))
    assert dm.main(["--models-dir", str(tmp_path)]) == 0
    assert got == ["small"] and "small model instead" in questions[0]


def test_not_enough_space_stops_before_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "mac_memory_bytes", lambda: 16 * 1024 ** 3)
    monkeypatch.setattr(dm, "free_bytes", lambda folder: 10 ** 8)
    monkeypatch.setattr(dm, "download", lambda *a, **k: pytest.fail("downloaded"))
    assert dm.main(["--models-dir", str(tmp_path), "--yes"]) == 1


def test_choosing_the_small_model_saves_it_in_the_settings(isolate_data_paths, monkeypatch):
    from engine.config import load_user_config
    paths = isolate_data_paths
    monkeypatch.setattr(dm, "download", lambda model, folder, **k: None)
    monkeypatch.setattr(dm, "free_bytes", lambda folder: 10 ** 12)
    assert dm.main(["--small", "--yes"]) == 0
    assert load_user_config(paths).whisper.model_size == "small"
    assert "# Whisper model size" in paths.user_config.read_text(encoding="utf-8")  # comments kept


# ---- Packages --------------------------------------------------------------------------------

def _requirements(name):
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return [line.split(">")[0].split("=")[0].strip().lower() for line in lines
            if line.strip() and not line.startswith(("#", "-r"))]


def test_people_installing_utter_get_no_pytorch():
    app = _requirements("requirements.txt")
    assert "torch" not in app and "silero-vad" not in app and "mlx-whisper" not in app
    for needed in ("numpy", "mlx", "rumps", "pynput", "sounddevice", "numba", "scipy", "tiktoken",
                   "huggingface_hub"):
        assert needed in app
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert '--no-deps "mlx-whisper>=0.4.0"' in script and "-r \"$ROOT/requirements.txt\"" in script


def test_mlx_whisper_needs_are_all_in_the_app_list():
    from importlib.metadata import requires
    app = set(_requirements("requirements.txt"))
    for line in requires("mlx-whisper"):
        name = line.split(">")[0].split("=")[0].split(";")[0].strip().lower().replace("-", "_")
        if name == "torch":
            continue
        assert name in {a.replace("-", "_") for a in app}, name


def test_developers_still_get_everything():
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev
    for name in ("mlx-whisper", "pytest", "silero-vad", "torch"):
        assert name in dev


# ---- install.sh and the certificate ------------------------------------------------------------

def test_install_script_is_valid_bash_and_explains_itself():
    script = ROOT / "install.sh"
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)
    out = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True, check=True).stdout
    for option in ("--yes", "--small", "--no-update", "--no-open", "--applications"):
        assert option in out
    bad = subprocess.run(["bash", str(script), "--nope"], capture_output=True, text=True)
    assert bad.returncode == 2 and "Unknown option" in bad.stderr


def test_install_script_asks_before_downloads_and_the_keychain():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    uv_install = script.index("astral.sh/uv/install.sh | ")
    assert script.rfind('ask "Download and install uv?"', 0, uv_install) != -1
    cert = script.index('"$ROOT/scripts/make_certificate.sh"')
    assert script.rfind("ask \"Make the", 0, cert) != -1
    assert "UV_NO_MODIFY_PATH=1" in script  # uv doesn't change the shell's settings
    assert "git -C \"$ROOT\" pull --ff-only" in script  # updates never merge or overwrite changes


def test_certificate_is_a_code_signing_certificate(tmp_path):
    script = ROOT / "scripts" / "make_certificate.sh"
    assert os.access(script, os.X_OK)
    subprocess.run([str(script), "--files", str(tmp_path)], check=True, capture_output=True)
    text = subprocess.run(["/usr/bin/openssl", "x509", "-in", str(tmp_path / "cert.pem"), "-noout", "-text"],
                          capture_output=True, text=True, check=True).stdout
    assert "CN=Utter Developer" in text and "Code Signing" in text and "CA:FALSE" in text
    assert (tmp_path / "utter.p12").stat().st_size > 0


# ---- Build ---------------------------------------------------------------------------------------

def test_utter_goes_to_your_own_applications_folder_if_it_may_not_write_to_the_mac_one(monkeypatch):
    monkeypatch.setattr(build_app.os, "access", lambda path, mode: False)
    assert build_app.applications_folder() == Path.home() / "Applications"
    monkeypatch.setattr(build_app.os, "access", lambda path, mode: True)
    assert build_app.applications_folder() == Path("/Applications")


def test_install_into_a_chosen_folder(tmp_path, monkeypatch):
    app = tmp_path / "dist" / "Utter.app"
    (app / "Contents").mkdir(parents=True)
    calls = []

    def fake_run(args, *a, **k):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1)  # pgrep: not running
    monkeypatch.setattr(build_app.subprocess, "run", fake_run)
    monkeypatch.setattr(build_app, "run", lambda args, **k: calls.append(args))
    dest = build_app.install(app, applications=tmp_path / "Apps")
    assert dest == tmp_path / "Apps" / "Utter.app" and (tmp_path / "Apps").is_dir()
    assert ["ditto", str(app), str(dest)] in calls


# ---- The app without a model ------------------------------------------------------------------

def _app(transcriber):
    from engine.config import VocabularyConfig, load_config
    from engine.session import SessionManager
    from interface.app import VoiceAssistantApp
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    app = VoiceAssistantApp(config=config, session_manager=SessionManager(config=config), transcriber=transcriber)
    app.overlay = None
    return app


def test_without_a_model_utter_says_how_to_get_one(no_real_alerts):
    import types
    import interface.app as app_mod
    app = _app(types.SimpleNamespace(model_path=None))
    app.on_model_ready()
    assert app.status_item.title == app_mod.NO_MODEL_STATUS
    assert no_real_alerts and "./install.sh" in no_real_alerts[0][0][1]
    app.menu_icon.stop()


def test_with_a_model_nothing_changes(no_real_alerts):
    import types
    app = _app(types.SimpleNamespace(model_path="/models/whisper-large-v3-turbo-mlx"))
    app.on_model_ready()
    assert "No speech model" not in app.status_item.title and not no_real_alerts
    app.menu_icon.stop()


# ---- README, licence, privacy ------------------------------------------------------------------

def test_readme_has_the_install_command_for_this_repository():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    remote = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    if remote:
        assert f"git clone {remote}" in readme
    assert "./install.sh" in readme and "PRIVACY.md" in readme and "Uninstall" in readme


def test_licence_and_privacy_policy_are_there():
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    assert "email address" in privacy and "never sends audio" in privacy
    assert "Supabase" in privacy and "Sign Out" in privacy and "[CONTACT EMAIL]" not in privacy
