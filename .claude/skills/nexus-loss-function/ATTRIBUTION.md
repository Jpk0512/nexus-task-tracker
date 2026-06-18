# Attribution

The `nexus-loss-function` skill is an **adaptation** of the MIT-licensed
**`lfd-design`** skill from
[**elvisun/loss-function-development**](https://github.com/elvisun/loss-function-development).

- **Upstream project:** elvisun/loss-function-development
- **Upstream path:** `skills/lfd-design/` (SKILL.md + `references/cheat-museum.md`,
  `references/goal-template.md`, `references/log-template.md`)
- **License:** MIT (full notice below)
- **What we changed:** the four-part loss-function model (target / constraints /
  instruments / forced-entropy), the dev/holdout anti-gaming split, the
  red-team-your-own-draft pass, and patch mode are PRESERVED. They have been
  MAPPED onto Nexus primitives: instruments → the verification gates
  (`rtk tsc`/`rtk lint`, `uv run ruff check`/`uv run pytest`,
  `tools/build_snapshot.sh --check`); the separate judge → **Lens**; the
  iteration log → the **lessons table** + the **feedback system**; the
  forced-entropy stall rule → the **REVISE stall-escalation**. Nexus HARD RULES
  (DEC-002 main-only, DEC-005 no-deferral) and the runaway-guard requirements
  (DEC-024) were layered on top. Adopted per Nexus decision **DEC-025**.

This is an adaptation; **original authorship belongs to the upstream project.**
We do not claim original authorship of the loss-function-development design.

---

## Upstream MIT License

```
MIT License

Copyright (c) 2026 Elvis Sun

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
