# Notice

Creator Studio is an independent community application. It is **not** an official Resemble AI product and is not endorsed by Resemble AI.

The speech engines currently supported by this repository are based on Resemble AI's open-source Chatterbox family:

- Chatterbox Multilingual V3
- Chatterbox Turbo
- Chatterbox Nano

The upstream Chatterbox implementation and model repositories are distributed under their applicable open-source licenses. The application preserves the upstream PerTh watermarking behavior in generated audio.

This repository does not redistribute the model weights. Model files are downloaded from the official `ResembleAI` Hugging Face repositories when the user installs or uses a missing model. Creator Studio records the exact local snapshot it selected so a later upstream revision does not silently replace a working model; updating is an explicit user action.

See `requirements.txt` for the pinned upstream source revision and `LICENSE` for this repository's license.
