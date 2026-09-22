# 版权归属 / Third-party attribution

`LICENSE` covers only what **this** repository authors: the graft registry, the
anchor/step engine, the test suite and the device tooling. Everything the
registry writes into a kernel tree comes from somewhere else, and no upstream
copyright is transferred or claimed here.

## The grafted code is Linux kernel code

**© Linus Torvalds and the Linux kernel's contributors, GPL-2.0.** In the kernel,
copyright stays with the individual author — or with their employer, where a
work-for-hire arrangement applies — which is why upstream ships a `CREDITS` file
rather than naming a single holder. The table below lists every upstream commit
this module grafts for which a `.patch` is archived under `research/`
(**91 commits**, **49 distinct authors**). Each row's author is
that patch's own `From:` header, and the patch stays in-tree as the evidence.

## Corporate and organisational holders

Where a graft carries an explicit copyright or provenance statement, that
statement is reproduced here rather than inferred:

| holder | evidence in-tree | what they own |
|---|---|---|
| **Linus Torvalds and the Linux kernel contributors** | the whole graft set; `LICENSE` | the `5.15.y` / ACK / mainline commits themselves |
| **Google** | `google.com` and `chromium.org` authors in the table below — Sergey Senozhatsky, Yu Zhao, Richard Chang, T.J. Mercier, Suleiman Souhlal, Eric Dumazet | **25 of the 91 commits**, including MGLRU (`37a260870f2c`), the zram writeback reworks, the ACK proactive-reclaim batching (`287d5fedb377`) and the excess-steal-time drop (`56135262c1f9`) |
| **Qualcomm Technologies, Inc. and/or its subsidiaries; The Linux Foundation; Qualcomm Innovation Center, Inc.** | the SPDX + copyright headers of `research/popsicle_w_oss/walt_extract/` (`voter.h`, `voter.c`, `pipeline.c`, `smart_freq.c`, `cpufreq_walt.c`) | the WALT smart-freq / pipeline / voter line |
| **OPLUS** | `scripts/abk_stable_core.py` — "Copyright 2020-2022 Oplus, as shipped in Xiaomi_Kernel_OpenSource" | the `mi_dynamic_readahead` module (Batch 9-1) |
| **Xiaomi (MiCode/Xiaomi_Kernel_OpenSource)** | `plan.md`, `docs/survey_popsicle_w_611.md`, `research/popsicle_w_oss/README.md` | distributor of the popsicle-w-oss branch the WALT and dynamic-readahead lines were taken from |
| **OPPO** | `4466afd69452.patch` — `wei li <liwei1234@oppo.com>`; `CHANGELOG.md` records it as Bug 337192903 | the `android_vh_customize_alloc_gfp` vendor hook (Batch 13) |

Google is named here because its engineers' `From:` addresses are in the record,
not as a copyright-assignment claim — an individual developer, or their employer
under work-for-hire, holds the copyright and only they can assert it.

## Individual authors

Every distinct `From:` address in the graft set, by number of grafted commits:

