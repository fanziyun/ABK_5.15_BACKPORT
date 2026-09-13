# GKI vendor hook 调研：`customize_alloc_gfp` 与 `wake_up_new_task`

日期：2026-09（gitiles `?format=TEXT` 逐分支取原文 + GitHub API 取上游 commit 全文，
探测脚本为一次性工具，已删除；上游补丁存档于
`research/upstream-5.15.y/patches/4466afd69452.patch`）。

## 结论速览

> **落地状态（Batch 13，v0.18.0）**：`customize_alloc_gfp_vh`（3 步 upstream-shape
> hook 组）+ `gfp_pressure_fastfail`（ABK 策略载荷：低内存时 order≥
> `abk_gfp_fastfail_order`（默认 9 = THP 级）的慢路径尝试加
> `__GFP_NORETRY|__GFP_NOWARN`，压力门 `si_mem_available() <` high 水位和的
> `abk_gfp_fastfail_pct`%，每秒采样，`abk_gfp_fastfail=0` 关闭）已注册进
> `stable_backport_core`（24 组）。四基线 step_audit / implementation_audit /
> smoke 双遍 + 回滚 / 单测全绿；ABK CI 编译为最终真门禁（本批引入 C）。
> `wake_up_new_task` 维持"基线已自带，不建组"。详见 plan.md Batch 13。

| hook | 是什么 | 5.15 基线（.167/.178/.194/.211-lts） | 能否 backport |
|---|---|---|---|
| `android_rvh_wake_up_new_task` | 新任务首次入队前的受限挂点 | **全部自带**（声明 + 调用点，形状与 6.x 逐字相同） | **无需 backport**，直接注册回调即可用 |
| `android_vh_customize_alloc_gfp` | 慢路径入口改写 `alloc_gfp` 的挂点 | 全部缺失（6.1 也没有；6.6/6.12 才有） | **可以，且极干净**：3 个 hunk 的锚点在 5.15 上逐字存在、各只 1 处 |

## 1. `android_rvh_wake_up_new_task`

声明（`include/trace/hooks/sched.h`，.167 即有）：

```c
DECLARE_RESTRICTED_HOOK(android_rvh_wake_up_new_task,
	TP_PROTO(struct task_struct *p),
	TP_ARGS(p), 1);
```

调用点（`kernel/sched/core.c`，`wake_up_new_task()` 第一条语句，.167 即有）：

```c
void wake_up_new_task(struct task_struct *p)
{
	struct rq_flags rf;
	struct rq *rq;

	trace_android_rvh_wake_up_new_task(p);

	raw_spin_lock_irqsave(&p->pi_lock, rf.flags);
```

语义：fork 出的新任务在**内核拿 rq 锁、做首次放置（`select_task_rq_fair`）
之前**先把 `task_struct *p` 交给 vendor 模块。restricted（rvh）= 允许回调覆盖
内核默认行为（对比 `android_vh_*` 只做观察/经指针改写参数）。用途：自定义首
放置（把子任务钉到父核/大核簇）、fork 时一次性 boost/策略、拦截唤醒。
`rvh = 0` 表示多回调时全部执行、不做"首个接管即返回"。

**对本仓库**：ABK 的 4 条基线树全部自带（含 deprecated .167），不建 group；
任何想用它（例如 plan.md 里 FAS fast_start / 初始放置方向）直接在 payload
`.c` 里 `register_trace_android_rvh_wake_up_new_task()`，与 Batch 10 挂
`android_vh_scheduler_tick` 同一手法。

## 2. `android_vh_customize_alloc_gfp`

上游 commit（**android15-6.6 线引入**，2024-04-30 由 Todd Kjos 合入；6.12 同带；
android14-6.1 无；android13-5.15 全分支无）：

```
4466afd69452088299671fcf3df891ba07daa8e0
ANDROID: vendor_hooks: add vendor hook for supporting customize alloc_gfp at alloc_page_slowpath
Bug: 337192903  Signed-off-by: liwei <liwei1234@oppo.com> (OPPO)
```

3 文件、5 行新增：声明 + 调用点 + `EXPORT_TRACEPOINT_SYMBOL_GPL`。

