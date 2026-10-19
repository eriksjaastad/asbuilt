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

Then open the address it prints (`http://127.0.0.1:8765/` unless you pass `--port`). With no
path it reads `asbuilt/plan.json`. Save the file and the page redraws within a moment. If the
file is missing or broken, the page shows the reason instead of a chart, and the chart comes
back when you fix it. Stop with Ctrl-C. It only listens on your own machine.

The plan is [ELK JSON](https://www.eclipse.org/elk/documentation/tooldevelopers/graph/json.html):
boxes inside boxes, and arrows between them.

```json
{
  "id": "root",
  "children": [
    { "id": "read_plan", "labels": [{ "text": "read the plan" }] },
    { "id": "draw_chart", "labels": [{ "text": "draw the chart" }] }
  ],
  "edges": [{ "id": "e1", "sources": ["read_plan"], "targets": ["draw_chart"] }]
}
```

Open source, runs locally.