| commits | author |
|---|---|
| 12 | Sergey Senozhatsky <senozhatsky@chromium.org> |
| 6 | Mel Gorman <mgorman@techsingularity.net> |
| 6 | Yu Zhao <yuzhao@google.com> |
| 4 | Jiayuan Chen |
| 4 | Minchan Kim <minchan@kernel.org> |
| 4 | Richard Chang <richardycc@google.com> |
| 4 | _not recorded in the archived fragment_ |
| 3 | K Prateek Nayak <kprateek.nayak@amd.com> |
| 3 | Shakeel Butt <shakeel.butt@linux.dev> |
| 2 | Dan Schatzberg <schatzberg.dan@gmail.com> |
| 2 | Gao Xiang <hsiangkao@linux.alibaba.com> |
| 2 | Huang Ying <ying.huang@linux.alibaba.com> |
| 2 | Jan Kara <jack@suse.cz> |
| 2 | Kairui Song <kasong@tencent.com> |
| 1 | "JP Kobryn (Meta)" <jp.kobryn@linux.dev> |
| 1 | "T.J. Mercier" <tjmercier@google.com> |
| 1 | Aboorva Devarajan <aboorvad@linux.ibm.com> |
| 1 | Adam Li <adamli@os.amperecomputing.com> |
| 1 | Chen Jinghuang <chenjinghuang2@huawei.com> |
| 1 | Chen Ridong <chenridong@huawei.com> |
| 1 | Chengming Zhou <zhouchengming@bytedance.com> |
| 1 | Cong Zhang <cong.zhang@oss.qualcomm.com> |
| 1 | Dave Hansen <dave.hansen@linux.intel.com> |
| 1 | Efly Young <yangyifei03@kuaishou.com> |
| 1 | Eric Dumazet <edumazet@google.com> |
| 1 | Feng Tang <feng.tang@intel.com> |
| 1 | Hrushikesh Salunke |
| 1 | Imran Khan |
| 1 | Jinjiang Tu <tujinjiang@huawei.com> |
| 1 | Joel Fernandes <joel@xxxxxxxxxxxxxxxxx> |
| 1 | Keith Busch <kbusch@kernel.org> |
| 1 | Kiryl Shutsemau (Meta) |
| 1 | Longlong Xia <xialonglong@kylinos.cn> |
| 1 | Matt Fleming <mfleming@cloudflare.com> |
| 1 | Qi Zheng <zhengqi.arch@bytedance.com> |
| 1 | Ryan Roberts <ryan.roberts@arm.com> |
| 1 | SeongJae Park <sj@kernel.org> |
| 1 | Sheng Yong <shengyong1@xiaomi.com> |
| 1 | Steven Rostedt <rostedt@goodmis.org> |
| 1 | Suleiman Souhlal <suleiman@google.com> |
| 1 | Thadeu Lima de Souza Cascardo <cascardo@igalia.com> |
| 1 | Usama Arif |
| 1 | Valentin Schneider <valentin.schneider@arm.com> |
| 1 | Vlastimil Babka <vbabka@suse.cz> |
| 1 | Waiman Long <longman@redhat.com> |
| 1 | Yafang Shao <laoar.shao@gmail.com> |
| 1 | Zeng Jingxiang <linuszeng@tencent.com> |
| 1 | fujunjie |
| 1 | wei li <liwei1234@oppo.com> |

Thirteen of the 91 carry no usable `From:` domain: four are the bare diffs with
no `From:` header at all, and nine more ship no `<...>` address (Jiayuan Chen ×4,
then fujunjie, Usama Arif, Imran Khan, Hrushikesh Salunke and Kiryl Shutsemau).
Separately, upstream's own `5.15.y` patch for `b37a667c6242` arrives with Joel
Fernandes' address already redacted (`joel@xxxxxxxxxxxxxxxxx`) — hence the
`xxxxxxxxxxxxxxxxx` "domain" below, which is upstream's redaction rather than
anything this repository introduced.

By `From:` address domain — a proxy for where each author was working, and the
reason the list above is dominated by a handful of companies:

| commits | domain |
|---|---|
| 13 | `_no address_` |
| 13 | `google.com` |
| 12 | `chromium.org` |
| 6 | `kernel.org` |
| 6 | `techsingularity.net` |
| 4 | `linux.alibaba.com` |
| 4 | `linux.dev` |
| 3 | `amd.com` |
| 3 | `gmail.com` |
| 3 | `huawei.com` |
| 3 | `suse.cz` |
| 3 | `tencent.com` |
| 2 | `arm.com` |
| 2 | `bytedance.com` |
| 1 | `cloudflare.com` |
| 1 | `goodmis.org` |
| 1 | `igalia.com` |
| 1 | `intel.com` |
| 1 | `kuaishou.com` |
| 1 | `kylinos.cn` |
| 1 | `linux.ibm.com` |
| 1 | `linux.intel.com` |
| 1 | `oppo.com` |
| 1 | `os.amperecomputing.com` |
| 1 | `oss.qualcomm.com` |
| 1 | `redhat.com` |
| 1 | `xiaomi.com` |
| 1 | `xxxxxxxxxxxxxxxxx` |