语义：`__alloc_pages()` 快路径（`get_page_from_freelist`）失败后、进入
`__alloc_pages_slowpath()` **之前**，把"即将用于慢路径的 gfp"以指针交出去。
回调可以按 order 改写：摘 `__GFP_IO/__GFP_FS/__GFP_RECLAIM`（拒绝进直接回收/
回写）、加 `__GFP_NORETRY`/`__GFP_NOWARN`（高阶分配失败即走 fallback）等。
与 5.15 已有的 `android_vh_alloc_pages_slowpath`（入口之后、只读 delta）互补：
这个钩子改的是**进入慢路径时真正生效的 gfp**。upstream 树内无 consumer，
纯 ODM 策略点。

### 5.15 锚点实测（.167 与 android13-5.15-lts/.216 各取一次）

ACK 的 android13-5.15 已把 6.1 的 cpuset 快路径重构系列收编，慢路径入口块与
6.6 **逐字相同**（变量名已是 `alloc_gfp`/`gfp`，含 "Restore the original
nodemask" 注释）：

```c
	/*
	 * Restore the original nodemask if it was potentially replaced with
	 * &cpuset_current_mems_allowed to optimize the fast-path attempt.
	 */
	ac.nodemask = nodemask;

	page = __alloc_pages_slowpath(alloc_gfp, order, &ac);
```

计数：`ac.nodemask = nodemask;` ×1、`page = __alloc_pages_slowpath(alloc_gfp,
order, &ac);` ×1、`trace_android_vh_customize_alloc_gfp` ×0（未打）。

- `include/trace/hooks/mm.h`：无 `android_vh_page_add_new_anon_rmap`（上游该
  hunk 的上下文行），所以移植时把 `DECLARE_HOOK` 块插到
  `#endif /* _TRACE_HOOK_MM_H */`（文件中唯一）之前——最终形态与上游一致
  （上游就是插在该 endif 前的末尾）。
- `drivers/android/vendor_hooks.c`：上下文行
  `EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);` ×1（.167 与
  lts 均有），与上游 hunk 的相对位置相同（上游即插在此行之后）。

### 拟定 group（3 步全 `required`，upstream-shape：不加 ABK 注释标记）

1. `mm/page_alloc.c`：old = `ac.nodemask = nodemask;\n\n\tpage =
   __alloc_pages_slowpath(alloc_gfp, order, &ac);`，new 在 `ac.nodemask` 行后
   插 `trace_android_vh_customize_alloc_gfp(&alloc_gfp, order);`（逐字上游）。
2. `include/trace/hooks/mm.h`：old = `#endif /* _TRACE_HOOK_MM_H */`，new =
   `DECLARE_HOOK(android_vh_customize_alloc_gfp,\n\tTP_PROTO(gfp_t *alloc_gfp,
   unsigned int order),\n\tTP_ARGS(alloc_gfp, order));\n\n#endif /*
   _TRACE_HOOK_MM_H */`。
3. `drivers/android/vendor_hooks.c`：old =
   `EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);`，new 在其后加
   `EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);`。

配套（已随 Batch 13 完成）：`FETCH_FILES` 补 `include/trace/hooks/mm.h` 与
`drivers/android/vendor_hooks.c`（另同步 `step_audit.py` AUDIT_FILES 与
`smoke.sh` SMOKE_FILES）；`GROUP_COUNTS`（core 子）+2（hook 组与 policy 组
分开，policy 组在树内无 hook 时 `blocked_by_shape`，防 undefined
`register_trace_` 编译陷阱）；unit test / implementation_audit 各扩；
`module.conf` 0.17.1 → 0.18.0。

### 红线自查

- KMI：只**新增**导出 tracepoint 符号（`__tracepoint_android_vh_*` +
  key），不动任何导出结构体字段、不占 KABI 槽位；现有符号 CRC 不变 ⇒
  既有 GKI vendor 模块照常加载。
- 配置：`CONFIG_ANDROID_VENDOR_HOOKS=y` 已在 gki_defconfig（.167 实测），
  config lane 无需动作。
- 范围：这是"结构挂点"类移植。**钩子本身惰性**（上游也没有 in-tree
  consumer），故按拍板配了 ABK 策略载荷 `gfp_pressure_fastfail`
  （低内存时 THP 级慢路径尝试加 `__GFP_NORETRY|__GFP_NOWARN`）——
  见文件顶部的落地状态与 plan.md Batch 13。
