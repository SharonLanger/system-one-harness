# README assets

These SVGs illustrate the main README. This folder holds their editable animation timings and maintenance instructions.

| Asset | What it shows |
| --- | --- |
| `build-with-blocks.svg` | Question blocks and tools compose into a Python flow connected through a Claude Code plugin. This illustrates composition, not parallel runtime execution. |
| `event-routing.svg` | Planned routing: A only, B only, and concurrent A+B. Concurrent routing is not implemented. |
| `method-context.svg` | The current proof of concept, including successful enrichment and skipped paths. Details: [method-context guide](../flows/method-context.md). |

All timings are illustrative. The SVGs use CSS animation without JavaScript or external resources and include a reduced-motion fallback.

## Editing

Edit the SVG's static layout and its matching `.spec.json` timing file. Obtain [animdiagram](https://github.com/praveenvijayan/animdiagram) separately, then regenerate and validate:

```sh
python /path/to/animdiagram/scripts/timeline.py docs/readme-assets/build-with-blocks.spec.json --inject docs/readme-assets/build-with-blocks.svg
python /path/to/animdiagram/scripts/check.py docs/readme-assets/build-with-blocks.svg
```

Replace `build-with-blocks` with the other asset's name as needed. Preview the result as an embedded image at desktop and phone widths, including reduced motion. The method-context walkthrough intentionally exceeds the validator's small-diagram duration and packet budgets to show all five scenarios.

## Attribution

Authored using animdiagram's MIT-licensed template, timeline compiler, and validator (skill version 1.1). Its license is retained in [animdiagram-LICENSE.txt](animdiagram-LICENSE.txt). These are authoring tools; readers and SOH runtime users need no installation.