## Per-commit attribution

| upstream commit | author | subject | archived as |
|---|---|---|---|
| `0388536ac291` | Efly Young <yangyifei03@kuaishou.com> | mm:vmscan: fix inaccurate reclaim during proactive reclaim | `upstream-5.15.y/patches/0388536ac291.patch` |
| `0b8d16680d9f` | _not recorded in the archived fragment_ |  | `upstream-5.15.y/patches/0b8d16680d9f.patch` |
| `0beeaf14e7b9` | Jiayuan Chen | memcg: bail out memory.high when memcg is dying | `upstream-5.15.y/patches/0beeaf14e7b9.patch` |
| `0eac511c7657` | Vlastimil Babka <vbabka@suse.cz> | mm, page_alloc, thp: prevent reclaim for __GFP_THISNODE THP | `upstream-5.15.y/patches/0eac511c7657.patch` |
| `0faa77afe72b` | Jinjiang Tu <tujinjiang@huawei.com> | filemap: optimize folio refount update in filemap_map_pages | `upstream-5.15.y/patches/0faa77afe72b.patch` |
| `10228e0a5123` | Jiayuan Chen | memcg-v1: bail out reclaim when memcg is dying | `upstream-5.15.y/patches/10228e0a5123.patch` |
| `143937ca51cc` | Huang Ying <ying.huang@linux.alibaba.com> | arm64, mm: avoid always making PTE dirty in pte_mkwrite() | `upstream-5.15.y/patches/143937ca51cc.patch` |
| `17dedfd6de69` | Mel Gorman <mgorman@techsingularity.net> | mm/page_alloc: explicitly record high-order atomic | `upstream-5.15.y/patches/17dedfd6de69.patch` |
| `1bc542c6a0d1` | Zeng Jingxiang <linuszeng@tencent.com> | mm/vmscan: wake up flushers conditionally to avoid cgroup OOM | `upstream-5.15.y/patches/1bc542c6a0d1.patch` |
| `25fc82f3a868` | K Prateek Nayak <kprateek.nayak@amd.com> | sched/core: Remove the unnecessary need_resched() check in | `upstream-5.15.y/patches/25fc82f3a868.patch` |
| `287d5fedb377` | "T.J. Mercier" <tjmercier@google.com> | mm: memcg: use larger batches for proactive reclaim | `upstream-5.15.y/patches/287d5fedb377.patch` |
| `37a260870f2c` | Yu Zhao <yuzhao@google.com> | mm/mglru: rework type selection | `upstream-5.15.y/patches/37a260870f2c.patch` |
| `38a4826f1bdf` | K Prateek Nayak <kprateek.nayak@amd.com> | sched/core: Prevent wakeup of ksoftirqd during idle load | `upstream-5.15.y/patches/38a4826f1bdf.patch` |
| `3b3c672a66db` | Chen Jinghuang <chenjinghuang2@huawei.com> | sched/rt: Skip currently executing CPU in rto_next_cpu() | `upstream-5.15.y/patches/3b3c672a66db.patch` |
| `410abb20acae` | Dan Schatzberg <schatzberg.dan@gmail.com> | mm: add defines for min/max swappiness | `upstream-5.15.y/patches/410abb20acae.patch` |
| `43c4cfde7e37` | SeongJae Park <sj@kernel.org> | mm/madvise: batch tlb flushes for MADV_DONTNEED[_LOCKED] | `upstream-5.15.y/patches/43c4cfde7e37.patch` |
| `4466afd69452` | wei li <liwei1234@oppo.com> | ANDROID: vendor_hooks: add vendor hook for supporting | `upstream-5.15.y/patches/4466afd69452.patch` |
| `46c66d975a58` | Waiman Long <longman@redhat.com> | locking/semaphore: Use wake_q to wake up processes outside | `upstream-5.15.y/patches/46c66d975a58.patch` |
| `4c4e238d3ada` | Matt Fleming <mfleming@cloudflare.com> | mm/page_alloc: let GFP_ATOMIC order-0 allocs access | `upstream-5.15.y/patches/4c4e238d3ada.patch` |
| `4cdc1bdf4094` | _not recorded in the archived fragment_ |  | `upstream-5.15.y/patches/4cdc1bdf4094.patch` |
| `56135262c1f9` | Suleiman Souhlal <suleiman@google.com> | sched: Don't try to catch up excess steal time. | `upstream-5.15.y/patches/56135262c1f9.patch` |
| `61c663e020d2` | Yu Zhao <yuzhao@google.com> | mm/truncate: batch-clear shadow entries | `upstream-5.15.y/patches/61c663e020d2.patch` |
| `6375e95f381e` | Qi Zheng <zhengqi.arch@bytedance.com> | mm: pgtable: reclaim empty PTE page in madvise(MADV_DONTNEED) | `upstream-5.15.y/patches/6375e95f381e.patch` |
| `6422cde1b0d5` | Gao Xiang <hsiangkao@linux.alibaba.com> | erofs: use buffered I/O for file-backed mounts by default | `upstream-5.15.y/patches/6422cde1b0d5.patch` |
| `66bcd6c577d8` | Eric Dumazet <edumazet@google.com> | net: call cond_resched() less often in __release_sock() | `upstream-5.15.y/patches/66bcd6c577d8.patch` |
| `68cd9050d871` | Dan Schatzberg <schatzberg.dan@gmail.com> | mm: add swappiness= arg to memory.reclaim | `upstream-5.15.y/patches/68cd9050d871.patch` |
| `6aeeac48fc1b` | K Prateek Nayak <kprateek.nayak@amd.com> | sched/fair: Check idle_cpu() before need_resched() to detect | `upstream-5.15.y/patches/6aeeac48fc1b.patch` |
| `70a64b7919cb` | Shakeel Butt <shakeel.butt@linux.dev> | memcg: dynamically allocate lruvec_stats | `upstream-5.15.y/patches/70a64b7919cb.patch` |
| `735457683e23` | Thadeu Lima de Souza Cascardo <cascardo@igalia.com> | mm/page_alloc: only set ALLOC_HIGHATOMIC for __GPF_HIGH | `upstream-5.15.y/patches/735457683e23.patch` |
| `757dd8193f6c` | Jiayuan Chen | memcg: bail out proactive reclaim when memcg is dying | `upstream-5.15.y/patches/757dd8193f6c.patch` |
| `770c8d55c428` | Sheng Yong <shengyong1@xiaomi.com> | lib/iov_iter: fix to increase non slab folio refcount | `upstream-5.15.y/patches/770c8d55c428.patch` |
| `798c0330c2ca` | Yu Zhao <yuzhao@google.com> | mm/mglru: rework aging feedback | `upstream-5.15.y/patches/798c0330c2ca.patch` |
| `7a1eb89f7918` | Jan Kara <jack@suse.cz> | readahead: don't shorten readahead window in read_pages() | `upstream-5.15.y/patches/7a1eb89f7918.patch` |
| `7e1b6b281aa8` | Ryan Roberts <ryan.roberts@arm.com> | randomize_kstack: Maintain kstack_offset per task | `upstream-5.15.y/patches/7e1b6b281aa8.patch` |
| `85f58ee33c6c` | Mel Gorman <mgorman@techsingularity.net> | mm/page_alloc: explicitly define how __GFP_HIGH non-blocking | `upstream-5.15.y/patches/85f58ee33c6c.patch` |
| `8a2375b0e9b8` | Huang Ying <ying.huang@linux.alibaba.com> | arm64, mm: avoid always making PTE dirty in pte_mkwrite() | `upstream-5.15.y/patches/8a2375b0e9b8.patch` |
| `8fe7de5d1c7f` | Cong Zhang <cong.zhang@oss.qualcomm.com> | blk-mq: Abort suspend when wakeup events are pending | `upstream-5.15.y/patches/8fe7de5d1c7f.patch` |
| `92e52ff398b5` | Mel Gorman <mgorman@techsingularity.net> | mm/page_alloc: rename ALLOC_HIGH to ALLOC_MIN_RESERVE | `upstream-5.15.y/patches/92e52ff398b5.patch` |
| `9646443f28f3` | Keith Busch <kbusch@kernel.org> | blk-mq: use quiesced elevator switch when reinitializing | `upstream-5.15.y/patches/9646443f28f3.patch` |
| `9669b87065a6` | "JP Kobryn (Meta)" <jp.kobryn@linux.dev> | mm/lruvec: preemptively free dead folios during lru_add drain | `upstream-5.15.y/patches/9669b87065a6.patch` |
| `9b0fcac3cfe7` | fujunjie | mm/filemap: do not count FAULT_FLAG_TRIED retries as mmap hits | `upstream-5.15.y/patches/9b0fcac3cfe7.patch` |
| `9cbfd1c3c83b` | Yu Zhao <yuzhao@google.com> | mm/mglru: clean up workingset | `upstream-5.15.y/patches/9cbfd1c3c83b.patch` |
| `9da195a2d35b` | Mel Gorman <mgorman@techsingularity.net> | mm/page_alloc: treat RT tasks similar to __GFP_HIGH | `upstream-5.15.y/patches/9da195a2d35b.patch` |
| `a4519e5b648a` | Usama Arif | mm/swap_state: remove unnecessary lru_add_drain() from readahead | `upstream-5.15.y/patches/a4519e5b648a.patch` |
| `aaa98b100ea8` | Imran Khan | mm/vmstat: avoid taking zone lock in /proc/buddyinfo reads | `upstream-5.15.y/patches/aaa98b100ea8.patch` |
| `b001cf7d16dd` | Hrushikesh Salunke | mm/page_alloc: replace kernel_init_pages() with batch page clearing | `upstream-5.15.y/patches/b001cf7d16dd.patch` |
| `b1a71694fb00` | Yu Zhao <yuzhao@google.com> | mm/mglru: rework refault detection | `upstream-5.15.y/patches/b1a71694fb00.patch` |
| `b37a667c6242` | Joel Fernandes <joel@xxxxxxxxxxxxxxxxx> | rcu/nocb: Add an option to offload all CPUs on boot | `upstream-5.15.y/patches/b37a667c6242.patch` |
| `b3a5ff8c4b6e` | Chengming Zhou <zhouchengming@bytedance.com> | sched/psi: Use task->psi_flags to clear in CPU migration | `upstream-5.15.y/patches/b3a5ff8c4b6e.patch` |
| `c1b8856c5a7d` | Mel Gorman <mgorman@techsingularity.net> | mm/page_alloc: explicitly define what alloc flags deplete min | `upstream-5.15.y/patches/c1b8856c5a7d.patch` |
| `c635a42d9b74` | Feng Tang <feng.tang@intel.com> | mm/page_alloc: detect allocation forbidden by cpuset and bail | `upstream-5.15.y/patches/c635a42d9b74.patch` |
| `ca8527f25736` | Mel Gorman <mgorman@techsingularity.net> | mm/page_alloc: split out buddy removal code from rmqueue into | `upstream-5.15.y/patches/ca8527f25736.patch` |
| `cc8ec7be78ff` | Yu Zhao <yuzhao@google.com> | mm/mglru: optimize deactivation | `upstream-5.15.y/patches/cc8ec7be78ff.patch` |
| `d071dba5ddd2` | Valentin Schneider <valentin.schneider@arm.com> | sched/fair: Add NOHZ balancer flag for nohz.next_balance | `upstream-5.15.y/patches/d071dba5ddd2.patch` |
| `d3db2c042591` | Shakeel Butt <shakeel.butt@linux.dev> | mm: optimize invalidation of shadow entries | `upstream-5.15.y/patches/d3db2c042591.patch` |
| `d5ea5e5e50df` | Jan Kara <jack@suse.cz> | readahead: properly shorten readahead when falling back to | `upstream-5.15.y/patches/d5ea5e5e50df.patch` |
| `d8312a56d9a1` | Steven Rostedt <rostedt@goodmis.org> | sched/rt: Have RT_PUSH_IPI be default off for non PREEMPT_RT | `upstream-5.15.y/patches/d8312a56d9a1.patch` |
| `d99f14f8b142` | Adam Li <adamli@os.amperecomputing.com> | sched/fair: Only update stats for allowed CPUs when looking | `upstream-5.15.y/patches/d99f14f8b142.patch` |
| `dc37771a43d4` | Richard Chang <richardycc@google.com> | mm: vmscan: abort proactive reclaim early when freezing for | `upstream-5.15.y/patches/dc37771a43d4.patch` |
| `de77545c72c4` | Yafang Shao <laoar.shao@gmail.com> | cgroup: Make operations on the cgroup root_list RCU safe | `upstream-5.15.y/patches/de77545c72c4.patch` |
| `e13f634f50d5` | Jiayuan Chen | memcg: bail out memory.max when memcg is dying | `upstream-5.15.y/patches/e13f634f50d5.patch` |
| `e8400a074123` | _not recorded in the archived fragment_ |  | `upstream-5.15.y/patches/e8400a074123.patch` |
| `eda99622e6f3` | Aboorva Devarajan <aboorvad@linux.ibm.com> | mm/page_alloc: make percpu_pagelist_high_fraction reads | `upstream-5.15.y/patches/eda99622e6f3.patch` |
| `f2795d1b9250` | Chen Ridong <chenridong@huawei.com> | cgroup: split cgroup_destroy_wq into 3 workqueues | `upstream-5.15.y/patches/f2795d1b9250.patch` |
| `f87c08060818` | Kiryl Shutsemau (Meta) | mm/huge_memory: unlock i_mmap_rwsem before releasing after-split folios | `upstream-5.15.y/patches/f87c08060818.patch` |
| `faa794dd2e17` | Dave Hansen <dave.hansen@linux.intel.com> | fuse: Move prefaulting out of hot write path | `upstream-5.15.y/patches/faa794dd2e17.patch` |
| `fb176750266a` | Gao Xiang <hsiangkao@linux.alibaba.com> | erofs: add file-backed mount support | `upstream-5.15.y/patches/fb176750266a.patch` |
| `ff48c71c26aa` | Shakeel Butt <shakeel.butt@linux.dev> | memcg: reduce memory for the lruvec and memcg stats | `upstream-5.15.y/patches/ff48c71c26aa.patch` |
| `013bf95a83ec` | Minchan Kim <minchan@kernel.org> | zram: add interface to specif backing device | `upstream-zram/patches/013bf95a83ec.patch` |
| `1d69a3f8ae77` | Minchan Kim <minchan@kernel.org> | zram: idle writeback fixes and cleanup | `upstream-zram/patches/1d69a3f8ae77.patch` |
| `330edc2bc059` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: rework writeback target selection strategy | `upstream-zram/patches/330edc2bc059.patch` |
| `37b72d525502` | _not recorded in the archived fragment_ |  | `upstream-zram/patches/37b72d525502.patch` |
| `3bf1c285dc40` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: clear trailing bytes of compressed writeback pages | `upstream-zram/patches/3bf1c285dc40.patch` |
| `3e8d8eb8d7f5` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: do not leak blk idx at the end of writeback | `upstream-zram/patches/3e8d8eb8d7f5.patch` |
| `424d0e5828ad` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: cond_resched() in writeback loop | `upstream-zram/patches/424d0e5828ad.patch` |
| `4c1d61389e8e` | Richard Chang <richardycc@google.com> | zram: introduce writeback_compressed device attribute | `upstream-zram/patches/4c1d61389e8e.patch` |
| `5e99893444a0` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: remove UNDER_WB and simplify writeback | `upstream-zram/patches/5e99893444a0.patch` |
| `74363ec674cb` | Kairui Song <kasong@tencent.com> | zram: fix uninitialized ZRAM not releasing backing device | `upstream-zram/patches/74363ec674cb.patch` |
| `894913e2d35c` | Longlong Xia <xialonglong@kylinos.cn> | zram: fix out-of-bounds access in writeback_store() | `upstream-zram/patches/894913e2d35c.patch` |
| `a939888ec38b` | Minchan Kim <minchan@kernel.org> | zram: support idle/huge page writeback | `upstream-zram/patches/a939888ec38b.patch` |
| `b0377ee80429` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: do not slot_free() written-back slots | `upstream-zram/patches/b0377ee80429.patch` |
| `b8d3ff7bb511` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: use zram_read_from_zspool() in writeback | `upstream-zram/patches/b8d3ff7bb511.patch` |
| `b967fa1ba72b` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: do not mark idle slots that cannot be idle | `upstream-zram/patches/b967fa1ba72b.patch` |
| `ba4c3698e696` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: rename writeback_compressed device attr | `upstream-zram/patches/ba4c3698e696.patch` |
| `bb416d18b850` | Minchan Kim <minchan@kernel.org> | zram: writeback throttle | `upstream-zram/patches/bb416d18b850.patch` |
| `be48c412f6eb` | Kairui Song <kasong@tencent.com> | zram: refuse to use zero sized block device as backing device | `upstream-zram/patches/be48c412f6eb.patch` |
| `bf62f69574b1` | Richard Chang <richardycc@google.com> | zram: fix use-after-free in zram_writeback_endio | `upstream-zram/patches/bf62f69574b1.patch` |
| `cf42d4cccf0d` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: modernize writeback interface | `upstream-zram/patches/cf42d4cccf0d.patch` |
| `d38fab605c66` | Richard Chang <richardycc@google.com> | zram: introduce compressed data writeback | `upstream-zram/patches/d38fab605c66.patch` |
| `e828cccb72ed` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: add writeback batch size device attr | `upstream-zram/patches/e828cccb72ed.patch` |
| `f405066a1f0d` | Sergey Senozhatsky <senozhatsky@chromium.org> | zram: introduce writeback bio batching | `upstream-zram/patches/f405066a1f0d.patch` |

