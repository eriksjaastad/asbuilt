# As-Built

Draw the plan for a program before it is built, then compare it with what was actually built.

In construction, the *as-built drawings* record what really got built, kept next to the
original plans. As-Built does the same for code:

1. **Plan.** An agent drafts a flowchart of the program before any code exists. You reshape
   it by talking to the agent in your terminal, and the page in your browser redraws as the
   chart changes.
2. **As built.** Once the code exists, the chart is rebuilt from the code itself and compared
   with the plan, with a note on why each part changed.
3. **Walkthrough (later version).** Follow the path data takes through the program, with the
   lines it passes through highlighted.

Status: the first view works. `asbuilt` draws a plan file as a hand-inked flowchart in your
browser and redraws it each time you save the file, and it can draw a planned chart and a built one
as one chart, marked. It can also chart one Python file, or the Python files in one folder, and open a function's code
beside the chart.
JavaScript and TypeScript have a separate first-slice reader, described below.
An agent drafting the plan and the note on why each part changed come later.

## Use it

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). You don't need Node: the page's
libraries are included, and package.json is only for running its tests.

```
git clone https://github.com/eriksjaastad/asbuilt
cd asbuilt
uv run asbuilt --help
```

The help ends with an example plan. Save it as `plan.json`, then run:

```
uv run asbuilt plan.json
```

Open the address it prints. Each time you save `plan.json` the page redraws. `--help` says the
rest: the defaults, what the page does when the file is broken, and the plan format.

## Compare a plan with what was built

```
uv run python -m asbuilt.compare planned.json built.json compared.json
uv run asbuilt compared.json
```

The chart is the built one, with each box and arrow that differs from the plan tagged "new",
"changed" (dotted) or "dropped" (faded and dashed, where the plan had it), and a legend under it.

A box you write before the code exists can be named `shop.py:load`: the file, then the function
or class. The built chart uses that same id with the line number on the end (`shop.py:load:12`),
which is how the code panel finds the lines. Compare matches the two, and the line number is
not a difference.

## Read JavaScript or TypeScript

```
uv run python -m asbuilt.javascript path/to/project built.json
uv run asbuilt built.json
```

This first slice reads `.js`, `.jsx`, `.ts` and `.tsx`. Each file is a box, labelled with its
relative path, and its top-level functions, classes and function-valued variables (including
component functions) are boxes inside it. Clicking one opens its source using the same
line-number IDs and code panel as Python. It never runs the project or calls a model.

Arrows labelled “imports” connect files for relative ES-module imports, including TypeScript
ESM `.js`/`.jsx` specifiers resolved to `.ts`/`.tsx` when unambiguous. Call arrows connect
unique local definitions, or exported definitions proved by named/default imports. An
extensionless import resolves only when exactly one matching source file exists. Shadowed,
reassigned or ambiguous names do not get call arrows. OUT must end in `.json`.
The pinned grammars can reject some valid syntax, including some TypeScript arrow functions
with `<` inside an object literal. In a folder chart, unreadable or unparseable files are
skipped and listed on stderr so the rest of the project still charts; if no parseable files
remain, or when charting a single broken file, the command reports an error and leaves the
previous output untouched.

Tests (`test/`, `tests/`, `__tests__/`, `*.test.*`, `*.spec.*`, `test_*`, `*_test`), dependency
and generated folders, hidden paths, symlinks and `.d.ts` declarations are skipped. External
packages are not charted. Files remain directly inside the project box in this first slice.
Directly naming a test/dependency folder or one of its files is refused too. An unrelated
ancestor above the project root, such as `/build` in `/build/repo`, does not exclude the project.
For a single file, the nearest Git checkout or otherwise the outermost enclosing `package.json`
outside `node_modules` defines that root. Without either, folder checks include all ancestors; chart
the project folder to set an explicit boundary.
Nested callbacks and methods, JSX render relationships, CommonJS, index resolution,
re-exports, configured aliases, work badges and mixed Python/JS charts are not covered yet.
JSX component functions are included, but a JSX use is not presented as a direct call.

## Read one Python file

```
uv run python -m asbuilt.built t.py built.json
uv run asbuilt built.json
```

