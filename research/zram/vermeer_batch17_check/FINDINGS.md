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
  内核侧无解；要靠 ROM 集成或 KSU 的 sepolicy 补丁。
- 因此本节的性能数据是**在 permissive 下测的**：它验证的是内核代码路径与代价，不代表
  当时设备在 Enforcing 下能获得这个收益。

### 4.1 后续：缺口已由 Batch 18（v0.23.0）用 companion 模块打通并复测

- **方案**：`ksu/abk_runtime_tunables/sepolicy.rule` 只放一条最小权限
  `allow kernel zram_data_file file { read write }`，由 `post-fs-data.sh` 经
  `ksud sepolicy apply` 提交 —— **必须在任何东西挂上后备设备之前**，因为从那一刻起被拒的
  是内核线程。只给 `read`/`write`：内核从不解析路径（文件由 root 域的 `losetup` 打开，
  内核域只对**已打开的文件**做 I/O），32MiB 写回 + 全量读回零残留 AVC。
- **复测**（全程 Enforcing；脚本 `b17_enforcing.sh` / `b17_rompath.sh`，原始输出 `raw/08`–`raw/11`）：
  规则前 `rc=1 bd=[0 0 0]`；提交规则后 `rc=0 bd=[7690 7690 7690]`、写回前后读回 md5 一致、
  **0 条 zram AVC**。ROM 自己的 zram0 路径：先 `echo all > idle` 再写回，16MiB 预算（4096 块）
  **恰好写满 4096 页**（`bd=[4096 0 4096]`）、0 条 AVC，随后已把 ROM 的
  `writeback_limit`/`writeback_limit_enable` 原值（2752512/1）复原。
- **开机路径复测（2026-09-14 22:41，同机同内核，全程 Enforcing）**：把 v0.7.0 模块装进
  `modules_update`（当时**运行中的模块目录里还没有 `sepolicy.rule`**）后重启，开机日志出现
  `selinux: submitted /data/adb/modules/abk_runtime_tunables/sepolicy.rule via /data/adb/ksud`，
  重启后**不做任何手工 apply**：独立 zram1 上 32MiB 写回 `rc=0 bd=[7690 7690 7690]`、md5 前后一致、
  0 条 AVC；ROM 自己的 zram0 路径 `rc=0 bd=[0 0 0] → [1 0 1]`（该时刻 swap 里**只有 1 页**：
  `mm_stat` 首个字段 `orig_data_size=4096`、`/proc/swaps` 用 0 KiB，所以「恰好一页」正是对的）。
  证据：`raw/12-post-reboot.txt`。
- **诚实边界**：`ksud sepolicy apply` 对**不存在的 type / permission 也返回 0**，返回码只代表
  「语句已提交」，所以判据只能是 `bd_stat` 与 I/O 正确性；规则只在本机这一个 ROM 上验证过。
- **更正一处失实说法**：本条此前写「本模块已有 `abk_fido_selinux` 那条分发线」——`abk_fido_selinux`
  是**另一个仓库**（`ABK_FIDO_KEY_MODULE`）的独立 KernelSU 模块（给 `/metadata` 上的 FIDO 存储
  加 kernel 域规则），本仓库当时**没有任何 sepolicy 分发线**；上面这条 `sepolicy.rule` 是本仓库第一条。

## 5. 对模块的结论（Batch 17 未改代码；Batch 18 只加了上面那条 sepolicy 规则）

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

## 7. 直接测量 bio batching：并发深度（同日复测，**全程 Enforcing**）

仪器：本目录 `b17_inflight.sh`（mksh 语法、`sh -n` 通过；**从不调用 setenforce**，开头
直接对非 Enforcing 退出；只用 hot_add 出来的 zram1，zram0 一行未碰）。原始输出
`raw/13-inflight-and-cwb-enforcing.txt`（3 次完整重复 + 1 轮 cwb）。

### 7.1 为什么不能靠数 bio / 数 I/O 来验证 batching

