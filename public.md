# public.md — injectable child ids

Active child ids for `set:<repo>#<child>;stage` injection:

- `stable_backport_core`
- `stable_perf_backport`
- `stable_display_fix`

Stage: `after_patch` (recommended and default). `before_build` is accepted
and is a no-op.

Migration notes: this module has no legacy ids. Composition contract and the
canonical ordering with `ABK_F2FS_FIX_MODULE` and `ABK_ABI_PATCH_SUITE` are
documented in `docs/porting_policy.md`.
