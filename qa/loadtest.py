"""Bounded local load test (QA tooling). Runs ONLY against a loopback server started by qa/local_server.py.

Safety limits, enforced before anything is sent:
- target must be 127.0.0.1 / localhost (the session file written by local_server.py);
- concurrency <= 32, requests per scenario <= 2000, wall time per scenario <= 120 s, request timeout 15 s;
- a scenario stops early when more than 20% of its first 50+ responses are errors.
Writes are limited to attrition predictions (rows in the disposable database) and one login per request in the
login scenario. Results (latency percentiles, throughput, status codes, server CPU and memory when a PID is given)
are written as JSON; nothing is inferred beyond what was measured.

usage: python qa/loadtest.py --out results.json [--concurrency 8] [--requests 300] [--server-pid N] [--only a,b]
"""

import argparse
import asyncio
import ctypes
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_CONCURRENCY, MAX_REQUESTS, MAX_SECONDS, TIMEOUT = 32, 2000, 120, 15.0


# --- Server process observation (Windows: GetProcessTimes / GetProcessMemoryInfo via ctypes) ---------------------
class ProcessProbe:
    def __init__(self, pid: int | None) -> None:
        self.handle = None
        if pid and sys.platform == "win32":
            self.handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED | VM_READ

    def sample(self) -> dict | None:
        if not self.handle:
            return None

        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.psapi.GetProcessMemoryInfo(self.handle, ctypes.byref(counters), counters.cb)
        times = [ctypes.c_ulonglong() for _ in range(4)]
        ctypes.windll.kernel32.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times])
        cpu_seconds = (times[2].value + times[3].value) / 1e7  # kernel + user, 100 ns units
        return {"t": time.perf_counter(), "cpu_s": cpu_seconds, "rss_mb": counters.WorkingSetSize / 2**20,
                "peak_rss_mb": counters.PeakWorkingSetSize / 2**20}


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def run_scenario(name, make_request, total, concurrency, probe) -> dict:
    latencies, statuses, errors = [], {}, 0
    sent = 0
    stop = asyncio.Event()
    start, before = time.perf_counter(), probe.sample()

    async def worker() -> None:
        nonlocal sent, errors
        while not stop.is_set():
            if sent >= total or time.perf_counter() - start > MAX_SECONDS:
                return
            sent += 1
            t0 = time.perf_counter()
            try:
                status = await make_request()
            except httpx.HTTPError as exc:
                status = type(exc).__name__
            latencies.append((time.perf_counter() - t0) * 1000)
            statuses[str(status)] = statuses.get(str(status), 0) + 1
            if not (isinstance(status, int) and status < 400):
                errors += 1
            done = len(latencies)
            if done >= 50 and errors / done > 0.2:
                stop.set()  # error budget exceeded: stop this scenario

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    wall = time.perf_counter() - start
    after = probe.sample()
    result = {
        "scenario": name, "requests": len(latencies), "concurrency": concurrency, "wall_seconds": round(wall, 2),
        "throughput_rps": round(len(latencies) / wall, 1) if wall else None, "status_counts": statuses,
        "error_rate": round(errors / len(latencies), 4) if latencies else None, "stopped_early": stop.is_set(),
        "latency_ms": {"p50": round(percentile(latencies, 50), 1), "p90": round(percentile(latencies, 90), 1),
                       "p95": round(percentile(latencies, 95), 1), "p99": round(percentile(latencies, 99), 1),
                       "max": round(max(latencies), 1), "mean": round(statistics.fmean(latencies), 1)} if latencies else None,
    }
    if before and after:
        result["server"] = {"cpu_seconds": round(after["cpu_s"] - before["cpu_s"], 2),
                            "avg_cpu_percent_of_one_core": round(100 * (after["cpu_s"] - before["cpu_s"]) / wall, 1),
                            "rss_mb_after": round(after["rss_mb"], 1), "peak_rss_mb": round(after["peak_rss_mb"], 1)}
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=str(Path(tempfile.gettempdir()) / "wfai_qa" / "session.json"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--login-requests", type=int, default=60)
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= MAX_CONCURRENCY or not 1 <= args.requests <= MAX_REQUESTS:
        raise SystemExit(f"limits: concurrency 1..{MAX_CONCURRENCY}, requests 1..{MAX_REQUESTS}")

    session = json.loads(Path(args.session).read_text(encoding="utf-8"))
    base = session["base_url"]
    if urlparse(base).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("refusing a non-loopback target")
    probe = ProcessProbe(args.server_pid)
    rng = random.Random(1)

    limits = httpx.Limits(max_connections=args.concurrency + 4, max_keepalive_connections=args.concurrency + 4)
    async with httpx.AsyncClient(base_url=base + "/api", timeout=TIMEOUT, limits=limits) as client:
        async def token(role):
            r = await client.post("/auth/login", json={"email": session["accounts"][role], "password": session["password"]})
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        hr, manager, employee = await token("HR"), await token("MANAGER"), await token("EMPLOYEE")
        admin = await token("ADMIN")
        bulk = session["bulk_employee_ids"] or list(session["employees"].values())
        model = session.get("model_version")
        period = (await client.get("/payroll/periods", headers=hr)).json()["items"]
        if not period:
            today = time.localtime()
            created = await client.post("/payroll/periods", headers=hr, json={"year": today.tm_year, "month": today.tm_mon})
            created.raise_for_status()
            period_id = created.json()["id"]
            (await client.post(f"/payroll/periods/{period_id}/calculate", headers=hr, json={}, timeout=120)).raise_for_status()
        else:
            period_id = period[0]["id"]

        async def get(path, headers, **params):
            return (await client.get(path, headers=headers, params=params)).status_code

        scenarios = {
            "auth_login (bcrypt 12 rounds)": (lambda: client.post("/auth/login", json={
                "email": session["accounts"]["EMPLOYEE"], "password": session["password"]}), args.login_requests),
            "auth_me": (lambda: get("/auth/me", employee), args.requests),
            "employees_list_hr": (lambda: get("/employees", hr, page=rng.randint(1, max(1, len(bulk) // 50)), page_size=50), args.requests),
            "employees_search_manager": (lambda: get("/employees/search", manager, q="Engineer", page_size=20), args.requests),
            "attendance_list_hr": (lambda: get("/attendance", hr, page_size=50, page=rng.randint(1, 20)), args.requests),
            "attendance_summary_hr": (lambda: get("/attendance/summary", hr), args.requests),
            "leave_requests_manager": (lambda: get("/leave/requests", manager, page_size=50), args.requests),
            "leave_balances_employee": (lambda: get("/leave/balances/me", employee), args.requests),
            "payroll_records_hr": (lambda: get(f"/payroll/periods/{period_id}/records", hr, page_size=50,
                                               page=rng.randint(1, max(1, len(bulk) // 50))), args.requests),
            "payslips_employee": (lambda: get("/payroll/me", employee), args.requests),
            "datasets_list_admin": (lambda: get("/datasets", admin), args.requests),
            "ai_model_status_hr": (lambda: get("/ai/attrition/model-status", hr), args.requests),
            "global_search_hr": (lambda: get("/search", hr, q="Synthetic"), args.requests),
        }
        if model:
            async def predict():
                return (await client.post(f"/ai/attrition/predict/{rng.choice(bulk)}", headers=hr)).status_code
            scenarios["ai_predict_hr (writes a prediction)"] = (predict, args.requests)

        async def as_status(factory):
            response = factory()
            response = await response if asyncio.iscoroutine(response) else response
            return response if isinstance(response, int) else response.status_code

        only = {s.strip() for s in args.only.split(",") if s.strip()}
        results = []
        for name, (factory, total) in scenarios.items():
            if only and not any(name.startswith(o) for o in only):
                continue
            result = await run_scenario(name, lambda f=factory: as_status(f), total, args.concurrency, probe)
            results.append(result)
            lat = result["latency_ms"] or {}
            print(f"{name:40s} n={result['requests']:4d} rps={result['throughput_rps']:7} p50={lat.get('p50')}ms "
                  f"p95={lat.get('p95')}ms p99={lat.get('p99')}ms errors={result['error_rate']}", flush=True)

    Path(args.out).write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "database": session.get("database"),
        "machine": {"platform": platform.platform(), "processor": platform.processor(),
                    "logical_cpus": __import__("os").cpu_count()},
        "limits": {"max_concurrency": MAX_CONCURRENCY, "max_requests": MAX_REQUESTS, "max_seconds": MAX_SECONDS,
                   "timeout_s": TIMEOUT, "stop_if_error_rate_above": 0.2},
        "parameters": {"concurrency": args.concurrency, "requests": args.requests, "login_requests": args.login_requests,
                       "bulk_employees": len(session["bulk_employee_ids"])},
        "results": results,
        "caveat": "Single local machine, one uvicorn worker, client and server on the same host, synthetic data. Not a "
                  "capacity estimate for production.",
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
