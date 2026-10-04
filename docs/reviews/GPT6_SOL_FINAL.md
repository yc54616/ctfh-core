## BLOCKERS (must change before implementation)

- The v0 performance gate does not require a matched direct baseline. “[Same native agent](</home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:658>)” can still mean different tools, environment, context, permissions, search access, deadlines, or evaluation. Those differences would make the claimed *wrapper overhead* uninterpretable. Specify that both arms use the same task instances and effective runtime conditions, with CTFH supervision as the measured difference, and record both configurations.

## NON-BLOCKING (measure during v0)

- Use the required fault tests to confirm that native agent processes stop on cancellation and that no old target-epoch action survives a reset. Track cleanup delay and leaked environments.
- Measure setup, verification, and total elapsed time alongside solve rate. The v0 scope is thin enough; its external-operation fixture can remain minimal.

## VERDICT

**NOT READY** until the A/B gate defines matched conditions.