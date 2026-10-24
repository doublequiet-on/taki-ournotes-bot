"""Shared facilities preserve transport caps, identities and adapter permissions."""
from __future__ import annotations

import asyncio
import copy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

import aiohttp

from ournotes_bot.sources.errors import ChartDataError
from ournotes_bot.sources.chart_data import ChartDataError as ExistingChartDataError
from ournotes_bot.sources.json_validation import decode_json
from ournotes_bot.sources.haneoka import chart_data, catalog, identity, transport
from ournotes_bot.sources.haneoka.chart_paths import ASSET_PREFIX, chart_asset_url
from ournotes_bot.sources.haneoka.event_cutoffs import decode_current
from ournotes_bot.sources.moenotes_events import SourceError


def release(server="jp"):
    return dict(schema="haneoka-resource-release-identity-v1", server=server,
                releaseId="r-0123456789abcdef0123", sourceId="synthetic-source.1")


class Response:
    status = 200
    headers = {"X-Haneoka-Release-Id": release()["releaseId"]}
    chunks = (b"12", b"34")

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    @property
    def content(self): return self
    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class Session(Response):
    def get(self, url, **kwargs):
        self.url, self.options = url, kwargs
        return self


class HaneokaFacilityTests(unittest.TestCase):
    def test_scoped_connections_reuse_tcp_and_close_after_success_or_failure(self):
        clients, cookies, sessions = [], [], []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_GET(self):
                clients.append(self.client_address)
                cookies.append(self.headers.get('Cookie'))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.send_header('Set-Cookie', 'unexpected=value')
                self.end_headers()
                self.wfile.write(b'ok')
            def log_message(self, *args): pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_session = aiohttp.ClientSession
        def local_session(**kwargs):
            session = real_session(**kwargs)
            sessions.append(session)
            real_get = session.get
            session.get = lambda url, **options: real_get(
                f'http://127.0.0.1:{server.server_port}/', **options)
            return session
        try:
            with patch.dict(os.environ, {'OURNOTES_PUBLIC_HTTP_PROXY': ''}), \
                    patch('aiohttp.ClientSession', side_effect=local_session):
                with transport.public_http_scope():
                    transport.public_get(transport.ORIGIN + '/test', 2, 1)
                    with transport.public_http_scope():
                        transport.public_get(transport.ORIGIN + '/test', 2, .5)
                    self.assertFalse(sessions[0].closed)
                self.assertTrue(sessions[0].closed)
                self.assertTrue(sessions[0]._loop.is_closed())
                self.assertEqual(clients[0], clients[1])
                with self.assertRaises(ChartDataError), transport.public_http_scope():
                    transport.public_get(transport.ORIGIN + '/test', 1, 1)
                self.assertTrue(sessions[1].closed)
                self.assertTrue(sessions[1]._loop.is_closed())
                self.assertNotEqual(clients[1], clients[2])
                self.assertEqual(cookies, [None, None, None])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_scopes_are_thread_local_and_keep_each_request_timeout(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        def worker():
            with transport.public_http_scope():
                barrier.wait(timeout=3)
                scope = transport._connections.scope
                with transport.public_http_scope():
                    self.assertIs(transport._connections.scope, scope)
                return scope
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: worker(), range(2)))
        self.assertIsNot(a, b)
        response = Session()
        with patch('aiohttp.ClientSession', return_value=response) as create, transport.public_http_scope():
            transport.public_get(transport.ORIGIN + '/test', 4, 20)
            transport.public_get(transport.ORIGIN + '/test', 4, .1)
            self.assertEqual(response.options['timeout'].total, .1)
        self.assertEqual(create.call_count, 1)

    def test_public_proxy_is_explicit_and_preserves_request_security(self):
        response=Session()
        with patch.dict(os.environ,{'OURNOTES_PUBLIC_HTTP_PROXY':'http://127.0.0.1:7890'}),patch('aiohttp.ClientSession',return_value=response) as create:
            self.assertEqual(transport.public_get(transport.ORIGIN+'/test',4,2)[0],b'1234')
        self.assertEqual(response.options['proxy'],'http://127.0.0.1:7890')
        self.assertIs(response.options['allow_redirects'],False)
        self.assertIs(create.call_args.kwargs['trust_env'],False)

    def test_ambient_proxy_is_not_implicitly_used(self):
        with patch.dict(os.environ,{'OURNOTES_PUBLIC_HTTP_PROXY':'','HTTPS_PROXY':'http://user:private@ambient.invalid'}):
            self.assertEqual(transport.public_proxy_options(),{})

    def test_public_proxy_rejects_credentials_paths_and_unsupported_routes(self):
        for value in ('socks5://localhost:1080','http://user:private@proxy','http://user@proxy',
                      'http://proxy/path','http://proxy?token=private','http://proxy/#private',
                      'http://proxy:0','http://proxy:65536','http://','http://bad host'):
            with self.subTest(proxy=value),patch.dict(os.environ,{'OURNOTES_PUBLIC_HTTP_PROXY':value}):
                with self.assertRaises(ChartDataError) as caught:transport.public_proxy_options()
                self.assertNotIn('private',str(caught.exception))

    def test_fixed_note_skin_uses_same_explicit_proxy_without_redirects(self):
        from ournotes_bot.sources.haneoka.game_assets import _fetch,MANIFEST
        response=Session()
        with patch.dict(os.environ,{'OURNOTES_PUBLIC_HTTP_PROXY':'http://127.0.0.1:7890'}),patch('aiohttp.ClientSession',return_value=response) as create:
            self.assertEqual(_fetch(MANIFEST['skin001.json']['url'],timeout=180),b'1234')
        self.assertEqual(response.options['proxy'],'http://127.0.0.1:7890')
        self.assertIs(response.options['allow_redirects'],False)
        self.assertIs(create.call_args.kwargs['trust_env'],False)
        self.assertEqual(create.call_args.kwargs['timeout'].total,180)

    def test_note_preparation_blob_route_requests_raw_bytes_without_credentials_or_redirects(self):
        from ournotes_bot.sources.haneoka.game_assets import _fetch,MANIFEST
        response=Session()
        with patch.dict(os.environ,{'OURNOTES_PUBLIC_HTTP_PROXY':''}),patch('aiohttp.ClientSession',return_value=response) as create:
            self.assertEqual(_fetch(MANIFEST['skin001.png']['prepare_url'],timeout=180),b'1234')
        self.assertEqual(response.options['headers']['Accept'],'application/vnd.github.raw+json')
        self.assertNotIn('Authorization',response.options['headers'])
        self.assertIs(response.options['allow_redirects'],False)
        self.assertIs(create.call_args.kwargs['trust_env'],False)
        self.assertEqual(create.call_args.kwargs['timeout'].total,180)

    def test_public_get_preserves_budget_headers_redirect_and_proxy_policy(self):
        response = Session()
        with patch("aiohttp.ClientSession", return_value=response) as create:
            raw, headers = transport.public_get(transport.ORIGIN + "/api/test", 4, 2.75)
        self.assertEqual(raw, b"1234")
        self.assertEqual(headers, {"x-haneoka-release-id": release()["releaseId"]})
        self.assertEqual(create.call_args.kwargs["timeout"].total, 2.75)
        self.assertIs(create.call_args.kwargs["trust_env"], False)
        self.assertIs(response.options["allow_redirects"], False)
        self.assertEqual(response.options["headers"], {"User-Agent": "Taki-chart/1", "Accept-Encoding": "gzip"})
        self.assertIs(ExistingChartDataError, ChartDataError)
        self.assertIs(chart_data.ChartDataError, ChartDataError)
        self.assertIs(chart_data.public_get, transport.public_get)

    def test_gzip_transport_checks_decoded_size_and_rejects_broken_stream(self):
        body = b'{"data":"' + b"x" * 131072 + b'"}'
        compressed = gzip.compress(body)
        served = [compressed]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(served[0])))
                self.end_headers()
                self.wfile.write(served[0])

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_session = aiohttp.ClientSession

        def local_session(**kwargs):
            session = real_session(**kwargs)
            real_get = session.get
            session.get = lambda url, **options: real_get(
                f"http://127.0.0.1:{server.server_port}/", **options)
            return session

        try:
            with patch.dict(os.environ, {"OURNOTES_PUBLIC_HTTP_PROXY": ""}), \
                    patch("aiohttp.ClientSession", side_effect=local_session):
                self.assertEqual(transport.public_get(transport.ORIGIN + "/test", len(body), 2)[0], body)
                with self.assertRaisesRegex(ChartDataError, "too large"):
                    transport.public_get(transport.ORIGIN + "/test", len(body) - 1, 2)
                served[0] = b"invalid gzip stream"
                with self.assertRaisesRegex(ChartDataError, "request failed"):
                    transport.public_get(transport.ORIGIN + "/test", len(body), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_url_permissions_are_checked_before_any_http(self):
        for url in ("http://haneoka.org/api/test", "https://haneoka.org.evil/test",
                    "https://haneoka.org:443/test", "https://user@haneoka.org/test",
                    "https://haneoka.org/test#fragment", "https://Haneoka.org/test"):
            with self.subTest(url=url), patch("aiohttp.ClientSession", side_effect=AssertionError("HTTP attempted")):
                with self.assertRaises(ChartDataError):
                    transport.public_get(url, 10, 1)

    def test_header_retry_and_body_share_one_real_http_deadline(self):
        scenario, calls = ["headers_once"], []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                mode, number = scenario[0], len(calls)
                if mode == "headers_always" or (mode == "headers_once" and number == 1):
                    time.sleep(.8)
                if mode == "disconnect_once" and number == 1:
                    self.close_connection = True
                    return
                self.send_response(503 if mode == "http_error" else 200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                if mode in ("slow_body", "stalled_body"):
                    time.sleep(.4 if mode == "slow_body" else .8)
                try:
                    self.wfile.write(b"ok")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_session = aiohttp.ClientSession

        def local_session(**kwargs):
            session = real_session(**kwargs)
            real_get = session.get
            session.get = lambda url, **options: real_get(
                f"http://127.0.0.1:{server.server_port}/", **options)
            return session

        try:
            with patch.dict(os.environ, {"OURNOTES_PUBLIC_HTTP_PROXY": ""}), \
                    patch("aiohttp.ClientSession", side_effect=local_session):
                for mode, expected_calls, success in (
                    ("headers_once", 2, True), ("disconnect_once", 2, True),
                    ("headers_always", 2, False), ("slow_body", 1, True),
                    ("stalled_body", 1, False), ("http_error", 1, False),
                ):
                    with self.subTest(mode=mode):
                        scenario[0], calls[:] = mode, []
                        started = time.monotonic()
                        if success:
                            self.assertEqual(transport.public_get(transport.ORIGIN + "/test", 2, .6)[0], b"ok")
                        else:
                            with self.assertRaises(ChartDataError):
                                transport.public_get(transport.ORIGIN + "/test", 2, .6)
                        self.assertLess(time.monotonic() - started, .85)
                        self.assertEqual(len(calls), expected_calls)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_tls_failure_is_not_retried(self):
        class TLSFailure(Response):
            async def __aenter__(self):
                raise aiohttp.ClientSSLError(None, OSError("TLS failed"))
        session = Session()
        with patch("aiohttp.ClientSession", return_value=session), \
                patch.object(session, "get", return_value=TLSFailure()) as get:
            with self.assertRaises(ChartDataError):
                transport.public_get(transport.ORIGIN + "/test", 4, 1)
        self.assertEqual(get.call_count, 1)

    def test_redirect_status_and_stream_overflow_never_return_partial_bytes(self):
        response = Session()
        with patch("aiohttp.ClientSession", return_value=response):
            for status in (301, 302, 307, 404, 500):
                response.status = status
                with self.subTest(status=status), self.assertRaises(ChartDataError):
                    transport.public_get(transport.ORIGIN + "/test", 4, 1)
            response.status = 200
            with self.assertRaises(ChartDataError):
                transport.public_get(transport.ORIGIN + "/test", 3, 1)

    def test_timeouts_and_transport_errors_keep_old_catch_contract(self):
        for error in (asyncio.TimeoutError(), aiohttp.ClientError("offline"), OSError("offline")):
            with self.subTest(error=type(error)), patch("aiohttp.ClientSession", side_effect=error):
                with self.assertRaises(ExistingChartDataError) as caught:
                    transport.public_get(transport.ORIGIN + "/test", 4, .1)
                self.assertIs(caught.exception.__cause__, error)

    def test_strict_json_rejects_duplicates_nonfinite_and_invalid_documents(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}',
                    b'{"a":-Infinity}', b'{"a":1e999}', b'{', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_json(raw)
        self.assertEqual(decode_json(b'{"a":[0,null,1.5]}'), {"a": [0, None, 1.5]})

    def test_static_jp_intl_require_exact_server_and_valid_release_source(self):
        for server in ("jp", "intl"):
            value = release(server)
            before = copy.deepcopy(value)
            self.assertIs(identity.release_identity(value, server), value)
            self.assertIs(catalog.identity(value, server), value)
            self.assertEqual(value, before)
            for wrong in ("jp" if server == "intl" else "intl", "en", "hk", "tw", "kr", "bogus", "JP", None, []):
                with self.subTest(server=server, expected=wrong), self.assertRaises(ChartDataError):
                    identity.release_identity(value, wrong)
            for key, wrong in (("schema", "foreign"), ("releaseId", "r-z" * 20),
                               ("sourceId", "../source"), ("sourceId", "")):
                with self.subTest(key=key), self.assertRaises(ChartDataError):
                    identity.release_identity({**value, key: wrong}, server)
        with self.assertRaises(ValueError):
            identity.resource_identity(release("intl"), "jp")

    def test_every_pinned_response_requires_both_exact_headers(self):
        value = release()
        good = {"x-haneoka-release-id": value["releaseId"], "x-haneoka-source-id": value["sourceId"]}
        identity.check_headers(good, value)
        for headers in ({}, {"x-haneoka-release-id": value["releaseId"]},
                        {**good, "x-haneoka-source-id": "foreign"},
                        {**good, "x-haneoka-release-id": "r-aaaaaaaaaaaaaaaaaaaa"}):
            with self.subTest(headers=headers), self.assertRaises(ChartDataError):
                identity.check_headers(headers, value)

    def test_four_live_regions_cannot_mix_static_intl_or_other_region(self):
        for expected in ("jp", "tw", "kr", "en"):
            self.assertTrue(identity.matches_game_region(expected, expected))
            for actual in ("jp", "tw", "kr", "en", "hk", "intl", "bogus", None):
                if actual == expected:
                    continue
                with self.subTest(expected=expected, actual=actual):
                    self.assertFalse(identity.matches_game_region(actual, expected))
                    with self.assertRaises(SourceError) as caught:
                        decode_current({"region": actual, "stale": False}, expected)
                    self.assertEqual(caught.exception.code, "invalid_event")
        with self.assertRaises(SourceError):
            decode_current({"region": "bogus", "stale": False}, "bogus")

    def test_chart_paths_remain_resource_specific(self):
        label = release()["releaseId"]
        valid = ASSET_PREFIX + "0001/0001_03.bytes"
        self.assertEqual(chart_asset_url(valid, label), transport.ORIGIN + valid + "?release=" + label)
        for path in (valid.replace("/jp/", "/intl/"), valid + "?other=1", valid.replace("0001_03.bytes", "../x.bytes"),
                     "/api/v1/servers/jp/songs", "/assets/jp/Assets/AddressableResources/MemberCard/1/member_full.png"):
            with self.subTest(path=path), self.assertRaises(ExistingChartDataError):
                chart_asset_url(path, label)

    def test_common_facilities_import_without_catalog_or_chart_adapters(self):
        probe = textwrap.dedent('''
            import importlib.abc, sys
            class NoAdapters(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in {'ournotes_bot.data', 'ournotes_bot.sources.chart_data',
                        'ournotes_bot.sources.haneoka.chart_data', 'ournotes_bot.sources.haneoka.catalog',
                        'ournotes_bot.sources.haneoka.event_cutoffs', 'botpy'}:
                        raise AssertionError('common facility imported adapter: ' + fullname)
            sys.meta_path.insert(0, NoAdapters())
            from ournotes_bot.sources.haneoka import transport, identity
            from ournotes_bot.sources.json_validation import decode_json
            assert decode_json(b'{"ok":true}') == {'ok': True}
        ''')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", "-c", probe], cwd=root,
                                env={**os.environ, "PYTHONPATH": str(root / "src")},
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
