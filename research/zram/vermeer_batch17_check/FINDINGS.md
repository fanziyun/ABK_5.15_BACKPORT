# Batch 17 真机验证 — zram writeback batching + compressed writeback（vermeer）

日期 2026-09-14（无线 adb，23113RKC6C/vermeer，HyperOS）。
内核 `5.15.216-android13-8-g5bfe2b8c1439`（= 本地 ROM tier 构建，见 `build-meta.txt` 的
`template_common_commit=5bfe2b8c1439`），运行模块 `abk_runtime_tunables v0.6.2`。

结论一句话：**三个节点/语义全部在真机成立，batching 的收益是 17× 墙钟 / 6.5× CPU**；
**compressed writeback 是「写侧省 CPU、读侧花 CPU」的搬移，默认关闭是对的**；
另外发现一个与本批无关、但会**让整台设备上的 zram writeback 在 Enforcing 下必然失败**的
ROM sepolicy 缺口（§4）。

## 1. 方法（为什么不用 zram0）

zram0 是**在用的 swap 设备**（16GiB，ROM 与模块共同管理），在上面做大面积写回既不可控也会
扰动系统。因此全部实验在 **hot_add 出来的独立 zram1** 上做：512MiB / 128MiB 稀疏文件 →
`losetup` → `backing_dev`，先 `disksize` 再 `dd` 灌数据，每例结束 `reset` + `hot_remove`
+ `losetup -d`，全程 zram0 的 `bd_stat` 保持 `0 0 0`（未被本实验触碰）。

数据：`cat /system/lib64/*.so | head -c <N>` —— 真实 ELF，**页页不同且可压缩**
（64MiB 那轮 `orig 64M / compr 30M`），避免 zram 的 same-page 去重把样本吃掉。
每例都校验 `md5sum`：灌进去的、写回前读回的、写回后读回的三个值必须相同。

脚本：本目录 `b17_*.sh`（mksh，`sh -n` 通过），原始输出在 `raw/`。

## 2. 能力与节点语义（Enforcing 下即可验证，不涉 I/O）

| 检查 | 结果 |
|---|---|
| `/proc/config.gz` | `CONFIG_ZRAM_WRITEBACK=y`、`ZRAM_MULTI_COMP=y`、`ZRAM_TRACK_ENTRY_ACTIME=y`、`ZRAM_DEF_COMP_LZ4KD=y` |
| zram0 节点 | `backing_dev=/dev/block/loop49`（ROM 的 mmd 挂的）、`writeback_batch_size=32`、`compressed_writeback=0`、`writeback_limit=2752512`+enable=1 |
| `writeback_batch_size` 边界 | `0`→EINVAL（保持 32）；`1`/`32`/`256` 接受；`257`/`1000000`→**夹取到 256**（`ZRAM_WB_BATCH_SIZE_MAX`）；`abc`/`-1`→EINVAL |
| `compressed_writeback` 边界 | 只能**在 `disksize` 之前**写：已 init 的设备一律 `-EBUSY`（上游形态，见 `zram_drv.c:3006`）——模块的 `abk_zram_attach_writeback()` 顺序（backing_dev → compressed → limit → disksize）正因此是对的 |
| Batch 12 遗留待验 | `/sys/module/zram/parameters/abk_{comp,lock,recomp}_algo` 存在、**0444**、读出 `lz4kd`/`Y`；`echo lzo-rle > comp_algorithm` 被拒且 dmesg 有 `comp_algorithm is locked, ignoring a write of 'lzo-rle'`；`backing_dev` 已挂且 `writeback_limit` 生效 —— 三条**全部成立** |

## 3. A/B 实测

### 3.1 batching（32MiB，data=`/system/lib64` 拼接；两次重复）

| batch | wall | voluntary ctx switches | 写回任务 CPU（jiffy） |
|---|---|---|---|
| 1 | 1560 / 1640 ms | 7673 / 7575 | 63 / 62 |
| 32 | 220 / 180 ms | 2159 / 2051 | 10 / 10 |
| 256 | 220 ms | 439 | 7 |

