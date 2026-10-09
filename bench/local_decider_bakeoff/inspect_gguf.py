"""Offline check of the two GGUFs (see RUN.md): decision metadata of Clef, and Eikos's letters A-Z as
single tokens identical between the checkpoint tokenizer and the GGUF vocabulary (the CPU half of guard 3).

    PYTHONPATH=<llama.cpp>/gguf-py python inspect_gguf.py <bakeoff>/weights
"""
import collections
import sys

from gguf import GGUFReader
from transformers import AutoTokenizer

W = sys.argv[1]
def field(r, k):
    f = r.fields.get(k)
    if f is None:
        return None
    v = f.parts[f.data[0]] if f.data else None
    try:
        return bytes(v).decode() if f.types and f.types[0].name == "STRING" else (v.tolist() if hasattr(v, "tolist") else v)
    except Exception:
        return v
for name in ["clef-flash-gguf/Cloudflare_clef-flash-Q4_K_M.gguf", "eikos-4b-gguf/Eikos-4B-Q8_0.gguf"]:
    r = GGUFReader(f"{W}/{name}")
    arch = field(r, "general.architecture")
    print("==", name, "arch", arch, "tensors", len(r.tensors))
    for k in r.fields:
        if any(s in k for s in ("decision", "rope", "context_length", "block_count", "file_type")) :
            print("  ", k, field(r, k))
    types = collections.Counter((t.name.split(".")[0] if not t.name.startswith("blk") else "blk", t.tensor_type.name) for t in r.tensors)
    print("  tensor types:", dict(types))
    if "eikos" in name:
        toks = r.fields["tokenizer.ggml.tokens"]
        vocab = [bytes(toks.parts[i]).decode("utf-8", "replace") for i in toks.data]
        tok = AutoTokenizer.from_pretrained(f"{W}/eikos-4b")
        bad = []
        for L in [chr(65+i) for i in range(26)]:
            ids = tok.encode(L, add_special_tokens=False)
            if len(ids) != 1 or vocab[ids[0]] != L:
                bad.append((L, ids, [vocab[i] for i in ids]))
        print("  letters A-Z single-token and identical in GGUF vocab:", not bad, bad[:3], "vocab", len(vocab), "hf", len(tok))
