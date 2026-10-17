"""Offline gallery instrumentation. Never installed or enabled by the bot."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, is_dataclass
import hashlib
import importlib
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from PIL import Image, ImageDraw


def peak_memory():
    """OS process high water mark, not Python allocation or encoded file size."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        data = Counters(); data.cb = ctypes.sizeof(data)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(data), data.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'bytes': data.PeakWorkingSetSize, 'method': 'PeakWorkingSetSize'}
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'bytes': int(value if sys.platform == 'darwin' else value * 1024), 'method': 'ru_maxrss'}


class Observation:
    def __init__(self, output, mode='visual'):
        self.output, self.mode = output, mode
        self.main_thread = threading.get_ident()
        self.reset('')

    def reset(self, key):
        self.key = key
        self.stage = 'prepare'
        self.started = time.perf_counter()
        self.timings = dict.fromkeys(('prepare', 'assets', 'draw', 'encode', 'io', 'observe'), 0.0)
        self.canvases, self.attempts, self.layouts, self.text = [], [], [], []
        self.fonts = {}
        self.current_encoding = None
        self.image_ids = {}
        self.next_image = 0

    @contextmanager
    def phase(self, stage):
        if threading.get_ident() != self.main_thread:
            yield
            return
        now = time.perf_counter()
        self.timings[self.stage] += now - self.started
        previous, self.stage, self.started = self.stage, stage, now
        try:
            yield
        finally:
            now = time.perf_counter()
            self.timings[self.stage] += now - self.started
            self.stage, self.started = previous, now

    def wrap(self, function, stage):
        def call(*args, **kwargs):
            with self.phase(stage):
                return function(*args, **kwargs)
        return call

    def canvas_id(self, image):
        # Keep the object alive until the sample finishes so id reuse cannot
        # attribute a later page to a discarded encoding candidate.
        key = id(image)
        if key not in self.image_ids:
            self.next_image += 1
            self.image_ids[key] = (self.next_image, image)
        return self.image_ids[key][0]

    def encode(self, function):
        def call(image, *args, **kwargs):
            row = {'index': len(self.canvases), 'size': list(image.size), 'mode': image.mode,
                   'status': 'pending', 'canvas_id': self.canvas_id(image)}
            with self.phase('observe'):
                if self.mode == 'visual':
                    row['pixel_sha256'] = hashlib.sha256(image.tobytes()).hexdigest()
                    name = f'canvases/{self.key}-{len(self.canvases)}.png'
                    path = self.output / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    image.save(path, format='PNG')
                    row['lossless_file'] = name
            self.canvases.append(row)
            previous, self.current_encoding = self.current_encoding, row['index']
            try:
                with self.phase('encode'):
                    raw = function(image, *args, **kwargs)
                row.update(status='success', encoded_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
                return raw
            except Exception as exc:
                row.update(status='failed', error=type(exc).__name__, reason=str(exc))
                raise
            finally:
                self.current_encoding = previous
        return call

    def install(self, stack):
        from ournotes_bot.rendering import image_output, haneoka_theme
        encoder = image_output.encode_image
        # Patch the original binding and every direct imported alias. _bytes
        # imports the original at call time and is therefore captured once.
        for name in ('image_output', 'readable_pages', 'song_layout', 'song_detail_layout', 'chart_layout'):
            module = importlib.import_module('ournotes_bot.rendering.' + name)
            stack.enter_context(patch.object(module, 'encode_image', self.encode(encoder)))
        save = Image.Image.save
        def capture_save(image, fp, format=None, **params):
            if self.current_encoding is None:
                return save(image, fp, format, **params)
            attempt = {'canvas': self.current_encoding, 'format': format,
                       'size': list(image.size), 'parameters': params}
            self.attempts.append(attempt)
            try:
                value = save(image, fp, format, **params)
                attempt['status'] = 'success'
                if hasattr(fp, 'tell'): attempt['bytes'] = fp.tell()
                return value
            except Exception as exc:
                attempt.update(status='failed', error=type(exc).__name__)
                raise
        stack.enter_context(patch.object(Image.Image, 'save', capture_save))
        draw_text = ImageDraw.ImageDraw.text
        def capture_text(draw, xy, text, *args, **kwargs):
            font = kwargs.get('font', args[1] if len(args) > 1 else None)
            path = getattr(font, 'path', None)
            if path and os.path.isfile(path): self.fonts[str(path)] = getattr(font, 'size', None)
            if self.mode == 'visual':
                with self.phase('observe'):
                    self.text.append({'canvas_id': self.canvas_id(draw._image), 'position': list(xy),
                                      'text': str(text), 'font': str(path) if path else None,
                                      'size': getattr(font, 'size', None)})
            return draw_text(draw, xy, text, *args, **kwargs)
        stack.enter_context(patch.object(ImageDraw.ImageDraw, 'text', capture_text))
        for name, entry in (('song_layout', 'render_list'), ('catalog_layout', 'render_list'),
                            ('song_detail_layout', 'render'), ('chart_layout', 'render'),
                            ('card_detail_layout', 'render'), ('info_layout', 'render_info'),
                            ('ranking_layout', 'render')):
            module = importlib.import_module('ournotes_bot.rendering.' + name)
            original = getattr(module, entry)
            def render(*args, _fn=original, _name=name, **kwargs):
                with self.phase('draw'):
                    result = _fn(*args, **kwargs)
                if self.mode == 'visual':
                    with self.phase('observe'):
                        if _name == 'catalog_layout':
                            records, ranges = result[1], result[2]
                        elif _name == 'song_layout':
                            records, ranges = args[0], result[1]
                        else:
                            records, ranges = args[:1], None
                        self.layouts.append({'renderer': _name, 'ranges': ranges,
                            'records': [asdict(r) if is_dataclass(r) else r for r in records]})
                return result
            stack.enter_context(patch.object(module, entry, render))
            if hasattr(module, 'prepare_assets'):
                stack.enter_context(patch.object(module, 'prepare_assets', self.wrap(module.prepare_assets, 'assets')))
        # These call sites may prepare assets through the original module.
        stack.enter_context(patch.object(haneoka_theme, 'prepare_assets', self.wrap(haneoka_theme.prepare_assets, 'assets')))

    def result(self):
        now = time.perf_counter()
        self.timings[self.stage] += now - self.started
        self.started = now
        return {'mode': self.mode, 'stages_seconds': dict(self.timings),
                'process_peak_memory': peak_memory(), 'canvases': self.canvases,
                'encoding_attempts': self.attempts, 'layouts': self.layouts,
                'drawn_text': self.text, 'fonts': [
                    {'path': p, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                    for p in sorted(self.fonts)]}
