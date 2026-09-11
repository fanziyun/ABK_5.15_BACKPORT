# zram 占用异常 — adb 诊断记录（vermeer / 5.15.215-FanZiyun）

日期：设备 uptime ~1h，SELinux Enforcing，KernelSU，模块 `abk_runtime_tunables` 已装。

## 结论
**ABK 内核模块组件无故障，工作正常。** zram「占用高、不释放」是 HyperOS ROM 自身
（`/system/etc/init/hw/init.rc:42` → `write /proc/sys/vm/swappiness 100`，memcg
apps/system=100、bg=140）+ 16GB zram（vendor `configure_zram_parameters`/mmd 创建）的
设计行为；监控工具显示的是 **swap 槽位用量（~1.2GB）**，而 zram 的**真实物理内存代价只有
~380MB**（mm_stat f3），上限 3.71GB（RAM 25%）远未触及。

## 关键数据
- `/proc/swaps`：zram0 16GB，Used ~1.19–1.25GB（槽位）
- `mm_stat`（字段序）：orig / compr / **mem_used_total** / **mem_limit** / mem_used_max /
  same_pages / pages_compacted / huge_pages / **f9（FanZiyun 内核自定义字段，与 huge_pages 同涨，非 ABK）**
- 开机 1h 累计：写 zram 1.35GB、读回 95MB（`/sys/block/zram0/stat`）→ SwapCached 仅 2MB，
  换出的都是冷页且几乎不被读回 → 「不释放」是 swap 的正常语义
- 4 分钟采样：槽位用量 1249024→1246208 KB（缓降，无泄漏增长）
- 换出 +172MB 的同时 mem_used 398.5→396.8MB，pages_compacted 0→16576：
  ABK 重压缩+整理路径在真机生效
- 压缩比 orig/mem_used ≈ 3.6–4.0:1；huge_pages ~6.7k（27MB，未压缩页，正常）

## ABK 组件状态（全部正常）
- 内核参数锁：`abk_comp_algo=lz4kd` `abk_recomp_algo=zstd` `abk_lock_algo=Y`；
  `comp_algorithm=[lz4kd]` `recomp_algorithm=[zstd]`（ROM post_boot 试图写 lz4 被锁为 no-op，符合设计）
- 监督进程：pid 4317 `service.sh --supervise-zram` + 内核线程 `[zram_recompd]` 存活
- 模块日志 `state/abk_runtime_tunables.log`：03:05 策略生效 → 每 60s 复检 mem_limit
  （=3983572992，正好 MemTotal 25%）→ 每 30min async 重压缩扫描，03:42 完成一次，无报错
  （仅启动瞬间 mem_limit 512MB 页对齐 1 字节良性 WARN）
- writeback：内核无 `CONFIG_ZRAM_WRITEBACK`（无 backing_dev 节点），组件按设计跳过
- 模块**从未做过 takeover/rewrite**（日志无 `rewrite:` 行）——16GB zram 是 ROM 建的，组件只继承

## 杂项说明
1. `mem_limit`/`mem_used_max`/`idle`/`recompress*` 是 0200 只写节点 → root 也读不了，正常加固。
2. 实时 dmesg 0 条 zram：设备已开机 1h，`log_buf_len=2M` 环形缓冲只剩 ~20 分钟；
   启动完整日志在 `/data/adb/ksu/log/dmesg.log`。
3. swap 占用大户（VmSwap）：dboxed 124MB、camera 121MB、微信 73MB、QQ 64MB… 分布正常。

## 若要压低 zram 用量（可选）
- 在 `tunables.conf` 设 `vm.swappiness=60`（模块 post-fs-data 会覆盖 ROM 的 100，需重启）
  ——代价是后台驻留/秒开可能变差，HyperOS 本意即「内存扩展」。
- 监控改看物理代价口径（mm_stat f3）而非槽位用量。
- 手动触发一次整理：`compact` / `recompress` 节点（模块 action.sh 有入口）。
