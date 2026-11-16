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
code sits under the chart.

The Python files in a folder are charted the same way, including subfolders. Each subfolder is a
box inside its parent. The folder you named keeps its plain name (`shop`). A subfolder's id
ends with a slash (`sub/`), so a plan can name it ahead of the code, and a subfolder called
shop is not the same box as the folder shop. A file in the folder is `load.py:load`; a nested
file is `sub/util.py:name`. Directories named tests, __pycache__, venv, env, node_modules,
build, dist, or site-packages, and hidden directories, are skipped, and the command names the
ones it skipped.

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
cannot add boxes. A function or class nested inside another is not its own box yet, and neither
is a JavaScript file.
