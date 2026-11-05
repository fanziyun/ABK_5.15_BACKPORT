File payloads overlaid into the kernel tree by `scripts/stable_backport.sh`.

Most of this module is a Python graft registry (no file payloads). Two whole
files are overlaid because neither shape can be expressed as an anchor:

- `drivers/input/moon_kmsg.c` — the power-key kernel-log dump handler.  It is a
  new driver file (`abk_stable_backport_overlay_moon_kmsg()`), so the function
  also appends `obj-y += moon_kmsg.o` to `drivers/input/Makefile`.  A new file
  cannot be snapshotted, so its `.abk-orig` sidecar carries a sentinel that
  `scripts/abk_rollback.sh` turns into a delete; the Makefile gets a normal
  snapshot and restores byte-identically.

- `drivers/of/address.c` — the pre-rework devicetree `ranges`
  flags parser, carrying 5.15.216's unrelated `__of_get_dma_parent`
  `of_node_get()` refcount fix.  `abk_stable_backport_overlay_of_address()`
  copies it over the tree only when the target still carries the 5.15.213
  ranges rework (`flag_cells` / `"default-flags"` markers), so it is a no-op on
  any tree already in the pre-rework form.  It reverts the window-split
  that left the Qualcomm SM8550 PCIe WLAN endpoint's BAR0 unplaceable (dead
  `wlan0` on 5.15.216/lts).  The original is snapshotted to
  `drivers/of/address.c.abk-orig`; `scripts/abk_rollback.sh` restores it.
