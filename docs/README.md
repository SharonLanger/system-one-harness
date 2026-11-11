# System One Harness guides

Start with the [project setup](../README.md#requirements), then choose the task
you want to perform. These guides describe current behavior; proposed concurrent
flow routing in the root README is not an implemented capability.

| Task | Guide |
| --- | --- |
| Install the CLI and connect, pause or disable Claude hooks | [Plugin setup](../plugins/README.md) |
| Check a real Laya model on a Mac | [Real-model setup](real-laya-mac.md) |
| Run one small component example | [Seven sanity examples](../examples/README.md) |
| Understand the maintained prompt-enrichment flow | [Method context](flows/method-context.md) |
| Configure a hook manually or inspect input/output | [Hook protocol and usage](hook-usage.md) |
| Search local logs and inspect JSON | [Log viewer](log-viewer/README.md) |
| Understand log fields, sessions and event types | [Log data model](log-viewer/DATA_MODEL.md) |
| Integrate another reader or change viewer behavior | [Log viewer API](log-viewer/API.md) |
| Understand repeat suppression and compaction | [Session storage](session-state.md) |
| Author model settings and acceptance rules | [Configuration conventions](configuration-conventions.md) |
| Reject oversized model input before inference | [Input budgets](input-budgets.md) |

Component contracts live beside their code: [block runtime](../src/block_runtime/README.md),
[question blocks](../src/building_blocks/questions/), [defaults](../src/defaults/README.md),
and [TypeScript utilities](../typescript-helper/README.md). Maintainers can use the
[test guide](../tests/README.md) and [contribution guide](../CONTRIBUTING.md).