Four of those patches (`0b8d16680d9f`, `37b72d525502`, `4cdc1bdf4094`,
`e8400a074123`) are stored as bare diffs with no commit metadata, so their author
is only recoverable from the upstream tree itself.

## Where the rest of the provenance lives

Not every graft has an archived `.patch`. The remaining sources, each with its
own upstream attribution:

- `research/upstream-5.15.y/patches/` and `research/upstream-zram/patches/` — the
  `5.15.y` and zram graft sources.
- `research/ack-6.1/` and `docs/survey_6_1_ack.md` — the android14-6.1 ACK line
  (`memory.reclaim`, PSI IRQ/kernfs polling, the PSI ONCPU state-mask sync,
  `cgroup.pressure`, the lazy-preemption and lock-wakeup hook families).
- `docs/survey_6_6_ack.md` — the android15-6.6 forms of zram recompression and
  zsmalloc chain-size sizing.
- `docs/survey_7_2_mm_reclaim.md` — the mainline v7.2 MM/reclaim line, including
  the two groups judged unportable.
- `research/readahead_618/`, `research/zram_cwb/`, `research/zram_wb_audit/` —
  mainline v6.18/v6.6/v7.x source snapshots.
- `research/popsicle_w_oss/` — the popsicle-w-oss branch extraction, including the
  Qualcomm WALT files quoted above.
- `research/zsmalloc_lockfree/`, `research/zram/`, `research/upstream-zram/`,
  `research/proactive_reclaim/` — further snapshots.

Each survey records the commit hashes, versions and the per-item verdict; the
author of a graft that has no archived patch can be read off the upstream commit
it names.
