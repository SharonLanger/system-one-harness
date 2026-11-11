# Selected-profile fixtures

These frozen JSON definitions are independent expectations for the requested-action
and method-selection production defaults. Tests compare runtime questions, rules
and settings against these files without importing any experiment runner.

The definitions were copied byte-for-byte from the campaign suites at commit
`a66982b276aa28e2080aa0207fff6006f19139b3`. They are authored test inputs, not
runtime configuration and not evidence of live-model accuracy. Keep them when
changing research tooling. When deliberately adopting a new default profile,
review both the production change and these independent expectations; do not
update a fixture merely to silence an unexpected regression.
