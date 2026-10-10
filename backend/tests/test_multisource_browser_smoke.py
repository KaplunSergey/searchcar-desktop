"""Opt-in real Chromium/resource smoke on the host OS, no external requests."""

import json
import os
import platform
from pathlib import Path
import subprocess
from time import monotonic, sleep

import pytest
from sqlalchemy import select

from app.desktop_runtime import desktop_playwright
from app.job_queue import claim_next_job
from app.models import ProjectScanRun, ScanRun
from app.scanner import CaptchaError
from app.worker import process_job
from test_multisource_acceptance import api
from test_multisource_queue import queue


def process_resources():
    """Sample just this test's process tree; never collect command lines."""
    if os.name == "nt":
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,WorkingSetSize | ConvertTo-Json -Compress",
        ]
    else:
        command = ["ps", "-axo", "pid=,ppid=,rss="]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as sampler:
        try:
            stdout, stderr = sampler.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            sampler.kill()
            sampler.communicate()
            raise
        if sampler.returncode:
            raise RuntimeError(f"Resource sampler failed: {stderr}")
    if os.name == "nt":
        rows = json.loads(stdout)
        processes = {int(row["ProcessId"]): (int(row["ParentProcessId"]), int(row["WorkingSetSize"] or 0))
                     for row in rows}
    else:
        processes = {int(pid): (int(parent), int(rss) * 1024)
                     for pid, parent, rss in (line.split() for line in stdout.splitlines())}
    owned = {os.getpid()}
    while children := {pid for pid, (parent, _) in processes.items() if parent in owned} - owned:
        owned.update(children)
    # Exclude the short-lived sampling command and its children from leakage checks.
    probe = {sampler.pid}
    while children := {pid for pid, (parent, _) in processes.items() if parent in probe} - probe:
        probe.update(children)
    return {pid: processes[pid][1] for pid in owned if pid in processes and pid not in probe}


@pytest.mark.skipif(os.environ.get("SEARCHCAR_QUEUE_BROWSER_SMOKE") != "1",
                    reason="Opt-in host OS smoke requires prepared Chromium; not a normal unit test")
@pytest.mark.parametrize("outcome", ["success", "captcha", "cancel_site"])
def test_real_chromium_mixed_queue_cleanup_and_locale(api, queue, monkeypatch, tmp_path, outcome):
    executable = Path(os.environ["SEARCHCAR_CHROMIUM_EXECUTABLE"])
    assert executable.is_file(), "Use the prepared bundled Chromium, not an arbitrary download"
    monkeypatch.setattr("app.worker.desktop_playwright", desktop_playwright)
    browsers, contexts, samples = [], [], []
    baseline = process_resources()
    started = monotonic()
    locales = {"alpha": "en-US", "bravo": "ko-KR", "charlie": "uk-UA"}

    def open_browser(key, playwright, **kwargs):
        assert not queue.active
        assert all(not browser.is_connected() for browser in browsers)
        browser = playwright.chromium.launch(headless=True, executable_path=kwargs["executable_path"])
        browsers.append(browser)
        context = browser.new_context(locale=locales[key])
        contexts.append(context)
        page = context.new_page()
        token = object()
        queue.active.add(token)
        queue.events.append(("open", key))
        original_close = browser.close

        def close():
            try:
                original_close()
            finally:
                queue.active.remove(token)
                queue.events.append(("close", key))

        monkeypatch.setattr(browser, "close", close)
        # Existing fixture invariants still exercise the real worker coordinator.
        page.source_key, page.locale, page.token = key, f"test-{key}", token
        # All navigation is fulfilled locally. Block anything not explicitly covered.
        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html",
                                                        body="<!doctype html><title>Queue fixture</title>"))
        page.goto("https://queue-smoke.example.test/")
        assert page.evaluate("navigator.language") == locales[key]
        assert context.cookies() == []
        context.add_cookies([{"name": "source", "value": key, "url": "https://queue-smoke.example.test"}])
        samples.append(process_resources())
        return browser, context, page

    for key, adapter in queue.adapters.items():
        monkeypatch.setattr(adapter, "open_browser", lambda playwright, _key=key, **kwargs:
                            open_browser(_key, playwright, **kwargs))

    response = api.post("/api/scans/projects", json={"all_sources": True})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    def hook(name, kwargs):
        if name != "alpha1":
            return
        if outcome == "captcha":
            raise CaptchaError("local smoke blocked alpha")
        if outcome == "cancel_site":
            assert api.post(f"/api/scans/{run_id}/cancel", json={"source_keys": ["alpha"]}).status_code == 202
            kwargs["checkpoint"]()

    queue.hook = hook
    assert claim_next_job(queue.engine, "chromium-smoke") == run_id
    process_job(run_id)
    assert not queue.active
    assert all(not browser.is_connected() for browser in browsers)
    assert all(context.pages == [] for context in contexts)
    assert ("collect", "bravo2") in queue.events and ("collect", "charlie2") in queue.events
    assert claim_next_job(queue.engine, "chromium-smoke") is None
    with queue.factory() as db:
        assert db.get(ScanRun, run_id).status == ("SUCCEEDED" if outcome == "success" else "PARTIAL")
        assert not set(db.scalars(select(ProjectScanRun.status))) & {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}

    deadline = monotonic() + 5
    remaining = process_resources()
    while set(remaining) - set(baseline) - {os.getpid()} and monotonic() < deadline:
        sleep(0.05)
        remaining = process_resources()
    assert not set(remaining) - set(baseline) - {os.getpid()}, "Browser/driver descendants leaked after job"
    report = {"platform": platform.system(), "machine": platform.machine(), "outcome": outcome,
              "status": "ok", "network": "local fixtures only", "installed_app": False,
              "browser_launches": len(browsers), "max_active_listing_browsers": 1,
              "peak_sampled_tree_rss_bytes": max(sum(sample.values()) for sample in samples),
              "max_sampled_tree_processes": max(len(sample) for sample in samples),
              "remaining_new_descendants": 0, "elapsed_seconds": round(monotonic() - started, 2)}
    report_dir = Path(os.environ.get("SEARCHCAR_QUEUE_SMOKE_REPORT_DIR", str(tmp_path)))
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"queue-{platform.system()}-{outcome}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "report": str(report_path)}))
