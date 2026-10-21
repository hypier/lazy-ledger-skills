"""Run a single-file converter with no ledger access, network or child processes."""
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 512 * 1024


def available():
    return sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists()


def execute(script, payload, *, timeout=5):
    if not available():
        raise ValueError("No supported OS sandbox; this host cannot validate or run extensions")
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError("Extension input exceeds 512 KiB")
    with tempfile.TemporaryDirectory(prefix="ledger-extension-") as folder:
        work = Path(folder).resolve()
        program = work / "main.py"
        program.write_text(script, encoding="utf-8")
        # Only runtime/system libraries and this disposable work directory are readable.
        roots = {"/usr", "/System", "/Library/Apple", str(Path(sys.base_prefix).resolve()), str(work)}
        reads = " ".join('(subpath ' + json.dumps(p) + ')' for p in sorted(roots))
        policy = '(version 1)(deny default)(allow process-exec)(allow process-info*)(allow sysctl-read)' + '(allow file-read* ' + reads + ' (literal "/") (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))' + '(allow file-write* (subpath ' + json.dumps(str(work)) + ') (literal "/dev/null"))'
        def limits():
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        with (work / "stdout").open("wb") as out, (work / "stderr").open("wb") as err:
            process = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", policy, sys.executable, "-I", "-S", str(program)],
                                       stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=work,
                                       env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                                       preexec_fn=limits, start_new_session=True)
            try:
                process.communicate(encoded, timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ValueError("Extension exceeded execution time") from None
        if process.returncode:
            error = (work / "stderr").read_bytes()[:2000].decode("utf-8", errors="replace")
            if "sandbox_apply" in error:
                raise ValueError("OS sandbox could not start; execution remains disabled")
            raise ValueError("Extension failed inside the sandbox (exit " + str(process.returncode) + "): " + error)
        output = (work / "stdout").read_bytes()
        if len(output) >= MAX_BYTES:
            raise ValueError("Extension output limit exceeded")
        try:
            return json.loads(output)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Extension must emit exactly one JSON document") from exc
