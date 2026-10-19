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
browser and redraws it each time you save the file. An agent drafting the plan and the as-built
comparison come later.

## Use it

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). From a clone of this repo:

```
uv run asbuilt path/to/plan.json
```

`uv run asbuilt --help` says the rest: the defaults, what the page does when the file is saved or
broken, and the plan format with an example.