The file is the outer box. Each top-level function and class is a box inside it, and an arrow
joins a box to another top-level name it calls. A parameter or a local of that name is not the
call, and a name defined twice points at the last definition. Click a
function or a class and its code opens beside the chart. The chart stays. The two stay within a laptop
width: the code takes a fixed column and the chart scales down to fit. On a narrow window the
code sits under the chart. “Only what this reaches” keeps that box, every box an arrow leads to
from it, and the file or folder around each of those, and hides the rest. Arrows are followed
outward. “Whole chart” brings the full chart back. Choosing a file or a folder starts from every
box inside it, because the arrows leave those boxes, not the folder.

A box can carry a small badge for work its own code proves, never for a box it calls.
The name is followed back to its import, and a parameter or a local of that name does not count.
`calls out` is a request, urllib.request, a socket, subprocess, or os.system.
`model` is the openai or anthropic SDK, including a client built once at the top (`client = OpenAI()`). `env` is an environment read, `input` is input(),
and `writes` is a file opened or written. A change in later behavior cannot be read from the
code, so it is not a badge. The badge sits on the box. It does not change the box's id or its arrows.
A box that calls out, reads the environment, or calls input() also gets two sentences, Plain and Expert.
Each one names a fact the parser can see in that box: the call, an environment name or a prompt when it is written there, and a URL's host. A network call is a request; it is described as sending data only when the call passes a body, and it is not called outside when the host is local. A timeout is mentioned only when that call shows one, or when leaving it out is itself the proof. A box with no such fact gets no sentence. Building the chart writes these sentences, and a model does not rewrite them.
A box can also get up to three short sentences in everyday words, from what its own code proves: what goes in, what it reaches, what comes back, and how it stops. They are shown before the security lines. Those lines stay as written, both Plain and Expert. A model does not write the everyday sentences, and a docstring is not copied into them.
The buttons above the chart, next to “Only what this reaches” and “Whole chart”, show only the boxes that carry a chosen badge, and the arrows between those boxes. Several badges means a box with any of them. Each button shows how many boxes in the current view prove that mark. The line beside them says how many boxes and arrows are hidden. When no box carries the mark, the chart says so once, in place of the drawing. All puts the full chart back. With “Only what this reaches” on as well, a box has to be in that pipeline and carry a chosen badge.

The Python files in a folder are charted the same way, including subfolders. Each subfolder is a
box inside its parent. The folder you named keeps its plain name (`shop`). The built chart writes a subfolder as
`sub/`, and a plan may name it `sub` or `sub/`. When the subfolder has the same name as the
folder, the plan uses the slash (`mysite/`) so the two boxes stay different. A file in the
folder is `load.py:load`; a nested file is `sub/util.py:name`. An arrow crosses files when an
import proves the call: `from other import load` then `load()`, `import other` then
`other.load()`, and `from pkg import mod` or `from . import mod` then `mod.f()`. A name keeps
that arrow only when one import binds it (importing that same module again does not remove it;
`import *` does). On the target side, the called name must be bound once in its file, as that
definition (`@overload` stubs do not count as a second definition). For `from pkg import mod`
then `mod.f()`, a name bound in `pkg/__init__.py` (or shadowed by `import *` there) is treated
as that object rather than the submodule, unless `pkg/__init__.py` binds it to the submodule
itself. Anything else is not an arrow. Directories named tests, venv,
env, node_modules, build, dist, or site-packages are named when skipped. __pycache__ and
hidden directories are skipped quietly. Test files (`test_*.py`, `*_test.py`) are left out of a
folder chart and refused when named directly, as JavaScript and TypeScript test files are.

```
uv run python -m asbuilt.built . built.json
uv run asbuilt built.json
```

When a function's only step is to call one other function and return that result, the same line
appears every time: "This function calls one other function and returns whatever that returns."

A second kind of note can be written by a model. It is labeled "model", and it is separate from
the line above. Nothing is sent unless you opt in. Either setting sends each function and each class.

With a Claude subscription, `ASBUILT_MODEL=claude` uses the Claude program already signed in on
this machine. There is no per-call charge.

```
ASBUILT_MODEL=claude uv run python -m asbuilt.notes built.json noted.json
```

`ASBUILT_MODEL=1` sends each function and class to OpenRouter and may cost money. The key is
the environment variable `OPENROUTER_API_KEY` (As-Built never stores it). The model is
`x-ai/grok-4.7`.

```
ASBUILT_MODEL=1 uv run python -m asbuilt.notes built.json noted.json
uv run asbuilt noted.json
```

Any other value, or leaving it unset, does not call out and adds no model notes. A reply that
is not the notes asked for is thrown away and the output file is left unchanged. The model
cannot add boxes. A function or class nested inside another is not its own box yet.
