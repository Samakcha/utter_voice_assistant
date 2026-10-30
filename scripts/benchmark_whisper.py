#!/usr/bin/env python3
"""
Benchmark script for Whisper models on Apple Silicon (MLX).
Measures transcription latency, real-time factor (RTF), and Word Error Rate (WER)
across configured models with optional initial prompt testing.
"""

import argparse
import csv
import logging
from pathlib import Path
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple
import wave

# Auto-reexec inside the virtual environment if available
PROJECT_ROOT = Path(__file__).resolve().parent.parent
venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
if venv_python.is_file() and sys.executable != str(venv_python):
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.config import NormalizationConfig, WhisperConfig
from engine.transcriber import MLX_MODEL_REPO_MAP, MLXWhisperTranscriber, resolve_model_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")

# Model repo download sizes for pre-download confirmation
MODEL_DOWNLOAD_SIZES = {
    "tiny": ("mlx-community/whisper-tiny-mlx", "~71 MB"),
    "tiny.en": ("mlx-community/whisper-tiny.en-mlx", "~71 MB"),
    "base": ("mlx-community/whisper-base-mlx", "~137 MB"),
    "base.en": ("mlx-community/whisper-base.en-mlx", "~137 MB"),
    "small": ("mlx-community/whisper-small-mlx", "~459 MB"),
    "small.en": ("mlx-community/whisper-small.en-mlx", "~459 MB"),
    "medium": ("mlx-community/whisper-medium-mlx", "~1.45 GB"),
    "medium.en": ("mlx-community/whisper-medium.en-mlx", "~1.45 GB"),
    "large-v3-turbo": ("mlx-community/whisper-large-v3-turbo", "~1.54 GB"),
}


def normalize_text(text: str) -> str:
    """Normalize text for WER calculation (lowercase, strip punctuation, normalize whitespace)."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute Word Error Rate (WER) using Levenshtein distance on words."""
    ref_words = normalize_text(reference).split()
    hyp_words = normalize_text(hypothesis).split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    r_len = len(ref_words)
    h_len = len(hyp_words)

    # Matrix initialization
    d = [[0] * (h_len + 1) for _ in range(r_len + 1)]
    for i in range(r_len + 1):
        d[i][0] = i
    for j in range(h_len + 1):
        d[0][j] = j

    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                substitution = d[i - 1][j - 1] + 1
                insertion = d[i][j - 1] + 1
                deletion = d[i - 1][j] + 1
                d[i][j] = min(substitution, insertion, deletion)

    return d[r_len][h_len] / float(r_len)


def load_expected_csv(csv_path: Optional[Path]) -> Dict[str, str]:
    """Load ground truth mappings (filename -> expected text)."""
    if not csv_path or not csv_path.is_file():
        return {}

    mapping = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                filename = row[0].strip()
                expected = row[1].strip()
                mapping[filename] = expected
                # Also index by basename without path
                mapping[Path(filename).name] = expected
    return mapping


def ensure_model_available(model_size: str, models_dir: Path, auto_yes: bool = False) -> bool:
    """Check if model is cached locally; if not, print repo ID/size and prompt user."""
    resolved = resolve_model_path(model_size, models_dir)
    if Path(resolved).is_dir():
        return True  # Already cached locally

    info = MODEL_DOWNLOAD_SIZES.get(model_size.lower())
    repo_id = info[0] if info else model_size
    size_str = info[1] if info else "unknown size"

    print("\n" + "=" * 65)
    print(f"⚠️  MODEL NOT CACHED: '{model_size}'")
    print(f"   Hugging Face Repo: {repo_id}")
    print(f"   Download Size:     {size_str}")
    print(f"   Target Directory:  {models_dir}")
    print("=" * 65)

    if not auto_yes:
        try:
            choice = input(f"Do you want to download {repo_id} ({size_str}) now? [y/N]: ").strip().lower()
            if choice not in ("y", "yes"):
                print(f"Skipping model '{model_size}'.")
                return False
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return False

    print(f"Downloading {repo_id} ({size_str})...")
    from huggingface_hub import snapshot_download
    dest = models_dir / f"whisper-{model_size}-mlx"
    snapshot_download(repo_id, local_dir=str(dest))
    print(f"Downloaded {model_size} successfully to {dest}")
    return True


