#!/usr/bin/env python3
"""Automated GUI load/feature test driver for IA-Interact.

Drives the REAL GUI (real mainloop, real workers, real retry/backoff,
skip-existing, derive flag, offline queue, progress plumbing) with the HTTP
layer instrumented, then prints a metrics report:

    python tests/gui_loadtest.py                          # 30 files, 15% flake
    python tests/gui_loadtest.py --files 100 --size-kb 512 --fail-rate 0.3
    python tests/gui_loadtest.py --skip-existing          # + resume coverage
    python tests/gui_loadtest.py --offline-start          # + IA queue coverage
    python tests/gui_loadtest.py --retries 0              # unlimited retries
    python tests/gui_loadtest.py --live                   # REAL uploads to
                                                          # test_collection
                                                          # (<=10 files, <=64 KiB)

Mock mode (default) never touches archive.org: S3 PUT/GET (download) are
answered with instrumented fake responses (per-path transient 503s at
--fail-rate), so the genuine retry loops run for real while the wire stays
quiet. Exit code 0 = all checks passed, 1 = failed.
"""

import argparse
import os
import random
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ia_interact_gui as gui  # noqa: E402


class FakeResponse:
    """Minimal requests.Response stand-in for the instrumented layer."""

    def __init__(self, status_code, reason="Simulated", body=b""):
        self.status_code = status_code
        self.reason = reason
        self._body = body
        self.headers = {}

    def iter_content(self, chunk_size=1024 * 1024):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset:offset + chunk_size]


class Metrics:
    def __init__(self):
        self.put_calls = 0        # upload attempts (incl. retries)
        self.get_download_calls = 0
        self.upload_ok = 0
        self.download_ok = 0
        self.retries = 0          # simulated 503s (extra attempts)
        self.bytes_sent = 0
        self.bytes_received = 0

    def report(self, elapsed_wall):
        return "\n".join([
            "=" * 62,
            " IA-Interact GUI load test report",
            "=" * 62,
            f" upload attempts (incl. retries): {self.put_calls}",
            f" uploads succeeded              : {self.upload_ok}",
            f" downloads succeeded            : {self.download_ok}",
            f" simulated transient failures   : {self.retries}",
            f" bytes sent                     : {self.bytes_sent:,}",
            f" bytes received                 : {self.bytes_received:,}",
            f" wall clock                     : {elapsed_wall:.1f}s",
        ])


