# L3: Local-only, pinned BGE Chinese ONNX encoder; lazy optional dependencies.
# Pos: Knowledge sources. Reads model files; never downloads or calls an embedding API.
from __future__ import annotations

import hashlib
from pathlib import Path
import threading

from ..domain.knowledge import KnowledgeError

MODEL_REVISION = 'fcecc3c5fef6becfa2b2bdda15c1c938857be534'
MODEL_FILES = {
    'tokenizer.json': '48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26',
    'model_quantized.onnx': '15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc',
}


class LocalBgeEncoder:
    identity = 'bge-small-zh-v1.5/onnx-int8/' + MODEL_REVISION + '/cls-l2-query-instruction-1'
    dimensions = 512

    def __init__(self, directory: Path | None):
        self.directory = directory
        self.lock = threading.Lock()
        self.session = None
        self.tokenizer = None

    def load(self):
        if self.session is not None:
            return
        if self.directory is None:
            raise KnowledgeError('semantic_model_not_configured')
        for name, expected in MODEL_FILES.items():
            try:
                hasher = hashlib.sha256()
                with (self.directory / name).open('rb') as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        hasher.update(block)
                if hasher.hexdigest() != expected:
                    raise KnowledgeError('semantic_model_hash')
            except OSError:
                raise KnowledgeError('semantic_model_missing') from None
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError:
            raise KnowledgeError('semantic_dependencies_missing') from None
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))
        tokenizer.enable_truncation(max_length=512)
        tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
        session = ort.InferenceSession(str(self.directory / 'model_quantized.onnx'),
                                      sess_options=options, providers=['CPUExecutionProvider'])
        self.tokenizer, self.session = tokenizer, session

    def encode(self, texts, *, query=False):
        with self.lock:
            self.load()
            import numpy as np
            rows = []
            # Bounded batches keep the candidate rebuild from taking a GPU or
            # all CPU cores. Query instruction only; CLS pooling, L2 normalized.
            for start in range(0, len(texts), 4):
                encoded = self.tokenizer.encode_batch([
                    ('为这个句子生成表示以用于检索相关文章：' if query else '') + text
                    for text in texts[start:start+4]])
                feed = {'input_ids': np.asarray([e.ids for e in encoded], dtype=np.int64),
                        'attention_mask': np.asarray([e.attention_mask for e in encoded], dtype=np.int64),
                        'token_type_ids': np.asarray([e.type_ids for e in encoded], dtype=np.int64)}
                output = self.session.run(None, {i.name: feed[i.name] for i in self.session.get_inputs()})[0]
                vectors = output[:, 0, :]
                vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
                rows.extend(tuple(float(x) for x in row) for row in vectors)
            return tuple(rows)
