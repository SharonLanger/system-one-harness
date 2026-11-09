# History fixture

Synthetic version-1 SOH records only. No user logs, prompts, paths or credentials were copied. The files are immutable reviewed test inputs; copy them into a temporary root before a test modifies anything.

There are 16 records in 9 hourly files across November 6, 7 and 9, 2026. November 8 is absent. November 7 has files at 00 and 02 but no 01 file. These gaps are normal idle periods. Never create missing files merely to make the fixture look contiguous.

`manifest.json` maps stable test IDs to file/line locations and expected descending file/append order. It is test metadata, not a log file. The actual records have no test-only envelope fields. The runner derives physical byte spans from those line locations independently of the production parser and maps returned source references back to the stable IDs.

| ID | Purpose |
|---|---|
| r01 | Null-session gateway start; details contain unvalidated session A. |
| r02 | Validated session A before hour boundary; nested Unicode/newline details. |
| r03 | Same invocation after hour boundary. |
| r04 | Session A starts a new invocation at exact midnight. |
| r05 | Different session, flow, component and WARNING; details-only search marker. |
| r06 | Session A after missing hour; uppercase message tests casefold. |
| r07 | Same timestamp as r06, appended later; different level/event. |
| r08 | PostCompact hook, session C, no flow. |
| r09 | Immediately before Past hour inclusive start at the default clock. |
| r10 | Exactly at Past hour start; all active matrix predicates match. |
| r11 | Same session/correlation/flow but extractor and WARNING. |
| r16 | Same session/correlation/flow/error/text but component type flow rather than block. ID order intentionally differs from chronological order. |
| r12 | Just before exclusive end; null session, details session A and HTML-looking text. |
| r13 | Exactly at exclusive end; literal string null session, null correlation/hook. |
| r14 | Standalone critical failure with all nullable fields null. |
| r15 | Future-dated stored record, unfamiliar level/event, Unicode casefold and regex-looking literal text. |

Most query cases use this fixture. Schema, fault and lifecycle variants are materialized in temporary roots by the [scenario runners](../../README.md). Do not rewrite this base fixture to make a failed expectation pass. Update the scenario documentation and affected cases together only for an intentional behavior change.