`CONFIG_HZ=250` → 1 jiffy = 4 ms。**batch 1→32：墙钟 7.3–9.1×、上下文切换 3.6×、CPU 6.2×**；
batch 256 把上下文切换再压到 1/17，但墙钟不再改善（瓶颈已转到 loop/闪存）。

64MiB、3 次重复（`raw/cpu-64m-repeat.txt`），每例 `bd=[15379 0 15379]` 完全一致：

| batch | wall（均值） | ctx switches | 写回 CPU（均值） |
|---|---|---|---|
| 1 | 4457 ms | 15186 | 90.7 j = 363 ms |
| 32 | 263 ms | 1246 | 14.0 j = 56 ms |

→ **17× 墙钟 / 12× 上下文切换 / 6.5× CPU**。这就是「每页一次 `submit_bio_wait` → 一次
批内流水线」的直接体现，也是本批存在的理由。

### 3.2 compressed writeback：写侧省、读侧花（64MiB，3 次重复，batch=32）

| 阶段 | cwb=0 | cwb=1 |
|---|---|---|
| 写回：任务自身 CPU | 25/20/19 j（中位 20） | 18/17/14 j（中位 17） |
| 写回：墙钟 | 440/390/360 ms | 360/370/310 ms |
| 写回：系统级 CPU（8 核 jiffy） | 220/104/114 | 182/120/109 |
| 读回：任务自身 CPU | 1/1/1 j | 0/2/1 j（**解压不在读者上下文**） |
| 读回：系统级 CPU | 68/51/67 j | 76/99/106 j |
| 读回：墙钟 | 290/180/280 ms | 290/330/270 ms |

- 写侧：`cwb=1` 的**任务自身 CPU 稳定低 20–25%**、墙钟低约 13% —— 省掉的正是
  `zram_bvec_read()` 的那次解压，与 `d38fab605c66` 的意图一致。
- 读侧：`cwb=1` 的**系统级 CPU 稳定高约 50%**（中位 67→99 j），且墙钟略增；解压被搬到
  `system_highpri_wq`，所以读者自己的 CPU 仍是 0–1 j（**不计在 syscall 上，但机器要付**）。
- **本实验是 compressed writeback 的最坏场景**：刻意把写回的 64MiB 全部读回一次。
  上游的理由是「写回的页大多不会被读回」，此时 `cwb=1` 才净赚。按这里的量级，
  盈亏平衡点大致在**读回率 ~25–30%**：低于它 `cwb=1` 省，高于它 `cwb=0` 省。
  因此**默认 0 是正确取舍**，不应由模块强行打开（§5）。
- 另一条被实测**证伪**的预期：compressed writeback **不减少 4K 写次数**
  （`bd=[15379 0 15379]` 两种模式完全相同，bio 长度恒为 `PAGE_SIZE`，对象后面用
  `memzero_page()` 补零，上游 master `zram_drv.c:1103/2151` 同形）。
  它省的是 CPU，不是闪存写次数——`ksu/abk_runtime_tunables/zram-policy.sh` 里
  「halves the flash traffic」那句注释是错的，已改。

### 3.3 写回上限记账（Batch 17 把扣费挪到提交前）

`batch ∈ {1,32,256}` 各跑一次 `writeback_limit=100` 块：

```
batch=1    limit=100 -> rc=1 bd=[ 100 0 100] limit_now=0
batch=32   limit=100 -> rc=1 bd=[ 100 0 100] limit_now=0
batch=256  limit=100 -> rc=1 bd=[ 100 0 100] limit_now=0
```

**三种批大小都恰好写 100 页**，不因批大而超发——这就是 `zram_account_writeback_submit()`
「提交前扣费」在真机上的验证（若按完成扣费，batch=256 时最多可over-shoot 256 页）。
`rc=1`（`-EIO`）是上游形态：预算耗尽走 `ret = -EIO; break;`，与 bio 错误同码。