移植后的路径**每页仍然只建一个请求**（`scripts/batch17_core_zram_writeback.py`：
`bio_init(&req->bio, &req->bio_vec, 1)` + `bio_add_page(&req->bio, req->page, PAGE_SIZE, 0)`），
只是异步提交、最多 `wb_batch_size` 个同时在飞。因此**请求数、bio 数、总扇区数在
batch=1 与 batch=256 下逐字节相同**——实测 7690 请求 / 61520 扇区 / `bd_stat` 相同，
6 次重复无一例外。**batching 买的是并发深度，不是更少的 I/O**；能区分两者的唯一直接
观测量是「同时有多少个请求在飞」。这也纠正了「打开 20 个应用看 bd_stat」这类测法：
它连方向都指错了。

测法：`block_rq_issue` + `block_rq_complete` tracepoint，按测试 loop 设备算出的
`dev` 原始值（`major*1048576+minor`，见 tracepoint `print fmt` 的 `huge_encode_dev` 编码）
过滤；每行 µs 时间戳拆成整数（时间戳恒 6 位小数），键取 `µs*2`（issue 再 +1），使一次
`sort -n` 既排序又让同微秒的 completion 先于 issue（对峰值偏保守），再单次扫描出在飞
峰值与时间加权平均，另取相邻 issue 间隔分位数。`block_bio_complete` 在这里**不可用**：
它只对带 `BIO_TRACE_COMPLETION` 的 bio 触发，即只有 blktrace 在跑时才出现
（实测 0 条，而 `block_rq_*` 7690 条）。

### 7.2 结果（32MiB、cwb=0；下表为 `raw/13` 那一轮）

| batch | 在飞峰值 | 时间加权平均 | issue 间隔 p50 | p90 | 请求数 | 扇区 | 墙钟(traced) | vcs | 任务 CPU |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **1** | 0.60 | 90 µs | 118 µs | 7690 | 61520 | 740 ms | 7693 | 29 j |
| 8 | **8** | 6.36 | 8 µs | 38 µs | 7690 | 61520 | 140 ms | 1897 | 6 j |
| 32 | **32** | 28.69 | 10 µs | 47 µs | 7690 | 61520 | 210 ms | 1812 | 11 j |
| 256 | **128** | 108.07 | 9 µs | 17 µs | 7690 | 61520 | 240 ms | 324 | 9 j |

6 次重复下，**在飞峰值恒等于 1 / 8 / 32**，无一次例外——batching 的机制到此不再依赖墙钟
推断。三点必须一起读：

1. **峰值 == batch，而请求数与扇区数完全不变**：这是「并发、未合并」的判据。
2. **batch=256 的峰值只有 128**：loop 设备的 `queue/nr_requests` 实测为 **128**，所以
   `wb_batch_size` 再往上加也压不进更深的队列。这是设备属性，不是移植缺陷；真实旋钮
   上限应视作 `min(wb_batch_size, 128)`。
3. **issue 间隔 p50 从 ~90–220 µs 掉到 4–13 µs**：batch=1 每页要等一个完整来回，
   batch>=8 是连续灌入。

### 7.3 墙钟不是这台设备上的可靠判据（方法论，重要）

同一配置 batch=1 在 6 次运行里墙钟为 **430–1970 ms**（相差 4.6 倍），batch=32 为
**140–420 ms**——设备自身的负载波动远大于待测差异。稳定的判据是三个：自愿上下文切换
（batch=1 中位 ~7590，约等于页数；batch=32 中位 ~2043，**3.7x**）、任务 CPU jiffy
（中位 41 -> 12 j，**3.6x**）、以及上面的 trace 指标；墙钟中位 1200 ms -> 240 ms（**5x**）。

因此**不要**把 §3.1 的「17x」当作可复现的规格：那是 64MiB 数据集、当时较空闲的设备、且
permissive 下的数字。本轮既没有复现也没有推翻它——条件不同。可复现的说法是「上下文切换
与任务 CPU 各降到约 1/3.7」。

### 7.4 compressed writeback：只复现了方向（32MiB、batch=32、3+3 次）

- **写侧**：任务自身 CPU cwb=0 中位 6.5 j、cwb=1 中位 6.0 j；6 次里 2 次低 ~20%、2 次无
  差别、2 次反向。**方向正确但被 jiffy 分辨率吃掉**。
