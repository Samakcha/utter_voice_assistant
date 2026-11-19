"""
Write the weights of the Silero VAD model Utter uses (silero-vad's 16 kHz PyTorch model)
to assets/silero_vad_16k.safetensors, so engine/vad.py can run it with numpy alone.
Needs the developer packages torch and silero-vad; the app itself never does.

    uv run python scripts/export_vad_weights.py
"""

import json
from pathlib import Path
import struct
import sys

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "assets" / "silero_vad_16k.safetensors"

NAMES = {  # PyTorch model's name -> engine/vad.py's name
    "_model.stft.forward_basis_buffer": "stft_conv.weight",
    "_model.encoder.0.reparam_conv.weight": "conv1.weight",
    "_model.encoder.0.reparam_conv.bias": "conv1.bias",
    "_model.encoder.1.reparam_conv.weight": "conv2.weight",
    "_model.encoder.1.reparam_conv.bias": "conv2.bias",
    "_model.encoder.2.reparam_conv.weight": "conv3.weight",
    "_model.encoder.2.reparam_conv.bias": "conv3.bias",
    "_model.encoder.3.reparam_conv.weight": "conv4.weight",
    "_model.encoder.3.reparam_conv.bias": "conv4.bias",
    "_model.decoder.rnn.weight_ih": "lstm_cell.weight_ih",
    "_model.decoder.rnn.weight_hh": "lstm_cell.weight_hh",
    "_model.decoder.rnn.bias_ih": "lstm_cell.bias_ih",
    "_model.decoder.rnn.bias_hh": "lstm_cell.bias_hh",
    "_model.decoder.decoder.2.weight": "final_conv.weight",
    "_model.decoder.decoder.2.bias": "final_conv.bias",
}


def write_safetensors(path: Path, arrays: dict, metadata: dict) -> None:
    header, body, offset = {"__metadata__": metadata}, b"", 0
    for name, array in arrays.items():
        data = np.ascontiguousarray(array, dtype="<f4").tobytes()
        header[name] = {"dtype": "F32", "shape": list(array.shape), "data_offsets": [offset, offset + len(data)]}
        body += data
        offset += len(data)
    head = json.dumps(header, separators=(",", ":")).encode()
    head += b" " * (-len(head) % 8)
    path.write_bytes(struct.pack("<Q", len(head)) + head + body)


def main() -> int:
    import silero_vad
    from importlib.metadata import version
    state = silero_vad.load_silero_vad().state_dict()
    arrays = {ours: state[theirs].numpy() for theirs, ours in NAMES.items()}
    write_safetensors(OUT, arrays, {"source": f"silero-vad {version('silero-vad')} (16 kHz model)",
                                    "license": "MIT, see silero_vad_LICENSE.txt"})
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