## 4. 必须记录的平台缺口：Enforcing 下内核写不了后备文件

第一次实验（后备文件放 `/data/local/tmp`）`bd_stat` 恒为 `0 0 0`、`echo idle > writeback`
返回 **rc=1**。根因不是本批代码：

```
avc: denied { write } for comm="kworker/u16:2" path="/data/local/tmp/b17/backing.img"
     scontext=u:r:kernel:s0 tcontext=u:object_r:shell_data_file:s0 tclass=file permissive=0
```

改用 ROM 自己的 `zram_data_file` 上下文（`/data/per_boot/zram/`）**同样被拒**：

```
avc: denied { write } for comm="kworker/u16:2" path="/data/per_boot/zram/b17_test.img"
     scontext=u:r:kernel:s0 tcontext=u:object_r:zram_data_file:s0 tclass=file permissive=0
```

即：**loop worker（内核线程）对后备文件的 write/read 没有策略许可**，每一页都退化成
`-EIO`，`alloc_block_bdev()` 随之 `free_block_bdev()` 归还 → `bd_stat` 全 0、写回返回错误。
把 SELinux 切到 permissive，**同一脚本、同一文件、同一内核**立刻全绿
（`rc=0`、`bd=[7690 0 7690]`、写回前后 md5 与原文件相同），实验结束已 `setenforce 1` 复原。

含义（对使用者的实际影响）：

- 这台 ROM 上 **zram writeback 在 Enforcing 下不可用**——包括 ROM 自己挂在 zram0 上的
  loop49：它的 `bd_stat` 至今 `0 0 0`，一旦真的被触发也会同样 `-EIO`。
- 这是 **sepolicy 缺口**（缺 `allow kernel zram_data_file:file { read write }` 一类规则），
  内核侧无解；要靠 ROM 集成或 KSU 的 sepolicy 补丁（本模块已有 `abk_fido_selinux` 那条
  分发线）才能打开。
- 因此本节的性能数据是**在 permissive 下测的**：它验证的是内核代码路径与代价，不代表
  当前设备在 Enforcing 下能获得这个收益。

## 5. 对模块的结论（未改代码，留给决定）

1. `zram0` 的 `compressed_writeback=0`：**不是 bug**。后备设备是 ROM 的 mmd 挂的，
   `abk_zram_has_writeback_owner()` 为真，模块按设计只「保留」不重写；而模块自己挂后备设备时
   才会写 `compressed_writeback=1`（`zram-policy.sh` `abk_zram_attach_writeback()`）。
   结合 §3.2 的取舍，**保持 0 是合理的**，不建议改成无条件打开。
2. `writeback_batch_size` 走内核默认 32，模块没有旋钮。若要暴露，注意它**初始化后可写**
   （与 `compressed_writeback` 不同），且 0 被拒、上限 256。
3. `zram-policy.sh` 里「compressed writeback … halves the flash traffic」注释与实测不符，已改为
   CPU 口径（纯注释，不改行为、不动版本号）。

## 6. 未覆盖 / 诚实边界

- **没有**在真实 swap 负载下（zram0、多 GiB）做写回；全部数据来自独立 zram1。
- **没有**触发 `swapoff`/`reset` 与写回并发的竞态，也没做长时间（>1 天）稳定性观察；
  bf62f69574b1 的 UAF 修复在本轮**没有被主动触发**，只验证了它不出错（dmesg 全程无
  zram 相关 splat；仅有的 5 条 `enable_irq` WARNING 是周期性设备问题，其中 2 条早于本实验
  200 秒以上，与本批无关）。
- §3.2 的**系统级 CPU 噪声较大**（8 核 jiffy，窗口 ~0.5s），结论主要依据**任务自身 CPU**
  这一tight 指标；读侧的 +50% 用中位数判断，未做统计显著性检验。
- 换机/换 ROM 后 §2 的节点与 §4 的策略都需重测。
