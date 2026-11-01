# WeldingSlm

A small welding-domain language model (Llama‑3.2‑1B, fine‑tuned with LoRA) plus **Weld Assist**, a local web app that answers welding questions only from your own Word documents. Everything runs on your machine through [Ollama](https://ollama.com).

| Folder | What it is |
|---|---|
| `welding-assistant/` | The web app (chat, agents, formulas, library, drafts). See its [README](welding-assistant/README.md). |
| `welding_slm/` | Model pipeline: datasets, training (`pipeline/train.py`), export (`pipeline/export.py`), evaluation, and the trained `lora_adapter/`. |
| `KnowledgeBase/` | Source `.docx` documents the model and app are built from. |

> **Model weights are not in git.** The GGUF files are 1–2 GB, over GitHub's 100 MB limit. The small LoRA adapter (`welding_slm/lora_adapter`, 45 MB) *is* included, and step 4 rebuilds the Ollama model from it.

## Requirements

- Python 3.10+ (developed on 3.12), Git
- [Ollama](https://ollama.com/download)
- ~6 GB free disk. A GPU is not needed to run or to rebuild the model (merging runs on CPU).

## Installation

### 1. Clone and create an environment
```bash
git clone https://github.com/Naveen3522/WeldingSlm.git
cd WeldingSlm
python -m venv .venv
# Windows:   .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull the embedding model
```bash
ollama pull nomic-embed-text
```
(Search falls back to keywords if it is missing, but results are better with it.)

### 3. Get llama.cpp (used to convert the model to GGUF)
```bash
git clone --depth 1 --branch b11056 https://github.com/ggml-org/llama.cpp welding_slm/llama.cpp
python -m venv venv_gguf
# Windows:   venv_gguf\Scripts\pip install -r requirements-gguf.txt
# Linux/Mac: venv_gguf/bin/pip install -r requirements-gguf.txt
```

### 4. Build the Ollama model from the included adapter
Downloads the base model (`unsloth/llama-3.2-1b-instruct`) from Hugging Face, merges the adapter, converts to Q8_0 GGUF and registers it in Ollama as **`welding-slm-q8`**, the name the app expects.
```bash
pip install -r requirements-model.txt
python welding_slm/pipeline/export.py --adapter welding_slm/lora_adapter --name welding-slm-q8 --work welding_slm/build
```
It ends with a `DONE {...}` line. Check with `ollama list`.

### 5. Create the first administrator and run the app
```bash
cd welding-assistant
python scripts/create_admin.py        # add --ask to choose your own details
start.bat                             # Windows
PYTHONPATH=src python apps/api/server.py   # Linux/Mac (or: make run)
```
Open <http://127.0.0.1:8765/>. Put your `.docx` files in **Library → Add documents**, then press **Reindex**. The sample documents are in `KnowledgeBase/`.

## Optional: retraining
Needs an NVIDIA GPU. `pip install -r requirements-training.txt`, then see `welding_slm/pipeline/train.py` (its docstring shows the arguments). Datasets are in `welding_slm/datasets/`. Afterwards re-run step 4 with your new adapter.

## Not in the repository (by design)
`.venv/`, `venv_gguf/`, `welding_slm/llama.cpp/`, `*.gguf`, `welding_slm/merged_16bit/`, training checkpoints and runs, and the app's runtime data (`welding-assistant/data/app.db`, search index, admin credentials). The index is rebuilt with **Reindex**; credentials are created by `create_admin.py`.
