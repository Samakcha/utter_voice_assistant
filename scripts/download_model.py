#!/usr/bin/env python3
"""
Download the speech model Utter needs, once. install.sh runs this; Utter itself never
downloads anything (it stays offline).

    .venv/bin/python scripts/download_model.py            the model in your settings (asks first)
    .venv/bin/python scripts/download_model.py --small    the smaller model instead
    .venv/bin/python scripts/download_model.py --yes      don't ask

The model comes from Hugging Face (mlx-community, MIT licence) and goes to
~/Library/Application Support/Utter/models. A download that stops half-way carries on where
it stopped the next time. Every file is checked against the size (and, for the weights, the
SHA-256) Hugging Face lists, and only a complete, checked copy is put where Utter looks.
"""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Utter's model name -> (Hugging Face repo, folder name in models/, size shown, memory note)
MODELS: Dict[str, Tuple[str, str, str]] = {
    "large-v3-turbo": ("mlx-community/whisper-large-v3-turbo", "whisper-large-v3-turbo-mlx", "1.6 GB"),
    "small": ("mlx-community/whisper-small-mlx", "whisper-small-mlx", "0.5 GB"),
}
BEST, SMALL = "large-v3-turbo", "small"
PART_SUFFIX = ".part"
LOW_MEMORY_BYTES = 8 * 1024 ** 3  # 8 GB Macs: the small model is the safer choice
WEIGHT_FILES = ("weights.safetensors", "weights.npz")


class DownloadError(Exception):
    pass


def model_ready(folder: Path) -> bool:
    """What Utter itself checks (engine/transcriber.py): a folder with a weights file."""
    return folder.is_dir() and any((folder / name).is_file() for name in WEIGHT_FILES)


def mac_memory_bytes() -> int:
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                  text=True, check=True).stdout.strip())
    except Exception:
        return 0


def free_bytes(folder: Path) -> int:
    folder = Path(folder)
    while not folder.exists():
        folder = folder.parent
    return shutil.disk_usage(folder).free


def repo_files(repo: str) -> List[dict]:
    """[{name, size, sha256 or None}] as Hugging Face lists them."""
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo, files_metadata=True)
    files = []
    for sibling in info.siblings or []:
        lfs = getattr(sibling, "lfs", None)
        sha = None
        if lfs is not None:
            sha = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        files.append({"name": sibling.rfilename, "size": sibling.size, "sha256": sha})
    return files


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_download(folder: Path, files: List[dict]) -> List[str]:
    """What is wrong with the downloaded copy (empty: all good)."""
    problems = []
    for item in files:
        path = folder / item["name"]
        if not path.is_file():
            problems.append(f"{item['name']} is missing")
            continue
        if item["size"] is not None and path.stat().st_size != item["size"]:
            problems.append(f"{item['name']} is {path.stat().st_size} bytes, expected {item['size']}")
            continue
        if item["sha256"] and item["name"] in WEIGHT_FILES and sha256_of(path) != item["sha256"]:
            problems.append(f"{item['name']} is damaged (its checksum does not match)")
            path.unlink()  # so the next run downloads it again instead of keeping it
    if not model_ready(folder):
        problems.append("no weights file")
    return problems


def download(model: str, models_dir: Path, *, fetch: Optional[Callable] = None,
             list_files: Callable[[str], List[dict]] = repo_files, say: Callable[[str], None] = print) -> Path:
    """Download `model` into models_dir and check it; returns its folder."""
    repo, folder_name, size = MODELS[model]
    final = Path(models_dir) / folder_name
    if model_ready(final):
        say(f"The {model} model is already on this Mac ({final}).")
        return final
    part = final.with_name(folder_name + PART_SUFFIX)
    part.mkdir(parents=True, exist_ok=True)
    say(f"Downloading the {model} speech model ({size}) from Hugging Face ({repo})...")
    if fetch is None:
        from huggingface_hub import snapshot_download as fetch
    try:
        files = list_files(repo)
        fetch(repo, local_dir=str(part))
    except Exception as e:
        raise DownloadError(f"The download stopped: {e}\nRun the same command again to carry on.") from e
    say("Checking the download...")
    problems = check_download(part, files)
    if problems:
        raise DownloadError("The download is not complete: " + "; ".join(problems) +
                            "\nRun the same command again to carry on.")
    if final.exists():  # a broken earlier copy without weights
        shutil.rmtree(final)
    part.rename(final)
    say(f"Done: {final}")
    return final


def ask(question: str, default_yes: bool = True) -> bool:
    hint = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default_yes
    return default_yes if not answer else answer.startswith("y")


def use_model_in_settings(model: str, paths) -> None:
    from engine.config import load_user_config
    from engine.settings_file import save_settings
    if load_user_config(paths).whisper.model_size != model:
        save_settings(paths.user_config, {"whisper": {"model_size": model}},
                      default_config=paths.default_config)
        print(f"Utter will use the {model} model (changeable in Settings → Advanced).")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Download Utter's speech model (once).")
    parser.add_argument("--small", action="store_true", help="the smaller, faster, less accurate model")
    parser.add_argument("--yes", action="store_true", help="don't ask")
    parser.add_argument("--models-dir", type=Path, help="download here instead (for testing)")
    args = parser.parse_args(argv)

    from engine.config import data_paths, load_user_config
    from engine.data_folder import prepare_data_folder
    paths = data_paths()
    if args.models_dir is None:
        prepare_data_folder(paths)  # creates the data folder and its settings file
        models_dir = load_user_config(paths).whisper.models_dir
        wanted = load_user_config(paths).whisper.model_size
    else:
        models_dir, wanted = args.models_dir, BEST
    model = SMALL if args.small else (wanted if wanted in MODELS else BEST)

    if not args.small and model == BEST and not model_ready(Path(models_dir) / MODELS[BEST][1]):
        memory = mac_memory_bytes()
        if memory and memory <= LOW_MEMORY_BYTES:
            print(f"This Mac has {memory / 1024 ** 3:.0f} GB of memory. The best model needs about 2 GB of it "
                  f"while Utter runs; the small one ({MODELS[SMALL][2]}) about 1 GB, but it mishears more.")
            if args.yes or ask("Use the small model instead?"):
                model = SMALL

    repo, folder_name, size = MODELS[model]
    if model_ready(Path(models_dir) / folder_name):
        print(f"The {model} speech model is already on this Mac.")
    else:
        needed = int(float(size.split()[0]) * 1.1e9)
        if free_bytes(Path(models_dir)) < needed:
            print(f"Not enough free space: the {model} model needs {size}. Free some space and run this again.")
            return 1
        if not (args.yes or ask(f"Download the {model} speech model ({size}, once) from Hugging Face?")):
            print("Not downloaded. Utter can't turn speech into text without it; run this again any time.")
            return 1
        try:
            download(model, Path(models_dir))
        except DownloadError as e:
            print(str(e))
            return 1
    if args.models_dir is None:
        use_model_in_settings(model, paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