def main():
    parser = argparse.ArgumentParser(description="IA-Interact GUI load test")
    parser.add_argument("--files", type=int, default=30)
    parser.add_argument("--size-kb", type=int, default=256, help="per-file size (KiB)")
    parser.add_argument("--fail-rate", type=float, default=0.15,
                        help="chance of a simulated transient 503 per attempt")
    parser.add_argument("--max-flakes", type=int, default=2,
                        help="max simulated failures per file path")
    parser.add_argument("--bandwidth-kbps", type=float, default=2048,
                        help="simulated link speed (time accounting only)")
    parser.add_argument("--retries", type=int, default=5,
                        help="Retries selector value (0 = unlimited)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="pre-seed half the remote paths as already present")
    parser.add_argument("--derive", action="store_true",
                        help="enable derive for uploads (default off, issue #7)")
    parser.add_argument("--offline-start", action="store_true",
                        help="start IA-offline to exercise the action queue")
    parser.add_argument("--live", action="store_true",
                        help="REAL uploads to test_collection (<=10 files x <=64 KiB)")
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()

    if args.live:
        args.files = min(args.files, 10)
        args.size_kb = min(args.size_kb, 64)
        print("LIVE mode: real uploads to 'test_collection' (deleted after 30 days).")

    root = gui.IAInteractGUI()
    # keep the window hidden for the whole run: the GUI logic (workers,
    # queue, progress) runs fine withdrawn, and a sub-second flash of a
    # half-laid-out window looks like a scaling bug when it is only the
    # harness tearing through startup.
    root.withdraw()
    root.login_frame.destroy()
    root.access_key = os.environ.get("S3_ACCESS_KEY", "TESTKEY")
    root.secret_key = os.environ.get("S3_SECRET_KEY", "TESTSECRET")
    root._build_main_screen()
    root.retry_var.set(args.retries)
    root.upload_derive_var.set(args.derive)

    metrics = Metrics()
    expected_uploads = args.files

    if not args.live:
        # ---- instrument the HTTP layer: the real retry loops run for real ----
        rng = random.Random(args.seed)
        state = {}
        real_get = gui.requests.get

        def fake_put(url, headers=None, data=None, timeout=None, proxies=None, **kw):
            metrics.put_calls += 1
            body = getattr(data, "_file", data)
            try:
                size = os.path.getsize(body.name) if hasattr(body, "name") else args.size_kb * 1024
            except OSError:
                size = args.size_kb * 1024
            key = url.split("?")[0]
            state[key] = state.get(key, 0) + 1
            if rng.random() < args.fail_rate and state[key] <= args.max_flakes:
                metrics.retries += 1
                return FakeResponse(503, "SlowDown")
            metrics.upload_ok += 1
            metrics.bytes_sent += size
            return FakeResponse(200, "OK")

        def fake_get(url, stream=False, timeout=None, proxies=None, **kw):
            if "/download/" in url:
                metrics.get_download_calls += 1
                size = args.size_kb * 1024
                metrics.download_ok += 1
                metrics.bytes_received += size
                return FakeResponse(200, "OK", body=b"\\x00" * size)
            return real_get(url, **{k: v for k, v in kw.items() if k in ("params",)})

        gui.requests.put = fake_put
        gui.requests.get = fake_get
        # the health probe must not touch the wire either; stays offline
        # until the simulated recovery so --offline-start really queues
        offline = {"down": args.offline_start}
        gui.check_ia_alive = lambda timeout=6, proxies=None: (
            (False, "[sim] offline") if offline["down"] else (True, "[sim] reachable")
        )

    # build the local workspace: files across folders + subfolders
    tmp_dir = tempfile.mkdtemp(prefix="ia_loadtest_")
    files = []
    for index in range(args.files):
        folder = os.path.join(tmp_dir, f"loadfolder_{index % 3}", f"sub_{index % 2}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"file_{index:04d}.bin")
        with open(path, "wb") as handle:
            handle.write(bytes(random.Random(index).getrandbits(8) for _ in range(args.size_kb * 1024)))
        files.append(path)

    if args.skip_existing and not args.live:
        seeded = {
            f"loadtest/{os.path.basename(path)}": {"size": args.size_kb * 1024, "md5": ""}
            for index, path in enumerate(files) if index % 2 == 0
        }
        gui.fetch_item_file_info = lambda ident, proxies=None, **k: seeded
        expected_uploads = args.files - len(seeded)

    started = time.time()

    def run_upload():
        root._start_upload(
            "ia-interact-loadtest",
            [(path, f"loadtest/{os.path.basename(path)}") for path in files],
            "", queue_derive=args.derive, skip_existing=args.skip_existing,
        )

    def finish():
        elapsed = time.time() - started
        print(metrics.report(elapsed))
        status = root.status_text.get("1.0", "end")
        checks = {
            "every file uploaded (retries recovered all flakes)":
                metrics.upload_ok == expected_uploads,
            "final summary logged": "Upload complete:" in status,
        }
        if args.skip_existing:
            checks["half the files skipped as already-present"] = (
                f"{args.files // 2} skipped (already on item)" in status
            )
        if args.offline_start:
            checks["queued action drained after recovery"] = (
                "archive.org is back online - starting queued" in status
            )
        print("-" * 62)
        all_ok = True
        for name, ok in checks.items():
            print(f" [{'PASS' if ok else 'FAIL'}] {name}")
            all_ok = all_ok and ok
        print("=" * 62)
        root.destroy()
        sys.exit(0 if all_ok else 1)

    def poll():
        if metrics.upload_ok >= expected_uploads:
            root.after(400, finish)  # let the summary line land
        else:
            root.after(250, poll)

    def start():
        root._submit_ia_action("upload", f"loadtest {args.files} file(s)", run_upload)
        root.after(400, poll)

    if args.offline_start:
        root._ia_alive = False
        # recovery: flip the mock probe, then run a real check -> drain
        def recover():
            offline["down"] = False
            root._ia_check_now()
        root.after(1500, recover)
    root.after(500, start)
    root.after(300000, lambda: (print("TIMEOUT - transfers did not finish"), root.destroy(), sys.exit(1)))
    root.mainloop()


if __name__ == "__main__":
    main()