- **读侧**：系统级 CPU（8 核 jiffy）cwb=0 中位 28.5、cwb=1 中位 39.5（**+39%**），两轮
  方向一致；而 `dd` 子进程自身的 user/sys 都落在 1 jiffy（4ms）量化噪声内，看不出差别
  ——与「解压被搬到 `system_highpri_wq`、不记在读者账上」的设计一致。§3.2 的 **+50%**
  方向复现，量级无法在本轮分辨。
- **I/O 完全不变**：`bd=[7690 7690 7690]` 两模式相同，**§3.2「cwb 不减少 4K 写次数」
  再次成立**。
- **方法论结论**：batch=32 之后一次 32MiB 写回只有 ~60–70 ms / 4–10 jiffy，**jiffy 分辨率
  不足以分辨 cwb 的解压节省**。要量出 §3.2 量级的差异必须放大数据集（Batch 17 用 64MiB）
  或把重复提到几十次。换句话说：**batching 生效之后 cwb 的收益变得更难测**，因为被省掉的
  那部分只是写回总代价里固定的一小段。
- **12 个单元格全部** `rc=0`、`md5=EQ`、**`zram_avc=0`**：Batch 18 那条规则在
  Enforcing 下覆盖整个矩阵（§4.1 只验过 2 个单元格，这里补全）。

## 8. 运行期现实：这台设备上 writeback 根本不会被触发

§7 证明了代码路径正确，但它同时暴露出一个更基础的事实——**当前配置下没有任何东西会去
触发 writeback**，所以这两个特性在本机是「不可达」的，而不是「收益小」：

| 证据 | 观察值 |
|---|---|
| `mmd` 进程 | **不存在**（`ps -A` 只有 `vendor.xiaomi.hardware.swap@1.0-service`） |
| `init.svc.mmd_setup` | `stopped`（开机 33.8s 跑过一次 oneshot 后退出） |
| `vendor.zram.disable` | **`1`** |
| `losetup -a` 的 loop49（zram0 的后备设备） | `/dev/block/loop49: [64819]:313194 ()` —— **文件名是空的**，即后备文件已被 unlink，loop 设备还挂着那个孤立 inode |
| `/data/per_boot/zram/` | 只剩本次实验的 `b17_probe.img`，**ROM 自己的后备文件不在** |
| zram0 本次开机 615 秒后 `bd_stat` | `[1 0 1]` —— 那 1 块来自我们自己的实验 |
| companion 侧 | `zram_recompress_trigger.sh` 只写 `idle` 与 `recompress*`；`abk_zram_attach_writeback()` 只**挂/保**后备设备，**没有任何一处写 `writeback` 节点**（§5.1 的设计） |

含义：

- **「连续打开 20 个应用」不可能测到这两个特性**：应用启动走的是 zram 压缩/换出热路径，而
  batching 与 cwb 都在 `writeback_store()` 里；本机连一次 writeback 都不会发生，预期差异
  恒等于 0。这不是测法不够灵敏，而是**被测路径没有被进入**。
- 因此 Batch 17/18 的代码在本机当前配置下是**未被执行过的代码**（除我们的实验）。它是否
  值得保留，取决于是否要让 writeback 真的跑起来——这是产品决策，不是性能决策：
  要跑起来必须有人 (a) 让 ROM 的 mmd 回来（其后备文件现在已被 unlink），或 (b) 让 companion
  自己按预算发 sweep（`echo <age> > idle` + `echo idle > writeback`，受 `writeback_limit`
  约束）。后者会给闪存写入量，且 cwb 的盈亏平衡点在 §3.2 的读回率 ~25–30%——而**真实读回率
  目前无人测过**。
- 本轮**没有改任何内核或模块代码**，因此版本号不动（仍是 module 0.23.0 / companion v0.7.0），
  本批只新增一个测量脚本与本节记录。

### 8.1 下一个该测的东西（如果要把 §8 变成收益）

不是启动 20 个应用，而是一次**计数研究**：在真实使用的一段时间里记录 `bd_stat` /
`io_stat` / `/proc/swaps` 的增量，以及写回后**被读回的页比例**。读回率是 cwb 唯一的
决策参数，且只能在真实负载下测——而它必须在 writeback 真的被触发之后才有意义。

