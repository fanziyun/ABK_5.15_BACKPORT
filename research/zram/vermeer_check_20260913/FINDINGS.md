# zram writeback / proactive reclaim (cfr) 组件真机检查 — vermeer

日期 2026-09-13（无线 adb 192.168.31.194:41639，23113RKC6C/vermeer）。
内核 `5.15.216-android13-8-g5bfe2b8c1439`，模块 v0.6.2，开机 1 天。
对应 action.sh 报告：`zram writeback not built in` / `proactive reclaim not running` — **两条均属实，且都是配置/编译决定的预期状态，非故障**。

## 1. zram writeback：内核没编进去，组件按设计静默跳过

- `/proc/config.gz`：`# CONFIG_ZRAM_WRITEBACK is not set`（CONFIG_ZRAM=y、MULTI_COMP=y、DEF_COMP_LZ4KD=y 都在）。
- `/sys/block/zram0/` 下 `writeback`、`backing_dev`、`compressed_writeback`、`writeback_limit*`、`writeback_stats` **全部不存在** → `abk_zram_writeback_capable()` 门为假。
- 模块侧策略完好待发：`zram.writeback=auto`、`size_mb=1024`；`/data/per_boot/zram/` 存在（空，从未创建 backing 文件）；losetup 里 48 个 loop 全是 ROM 系统分区，无 ABK 残留。
- 模块日志无任何 writeback 行 —— incapable 路径静默返回，符合设计。
- **启用路径**：本仓库 config-lane 的 ROM tier 已有 `("ZRAM_WRITEBACK","y")`（`scripts/abk_stable_core.py` L2297），由 `ABK_515_DEFCONFIG_ROM=1` 触发，当前构建未开。重编内核加这个开关后，模块 `auto` 模式会在开机 zram 重写时自动建 loop backing 并 attach（Batch 11 锁已解决算法策略与 writeback 互斥问题）。

## 2. proactive reclaim (cfr)：opt-in 关闭，内核半截完全可用

- `tunables.conf: cfr.enable=0` → service.sh 根本不 spawn cfr 监督（`state/cfr.pid` 不存在，进程表只有 `--supervise-zram` pid 4896）。**"not running" 是配置态，不是挂死**。
- 工具已随模块装好：`bin/cached_freeze_reclaim.sh`（mksh 可跑，`--list` 只读诊断正常）。
- **内核 memcg_v1_reclaim graft 活性验证通过**：`echo 4096 > /dev/memcg/apps/memory.reclaim` 后
  `cfr_reclaim_attempts 0→1, requested 0→1, reclaimed 0→73` —— 用户态写入→内核回收→计数器全链路在 5.15.216 上真实工作。
- HyperOS memcg 树实测（v1 `/dev/memcg`，memory 控制器；v2 侧 `system/uid_*` 无 memory.reclaim，graft 只加 v1，符合设计）：
  - 顶层 `apps`(217MiB)、`freeze-app`(0)、`game`(0)、`protected-app`(378MiB)、`mimd`(**6.45GiB**)、`camera`、`protect_memcg_1/2/3` —— 全部带 `memory.reclaim` 子组文件（每级组都有）。
  - 真正的 per-UID 组在 **二级**：`mimd/uid_*`（uid_10299 1.67GB、uid_10297 1.39GB、critical0 653MB…）。
- **发现范围约束（重要）**：工具只扫 `$root` 与 `$root/apps` 一层。默认 `uid_*` 发现在 HyperOS 上必然落空（exit 1）；`cfr.group=freeze-app game` 能命中但当前 usage=0（ROM 只在冻结瞬间挂入）；**`--cgroup-root /dev/memcg/mimd` + 默认 uid_* 发现才能扫到真 per-UID 组**——但 mimd 里含前台应用组（uid_10085 是前台），全量扫会伤及活跃页，不建议直接开。
- v1 树上无 `freezer.state`（本 ROM 没挂 freezer 控制器）→ `cfr.freeze=1` 在此设备自动降级为「只回收不冻结」，工具设计如此。

## 3. 结论与建议

| 组件 | 现状 | 要做的事 |
|---|---|---|
| zram writeback | 内核 CONFIG 未编 → 组件跳过（正确） | 想要则重编内核 `ABK_515_DEFCONFIG_ROM=1`；模块零改动即可接管 |
| proactive reclaim | `cfr.enable=0` 关闭；内核/工具两侧均验证可用 | 想试：conf 设 `cfr.enable=1` + `cfr.group=freeze-app game`（保守，回收量小时近零风险）；激进路线需给工具加 `--cgroup-root /dev/memcg/mimd` 支持（现监督脚本写死 v2+/dev/memcg 两个根，mimd 二级组扫不到） |

当前 zram：swap 已用 6.2GiB / 16GiB，mem_used 1.94GB vs limit 3.71GB，重压缩扫描正常运行（pid 4896）。两组件均无需修复。

探测脚本：本目录 `probe_wb_cfr.sh` / `probe_cfr_groups.sh` / `probe_memcg_liveness.sh` / `probe_child_groups.sh`（设备上副本在 /data/local/tmp/，纯读+4KiB 良性写）。