def run_benchmark(
    wav_dir: Path,
    models: List[str],
    expected_map: Dict[str, str],
    prompts: List[Optional[str]],
    models_dir: Path,
    auto_yes: bool = False,
    normalization_options: List[bool] = [True],
    target_peak_dbfs: float = -3.0,
    max_gain_db: float = 20.0,
) -> None:
    """Execute benchmark over WAV files across models, prompts, and normalization settings."""
    wav_files = sorted(wav_dir.glob("*.wav"))
    if not wav_files:
        print(f"Error: No .wav files found in directory: {wav_dir}")
        return

    print(f"\nFound {len(wav_files)} WAV file(s) in {wav_dir}")
    print(f"Models to evaluate: {', '.join(models)}")
    print(f"Prompts to evaluate: {[p if p is not None else '<none>' for p in prompts]}")
    norm_labels = [("Enabled" if n else "Disabled") for n in normalization_options]
    print(f"Normalization settings to evaluate: {norm_labels}")

    results = []

    norm_cfg = NormalizationConfig(
        enabled=True,
        target_peak_dbfs=target_peak_dbfs,
        max_gain_db=max_gain_db,
    )

    for model_size in models:
        if not ensure_model_available(model_size, models_dir, auto_yes=auto_yes):
            continue

        print(f"\n--- Loading and warming up model: {model_size} ---")
        cfg = WhisperConfig(
            model_size=model_size,
            language="en",
            initial_prompt="",
            models_dir=models_dir,
        )
        transcriber = MLXWhisperTranscriber(cfg, normalization_config=norm_cfg)
        transcriber.wait_until_ready(timeout=60.0)

        for prompt in prompts:
            prompt_label = f"\"{prompt}\"" if prompt else "<none>"

            for norm_flag in normalization_options:
                norm_group_label = "On" if norm_flag else "Off"

                for wav_file in wav_files:
                    res = transcriber.transcribe(wav_file, prompt=prompt, normalize=norm_flag)

                    expected = expected_map.get(wav_file.name, expected_map.get(str(wav_file), ""))
                    wer = compute_wer(expected, res.text) if expected else None

                    gain_str = f"+{res.applied_gain_db:.1f}dB" if (norm_flag and res.applied_gain_db > 0.05) else ("0dB" if norm_flag else "Off")

                    results.append({
                        "model": model_size,
                        "prompt": prompt_label,
                        "norm_group": norm_group_label,
                        "gain_str": gain_str,
                        "file": wav_file.name,
                        "duration": res.audio_duration_sec,
                        "transcript": res.text,
                        "time": res.processing_time_sec,
                        "rtf": res.real_time_factor,
                        "wer": wer,
                        "expected": expected,
                    })

        transcriber.close()

    if not results:
        print("No benchmark results generated.")
        return

    # Print Detailed Results Table
    print("\n" + "=" * 110)
    print("BENCHMARK RESULTS")
    print("=" * 110)
    has_wer = any(r["wer"] is not None for r in results)

    header = f"| {'Model':<10} | {'Prompt':<10} | {'Norm':<8} | {'File':<24} | {'Audio(s)':<8} | {'Time(s)':<8} | {'RTF':<6} |"
    if has_wer:
        header += f" {'WER':<7} |"
    header += f" {'Transcript':<30} |"

    print(header)
    print("|" + "-" * (len(header) - 2) + "|")

    for r in results:
        row = f"| {r['model']:<10} | {r['prompt']:<10} | {r['gain_str']:<8} | {r['file'][:24]:<24} | {r['duration']:>8.2f} | {r['time']:>8.3f} | {r['rtf']:>6.2f}x |"
        if has_wer:
            wer_str = f"{r['wer'] * 100:>6.1f}%" if r["wer"] is not None else "   N/A "
            row += f" {wer_str:<7} |"
        # Truncate transcript if needed
        tr = r["transcript"].replace("\n", " ")
        tr_display = (tr[:27] + "...") if len(tr) > 30 else tr
        row += f" {tr_display:<30} |"
        print(row)

    # Print Summary Averages Table
    print("\n" + "=" * 90)
    print("SUMMARY PER MODEL, PROMPT & NORMALIZATION CONFIGURATION")
    print("=" * 90)

    summary_header = f"| {'Model':<10} | {'Prompt':<10} | {'Norm':<6} | {'Count':<6} | {'Avg Audio':<10} | {'Avg Latency':<12} | {'Avg RTF':<8} |"
    if has_wer:
        summary_header += f" {'Avg WER':<9} |"
    print(summary_header)
    print("|" + "-" * (len(summary_header) - 2) + "|")

    # Group by (model, prompt, norm_group)
    from collections import defaultdict
    groups = defaultdict(list)
    for r in results:
        groups[(r["model"], r["prompt"], r["norm_group"])].append(r)

    for (model, prompt, norm_group), items in groups.items():
        count = len(items)
        avg_audio = sum(i["duration"] for i in items) / count
        avg_time = sum(i["time"] for i in items) / count
        avg_rtf = sum(i["rtf"] for i in items) / count

        wer_items = [i["wer"] for i in items if i["wer"] is not None]
        avg_wer = (sum(wer_items) / len(wer_items)) if wer_items else None

        row = f"| {model:<10} | {prompt:<10} | {norm_group:<6} | {count:>6} | {avg_audio:>9.2f}s | {avg_time:>10.3f}s | {avg_rtf:>7.2f}x |"
        if has_wer:
            wer_str = f"{avg_wer * 100:>7.1f}%" if avg_wer is not None else "    N/A  "
            row += f" {wer_str:<9} |"
        print(row)
    print("=" * 90 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Whisper models on Apple Silicon (MLX).")
    parser.add_argument("--wav-dir", type=Path, default=Path("./recordings"), help="Folder containing WAV files to test.")
    parser.add_argument("--models", type=str, default="small", help="Comma-separated model sizes (e.g. 'small,small.en,tiny').")
    parser.add_argument("--expected", type=Path, default=None, help="Optional CSV file with filename and expected text.")
    parser.add_argument("--prompt", type=str, default=None, help="Initial prompt to test.")
    parser.add_argument("--compare-prompts", action="store_true", help="Compare with and without prompt.")
    parser.add_argument("--models-dir", type=Path, default=Path("./models"), help="Directory for local model weights.")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm model downloads without prompting.")

    # Normalization options
    parser.add_argument("--normalize", action="store_true", default=None, help="Enable peak normalization (default).")
    parser.add_argument("--no-normalize", action="store_true", help="Disable peak normalization.")
    parser.add_argument("--compare-normalization", action="store_true", help="Compare without normalization vs with normalization.")
    parser.add_argument("--target-peak", type=float, default=-3.0, help="Target peak level in dBFS (default -3.0).")
    parser.add_argument("--max-gain", type=float, default=20.0, help="Maximum boost in dB (default 20.0).")

    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    expected_map = load_expected_csv(args.expected)

    if args.compare_prompts:
        prompts = [None, args.prompt or "open Notes"]
    elif args.prompt is not None:
        prompts = [args.prompt]
    else:
        prompts = [None]

    if args.compare_normalization:
        normalization_options = [False, True]
    elif args.no_normalize:
        normalization_options = [False]
    else:
        normalization_options = [True]

    run_benchmark(
        wav_dir=args.wav_dir,
        models=models,
        expected_map=expected_map,
        prompts=prompts,
        models_dir=args.models_dir,
        auto_yes=args.yes,
        normalization_options=normalization_options,
        target_peak_dbfs=args.target_peak,
        max_gain_db=args.max_gain,
    )


if __name__ == "__main__":
    main()
