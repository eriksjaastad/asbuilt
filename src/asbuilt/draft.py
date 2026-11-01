"""The plan as the model drafts it: untrusted, taken only if prepare.js takes it."""

import json
import subprocess
from pathlib import Path

from asbuilt.state import PlanError

PAGE = f"""import {{ prepare }} from {json.dumps((Path(__file__).parent / "page/prepare.js").as_uri())};
process.stdin.setEncoding("utf8"); let text = ""; for await (const part of process.stdin) text += part;
let says = null; try {{ prepare(JSON.parse(text)); }} catch (e) {{ says = String(e?.message ?? e); }}
process.stdout.write(JSON.stringify(says));"""


def page_takes(out, name, node=("node",), timeout=60):
    """None when the page's prepare.js takes `out` (a drafted plan's text), else a PlanError naming `name`
    with the page's own refusal, or why node could not check it."""
    try:
        run = [*node, "--input-type=module", "-e", PAGE]
        done = subprocess.run(run, input=out, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        why = "node was not found: install Node.js"
    except subprocess.TimeoutExpired:
        why = f"node gave no answer in {timeout} s"
    except OSError as e:
        why = f"node cannot be started: {e.strerror or type(e).__name__}"
    else:
        try:
            says = 0 if done.returncode else json.loads(done.stdout)
        except ValueError:
            says = 0
        if says is None:
            return
        if isinstance(says, str):
            raise PlanError(f"{name}: {says}")
        last = (done.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
        why = f"node stopped, exit {done.returncode}: {last[:200] or 'no message'}"
    raise PlanError(f"{name} cannot be checked: {why}.")
