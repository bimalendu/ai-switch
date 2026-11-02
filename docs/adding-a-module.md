# Adding a module

A module is one AI feature. It owns a list of actions, each controlling one concrete thing. Status, disable, enable and revert come from the actions, so a typical module is data.

1. **Research first.** Add a section to `docs/catalog.md` with the evidence, source URLs and date. If you cannot confirm the mechanism, say so and mark the module `experimental=True`. A test fails if a module id is missing from the catalog.
2. **Pick actions** from `ai_switch/actions/`:
   - `SystemdMask(unit)`: mask a unit (works offline through `--root`).
   - `PolicyJsonMerge`: merge keys into a JSON file shared with others (Firefox).
   - `OwnedJsonFile`: a whole file ai-switch owns (Chromium managed policy).
   - `SnapDisable`, and for `--purge` only: `PackageRemove`, `FlatpakRemove`, `SnapRemove`.
3. **Write the module.** For a service-style module, add a `ServiceModule(...)` in `ai_switch/modules/services.py` with the distros it applies to, candidate unit names and package globs. Unit names are also discovered from the package database, so you rarely need to guess them. Otherwise subclass `Module` and return actions from `actions(ctx)`.
4. **Register it** in `ai_switch/modules/__init__.py`.
5. **Test it** with a fake root (`tests/unit/helpers.py: FakeRoot`) and a scripted `FakeRunner`. Always include: status before and after, disable, revert, and `snapshot()` equality to prove revert is exact.

Rules for actions:
- `present(ctx)` must be cheap and must not change anything.
- `to_off` / `to_on` raise `ActionError` before changing anything when they cannot proceed safely, and otherwise return the `before` record that `undo` needs.
- `undo(ctx, before)` must restore that state exactly. Never overwrite a file or unit that you did not create.
- Use `ctx.path()` for every path and `ctx.run()` for every command, so `--root` and tests work.
